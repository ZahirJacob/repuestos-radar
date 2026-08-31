"""Adapter contract and the courtesy rules every adapter must obey.

An adapter knows how to fetch and parse ONE source, and emits listings in the
normalized schema. Whatever the source looks like, every adapter follows the
project's scraping courtesy policy — these rules are part of the contract,
not optional behavior:

- **Honest user-agent**: every request identifies the project with
  ``USER_AGENT``. Never impersonate a browser, never rotate or swap UAs. If
  a site rejects the honest UA, the adapter raises :class:`AdapterError` and
  the source is reported unavailable; whether to deactivate the source is a
  human decision, not adapter logic.
- **robots.txt is honored**: check before fetching; a disallowed path is an
  :class:`AdapterError`, never a workaround.
- **Backoff and bounded retries**: transient failures are retried a small,
  fixed number of times with exponential backoff, then the source is given
  up for the run.
- **Courtesy delay**: at least one second between successive requests to a
  host within a run.
- **Skip, don't work around**: a source that blocks automated access is
  skipped. No exceptions.
"""

from typing import Protocol, runtime_checkable

from repuestos_radar.schema import NormalizedListing
from repuestos_radar.sources import Source

USER_AGENT = "repuestos-radar/0.1 (+https://github.com/ZahirJacob/repuestos-radar)"


class AdapterError(Exception):
    """A source could not be fetched this run (network, HTTP, robots, or parse failure).

    Carries the source ``slug`` so per-source failure reporting can attribute
    it without parsing the message.
    """

    def __init__(self, message: str, *, slug: str | None = None) -> None:
        super().__init__(message)
        self.slug = slug


@runtime_checkable
class Adapter(Protocol):
    """Structural contract for source adapters.

    A Protocol rather than an ABC: adapters share no implementation, only a
    shape, so structural typing keeps them plain classes with no inheritance
    coupling.
    """

    source: Source
    skipped: int
    """Products skipped as malformed during the most recent fetch."""

    def fetch(self, query: str) -> list[NormalizedListing]:
        """Return current listings for one search query, normalized."""
        ...

    def close(self) -> None:
        """Release the adapter's HTTP resources; the ingestion runner calls this."""
        ...

    def __enter__(self) -> "Adapter":
        """Adapters are context managers so a runner can close them via ExitStack."""
        ...

    def __exit__(self, *exc_info: object) -> None: ...
