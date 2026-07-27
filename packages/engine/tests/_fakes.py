"""Test doubles.

``RecordedFetcher`` replaces the network, not the storage: payloads still pass
through a real :class:`PayloadStore`, are still hashed, and still carry real
provenance. A double that also faked the store would let a parser bug in
provenance handling pass every test.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime

from aletheia.core.errors import PermanentSourceError
from aletheia.provenance.payloads import PayloadStore
from aletheia.sources.http import FetchResult

FIXED_INSTANT = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class RecordedFetcher:
    """Serves queued payloads in order, recording the URLs it was asked for."""

    def __init__(self, payloads: PayloadStore) -> None:
        self._payloads = payloads
        self._queue: deque[bytes | Exception] = deque()
        self.requested: list[str] = []

    def record(self, payload: bytes) -> None:
        """Queue one response body."""
        self._queue.append(payload)

    def record_error(self, error: Exception) -> None:
        """Queue a failure — used to exercise entitlement and contract paths."""
        self._queue.append(error)

    def get(
        self,
        url: str,
        *,
        source: str,
        suffix: str = ".json",
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        self.requested.append(url)
        if not self._queue:
            raise PermanentSourceError(
                f"RecordedFetcher has no queued response for {url}", source=source, uri=url
            )
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        stored = self._payloads.put(
            item, source_uri=url, retrieved_at=FIXED_INSTANT, suffix=suffix, http_status=200
        )
        return FetchResult(body=item, status_code=200, stored=stored)

    def close(self) -> None:
        return None
