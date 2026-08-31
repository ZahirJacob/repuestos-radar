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

from repuestos_radar.schema import NormalizedListing

# --- tunable knobs ---------------------------------------------------------

# Accessory / non-part terms. A title carrying one of these is almost never
# the spare part being tracked, even when the model words match. EDIT FREELY:
# this list is meant to be tuned as we see real data.
BLOCKLIST: frozenset[str] = frozenset(
    {
        "funda",
        "fundas",
        "carcasa",
        "case",
        "templado",
        "templados",
        "vidrio",  # "vidrio templado" protector; real glass parts say "modulo"/"visor"
        "protector",
        "protectores",
        "mica",
        "cable",
        "cables",
        "cargador",
        "cargadores",
        "auricular",
        "auriculares",
        "soporte",
        "holder",
        "adaptador",
        "manos",  # "manos libres"
        "parlante",
        "memoria",
        "sim",
        "chip",
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
    # Join a lone letter group to an adjacent digit group: "a 34" -> "a34".
    joined = re.sub(r"\b([a-z]{1,3})\s+(\d{1,4}[a-z]?)\b", r"\1\2", spaced)
    return re.sub(r"\s+", " ", joined).strip()


def _tokens(normalized: str) -> list[str]:
    return [t for t in normalized.split(" ") if t]


def _is_model_number(token: str) -> bool:
    return bool(_MODEL_NUMBER_RE.match(token))


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_token_similarity(query_token: str, title_tokens: list[str]) -> float:
    return max((_fuzzy(query_token, t) for t in title_tokens), default=0.0)


def classify(query: str, title: str) -> RelevanceResult:
    """Classify one title against one query. Pure function, no side effects."""
    norm_query = normalize(query)
    norm_title = normalize(title)
    if not norm_query or not norm_title:
        return RelevanceResult(Relevance.REJECT, 0.0, "empty query or title")

    query_tokens = _tokens(norm_query)
    title_tokens = _tokens(norm_title)
    title_set = set(title_tokens)

    # 1) Required model numbers: every model-number token in the query must be
    # present in the title. A different model is a REJECT even if all other
    # words match — the worst error for Activcelu is the wrong model's price.
    query_models = [t for t in query_tokens if _is_model_number(t)]
    for model in query_models:
        if model not in title_set:
            return RelevanceResult(Relevance.REJECT, 0.0, f"required model number missing: {model}")

    # 2) Blocklist: an accessory term the user did NOT ask for. A term that is
    # in the query itself (e.g. query "cargador") is intended, not junk.
    query_set = set(query_tokens)
    blocked = sorted(title_set & BLOCKLIST - query_set)
    part_tokens = [t for t in query_tokens if not _is_model_number(t) and t not in _STOPWORDS]
    strong_part_match = any(t in title_set for t in part_tokens)
    if blocked and not strong_part_match:
        return RelevanceResult(Relevance.REJECT, 0.0, f"blocklisted term: {blocked[0]}")

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
        elif best >= TOKEN_FUZZY_LOW_BAND:
            fuzzy_hits += 0  # counts toward score, not toward a full hit
    matched = exact_hits + fuzzy_hits
    coverage = matched / len(significant)
    score = round(similarity_sum / len(significant), 4)

    # 4) A blocklisted accessory that nonetheless shares a part word is
    # suspicious: cap it at LOW_CONFIDENCE, never MATCH.
    if blocked:
        return RelevanceResult(
            Relevance.LOW_CONFIDENCE,
            score,
            f"blocklisted term {blocked[0]} but part word present",
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


def apply_relevance(query: str, listings: list[NormalizedListing]) -> list[ClassifiedListing]:
    """Classify a whole batch, keeping every listing with its label attached."""
    return [ClassifiedListing(listing, classify(query, listing.title)) for listing in listings]
