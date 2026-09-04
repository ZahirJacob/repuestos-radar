"""Relevance filter: score each listing title against the tracked query.

Adapters return hundreds of listings per query, many of which are not the
part we track — accessories (fundas, templados, cables), the same part for a
*different* model, or coincidental word overlap. This module scores every
listing and labels it MATCH / LOW_CONFIDENCE / REJECT. Nothing is discarded
here: the caller stores everything with its label so the dashboard can
show/hide and a human can review what the filter is unsure about.

The core is rule-based (token presence + a required-model-number rule +
an accessory blocklist) with stdlib fuzzy string similarity
(``difflib.SequenceMatcher``) to tolerate typos and minor variants. No third
-party fuzzy dependency is pulled in: the titles are short and the token-level
matching does the heavy lifting, so SequenceMatcher is more than adequate and
keeps the dependency surface minimal.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from repuestos_radar.models import KIND_PART, KIND_PHONE, TRACKED_KINDS
from repuestos_radar.schema import NormalizedListing

# --- tunable knobs ---------------------------------------------------------

# Two-tier blocklist. Both are EDITABLE module-level constants meant to be
# tuned as we see real data.
#
# HARD_REJECT: unambiguous accessories. A title carrying one of these (that
# the query did not itself ask for) is never the tracked spare part, so it is
# a REJECT even when the model words match.
HARD_REJECT: frozenset[str] = frozenset(
    {
        "funda",
        "fundas",
        "carcasa",
        "case",
        "templado",
        "templados",
        "protector",
        "protectores",
        "mica",
        "micas",
        "film",
        "hidrogel",
        "lamina",
        "laminas",
        "soporte",
        "holder",
        "adaptador",
        "cargador",
        "cargadores",
        "popsocket",
        "silicona",
        "manos",  # "manos libres"
        "auriculares",  # plural = headphones (accessory); singular is SOFT below
        "sim",  # "bandeja porta sim"
    }
)

# SOFT: ambiguous terms that ALSO name real repairable parts Activcelu sells
# (singular auricular = internal earpiece, parlante = internal loudspeaker,
# vidrio = camera/back glass, cable = internal flex, etc.). These must NEVER
# hard-reject: a title carrying one is capped at LOW_CONFIDENCE (never below)
# unless it is itself the part being queried.
SOFT: frozenset[str] = frozenset(
    {
        "vidrio",
        "auricular",
        "parlante",
        "cable",
        "cables",
        "memoria",
        "chip",
    }
)

# Part words. Only used for PHONE items: the query for a whole handset
# ("samsung s24 ultra") matches every part sold for that handset, and no query
# word can tell them apart — so a part word in the title is a hard REJECT for
# a phone item. Two EDITABLE tiers, both normalized, accent-free tokens
# (titles go through ``normalize``).
#
# PART_WORDS: unambiguous part names; reject anywhere in the title.
PART_WORDS: frozenset[str] = frozenset(
    {
        "modulo",
        "flex",
        "tapa",
        "placa",
        "lente",
        "buzzer",
        "boton",
        "botones",
        "bandeja",
        "porta",
        "conector",
        "touch",
        "tactil",
        "marco",
        "chasis",
        "vibrador",
        "cubre",
        "repuesto",
        "repuestos",
        "glass",
        "visor",
        "backcover",
        "teclado",
        "flexor",
    }
)

# PART_WORDS_LEADING: part names that are ALSO handset specs ("Samsung Galaxy
# S24 Ultra 256GB Cámara 200MP", "… Batería 5000mAh Carga Rápida"). These
# reject only when they appear BEFORE the first query token in the title: a
# part listing leads with the part name ("Bateria Samsung S24 Ultra", "CAMARA
# DELANTERA SAMSUNG S24"), a handset listing leads with the brand/model and
# lists its specs after it.
PART_WORDS_LEADING: frozenset[str] = frozenset(
    {
        "pantalla",
        "display",
        "bateria",
        "camara",
        "carga",
        "pin",
        "altavoz",
        "antena",
        "microfono",
        "memoria",
        # Seen leading real part titles on the first kind=phone ingest
        # (2026-09-03): "SENSOR PROXIMIDAD IPHONE 13", "PARLANTE AURICULAR
        # IPHONE 13", "IC CRISTAL IPHONE 13". After the model they are specs
        # ("Sensor Lidar", "Parlantes estéreo", "Cristal Ceramic Shield").
        "sensor",
        "proximidad",
        "parlante",
        "auricular",
        "cristal",
    }
)

# Fuzzy similarity (0..1) at/above which two tokens count as "the same word".
TOKEN_FUZZY_THRESHOLD = 0.82
# A middle band: at least this similar, but below the match threshold, is a
# hint rather than a match -> pushes toward LOW_CONFIDENCE, never MATCH.
TOKEN_FUZZY_LOW_BAND = 0.65
# Fraction of significant query tokens that must match for LOW_CONFIDENCE
# (below this, with no other signal, it's a REJECT).
MIN_TOKEN_COVERAGE = 0.5

# Very common words that carry no discriminating signal for these catalogs.
_STOPWORDS: frozenset[str] = frozenset({"de", "para", "con", "y", "el", "la", "original"})

# A model number: a letter+digits code (a34, g54, sm) or a bare number (11, 15).
_MODEL_NUMBER_RE = re.compile(r"^(?:[a-z]{1,3}\d{1,4}[a-z]?|\d{2,4})$")


class Relevance(Enum):
    """How well a listing matches the tracked query."""

    MATCH = "match"
    LOW_CONFIDENCE = "low_confidence"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RelevanceResult:
    """The label plus a numeric score (0..1) and a short human reason."""

    relevance: Relevance
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class ClassifiedListing:
    """A normalized listing paired with its relevance result."""

    listing: NormalizedListing
    result: RelevanceResult


def normalize(text: str) -> str:
    """Lowercase, strip accents, drop punctuation, and join model-number spacing.

    "Módulo A 34" and "modulo a-34" both normalize to "modulo a34".
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Punctuation -> space, collapse whitespace.
    spaced = re.sub(r"[^a-z0-9]+", " ", stripped).strip()
    # Join a SINGLE leading letter to an adjacent digit group: "a 34" -> "a34".
    # Restricted to one letter so stopwords aren't merged ("de 11" stays "de 11").
    joined = re.sub(r"\b([a-z])\s+(\d{1,4}[a-z]?)\b", r"\1\2", spaced)
    return re.sub(r"\s+", " ", joined).strip()


def _tokens(normalized: str) -> list[str]:
    return [t for t in normalized.split(" ") if t]


def _is_model_number(token: str) -> bool:
    return bool(_MODEL_NUMBER_RE.match(token))


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_token_similarity(query_token: str, title_tokens: list[str]) -> float:
    return max((_fuzzy(query_token, t) for t in title_tokens), default=0.0)


def _phone_part_words(query_set: set[str], title_tokens: list[str]) -> list[str]:
    """Part words that reject a title for a PHONE item, sorted; empty if none.

    PART_WORDS count anywhere in the title; PART_WORDS_LEADING only before
    the first (non-stopword) query token. The query's own tokens are exempt
    from both, as with HARD_REJECT/SOFT.
    """
    leading: list[str] = []
    for token in title_tokens:
        if token in query_set and token not in _STOPWORDS:
            break
        leading.append(token)
    hits = (set(title_tokens) & PART_WORDS) | (set(leading) & PART_WORDS_LEADING)
    return sorted(hits - query_set)


def classify(query: str, title: str, kind: str = KIND_PART) -> RelevanceResult:
    """Classify one title against one query. Pure function, no side effects.

    ``kind`` is the tracked item's kind (``TRACKED_KINDS``). For a phone
    item, a title carrying a part word (PART_WORDS anywhere, PART_WORDS_LEADING
    ahead of the model) is rejected before the regular rules run; for a part
    item the rules are exactly the pre-kind ones. An unknown kind raises
    ValueError.
    """
    if kind not in TRACKED_KINDS:
        raise ValueError(f"unknown tracked item kind: {kind!r} (expected {sorted(TRACKED_KINDS)})")
    norm_query = normalize(query)
    norm_title = normalize(title)
    if not norm_query or not norm_title:
        return RelevanceResult(Relevance.REJECT, 0.0, "empty query or title")

    query_tokens = _tokens(norm_query)
    title_tokens = _tokens(norm_title)
    title_set = set(title_tokens)
    query_set = set(query_tokens)

    # 0) Phone items: the query is the handset's name, so its words appear in
    # every spare part FOR that handset too. A part word in the title is the
    # only signal that separates "Samsung Galaxy S24 Ultra 256GB" from
    # "Bateria Samsung S24 Ultra" — hard reject on it.
    if kind == KIND_PHONE:
        part_hits = _phone_part_words(query_set, title_tokens)
        if part_hits:
            return RelevanceResult(
                Relevance.REJECT, 0.0, f"part word in a phone item: {part_hits[0]}"
            )

    # 1) Required model numbers: every model-number token in the query must be
    # present in the title. A different model is a REJECT even if all other
    # words match — the worst error for Activcelu is the wrong model's price.
    query_models = [t for t in query_tokens if _is_model_number(t)]
    for model in query_models:
        if model not in title_set:
            return RelevanceResult(Relevance.REJECT, 0.0, f"required model number missing: {model}")

    # 2) Blocklist tiers. A term the query itself asked for (e.g. query
    # "cargador" or "auricular") is intended, not junk, so exclude query
    # tokens from both tiers.
    part_tokens = [t for t in query_tokens if not _is_model_number(t) and t not in _STOPWORDS]
    # A phone query has no part words at all ("samsung", "ultra" name the
    # handset), so for a phone item nothing softens an accessory hit: a funda
    # for the phone is a REJECT, not LOW_CONFIDENCE.
    strong_part_match = kind == KIND_PART and any(t in title_set for t in part_tokens)

    # HARD_REJECT: an unambiguous accessory the user did not ask for and that
    # shares no part word -> REJECT.
    hard_hits = sorted(title_set & HARD_REJECT - query_set)
    if hard_hits and not strong_part_match:
        return RelevanceResult(Relevance.REJECT, 0.0, f"accessory term: {hard_hits[0]}")

    # SOFT: an ambiguous term that also names a real part -> never a hard
    # reject; the result is capped at LOW_CONFIDENCE further down.
    soft_hits = sorted(title_set & SOFT - query_set)

    # 3) Token coverage over the significant (non-stopword) query tokens.
    significant = [t for t in query_tokens if t not in _STOPWORDS]
    if not significant:
        significant = query_tokens

    exact_hits = 0
    fuzzy_hits = 0
    similarity_sum = 0.0
    for token in significant:
        if token in title_set:
            exact_hits += 1
            similarity_sum += 1.0
            continue
        best = _best_token_similarity(token, title_tokens)
        similarity_sum += best
        if best >= TOKEN_FUZZY_THRESHOLD:
            fuzzy_hits += 1
        # A best in [LOW_BAND, THRESHOLD) is a partial hint: it lifts the
        # score but does not count as a full token hit.
    matched = exact_hits + fuzzy_hits
    coverage = matched / len(significant)
    score = round(similarity_sum / len(significant), 4)

    # 4) A HARD_REJECT accessory that nonetheless shares a part word, or any
    # SOFT (ambiguous) term, caps the result at LOW_CONFIDENCE — never MATCH,
    # never a hard reject.
    if hard_hits:
        return RelevanceResult(
            Relevance.LOW_CONFIDENCE,
            score,
            f"accessory term {hard_hits[0]} but part word present",
        )
    if soft_hits:
        return RelevanceResult(
            Relevance.LOW_CONFIDENCE,
            score,
            f"ambiguous term (possible accessory): {soft_hits[0]}",
        )

    if coverage >= 1.0:
        return RelevanceResult(Relevance.MATCH, score, "all query tokens present")
    if coverage >= MIN_TOKEN_COVERAGE:
        return RelevanceResult(
            Relevance.LOW_CONFIDENCE,
            score,
            f"partial match: {matched}/{len(significant)} tokens (score {score})",
        )
    return RelevanceResult(
        Relevance.REJECT,
        score,
        f"too few query tokens match: {matched}/{len(significant)} (score {score})",
    )


def apply_relevance(
    query: str, listings: list[NormalizedListing], kind: str = KIND_PART
) -> list[ClassifiedListing]:
    """Classify a whole batch, keeping every listing with its label attached."""
    return [
        ClassifiedListing(listing, classify(query, listing.title, kind=kind))
        for listing in listings
    ]
