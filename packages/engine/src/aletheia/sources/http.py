"""HTTP fetching with rate limiting, bounded retries, and mandatory provenance.

Every external read in the system goes through :class:`Fetcher`. It exists to
make four things impossible by construction:

* **Exceeding a publisher's rate limit.** The SEC documents ~10 requests/second
  and blocks abusers; a token bucket enforces a margin below it.
* **Retrying something that will never succeed.** A 403 is an entitlement
  problem. Retrying it burns quota and buries the real cause under timeouts, so
  permanent and transient failures are separated and only the latter is retried.
* **Leaking a credential into a log.** API keys travel in query strings on two of
  the three sources here. Every URL is redacted before it can reach a log line,
  an exception message, or the provenance table.
* **Storing a parsed value with no record of the bytes it came from.** Fetch and
  store are a single operation; there is no code path that parses a payload the
  store never saw.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from aletheia.core.clock import Clock
from aletheia.core.errors import PermanentSourceError, TransientSourceError
from aletheia.provenance.payloads import PayloadStore, StoredPayload

SECRET_QUERY_KEYS: Final = frozenset({"apikey", "api_key", "token", "key", "access_token"})
RETRYABLE_STATUS: Final = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_BACKOFF_SECONDS: Final = 30.0


def redact(url: str) -> str:
    """Replace credential-bearing query values with ``***``.

    Used on every URL before it is logged, stored, or embedded in an error. The
    redacted form is also what lands in ``raw_payloads.source_uri``, so the
    warehouse itself never holds a key.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    cleaned = [
        (key, "***" if key.lower() in SECRET_QUERY_KEYS else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    # safe="*" keeps the redaction marker legible: percent-encoding it to
    # %2A%2A%2A makes every provenance URI harder to read for no benefit.
    return urlunsplit(parts._replace(query=urlencode(cleaned, safe="*")))


class RateLimiter:
    """Token bucket, one per host.

    Deliberately simple and blocking: an ingest that runs a little slower is a
    non-event, whereas being blocked by the SEC costs the whole dataset.
    """

    __slots__ = ("_capacity", "_rate", "_sleep", "_tokens", "_updated_at")

    def __init__(self, rate_per_second: float, *, burst: float | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._rate = rate_per_second
        self._capacity = burst if burst is not None else rate_per_second
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._sleep = time.sleep

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated_at) * self._rate)
            self._updated_at = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            self._sleep((1.0 - self._tokens) / self._rate)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """A fetched payload plus where it was stored."""

    body: bytes
    status_code: int
    stored: StoredPayload

    @property
    def content_sha256(self) -> str:
        return self.stored.content_sha256


class Fetcher:
    """Rate-limited, retrying HTTP client that stores everything it fetches."""

    def __init__(
        self,
        *,
        payloads: PayloadStore,
        clock: Clock,
        user_agent: str,
        rate_per_second: float = 8.0,
        timeout_seconds: float = 60.0,
        max_attempts: int = 4,
        client: httpx.Client | None = None,
    ) -> None:
        self._payloads = payloads
        self._clock = clock
        self._limiter = RateLimiter(rate_per_second)
        self._max_attempts = max_attempts
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    def get(
        self,
        url: str,
        *,
        source: str,
        suffix: str = ".json",
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        """Fetch a URL and persist the response body under its own hash.

        Raises :class:`TransientSourceError` when every attempt failed for a
        retryable reason, and :class:`PermanentSourceError` immediately for one
        that will not improve with time.
        """
        safe_url = redact(url)
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            self._limiter.acquire()
            try:
                response = self._client.get(url, headers=dict(headers or {}))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = TransientSourceError(
                    f"{type(exc).__name__} on attempt {attempt}/{self._max_attempts}",
                    source=source,
                    uri=safe_url,
                )
                self._backoff(attempt)
                continue

            if response.status_code in RETRYABLE_STATUS:
                last_error = TransientSourceError(
                    f"HTTP {response.status_code} on attempt {attempt}/{self._max_attempts}",
                    source=source,
                    uri=safe_url,
                )
                self._backoff(attempt, retry_after=response.headers.get("Retry-After"))
                continue

            if response.status_code >= 400:
                raise PermanentSourceError(
                    f"HTTP {response.status_code}: {response.text[:200]}",
                    source=source,
                    uri=safe_url,
                )

            body = response.content
            stored = self._payloads.put(
                body,
                source_uri=safe_url,
                retrieved_at=self._clock.now(),
                suffix=suffix,
                http_status=response.status_code,
            )
            return FetchResult(body=body, status_code=response.status_code, stored=stored)

        assert last_error is not None  # noqa: S101 - loop always sets it before exhausting
        raise last_error

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _backoff(self, attempt: int, *, retry_after: str | None = None) -> None:
        """Deterministic exponential backoff, honouring Retry-After when sent.

        No random jitter: this is a single-client ingest, not a thundering herd,
        and a deterministic schedule keeps run timing reproducible.
        """
        if retry_after is not None:
            try:
                time.sleep(min(float(retry_after), _MAX_BACKOFF_SECONDS))
                return
            except ValueError:
                pass  # Retry-After may be an HTTP-date; fall through to backoff
        time.sleep(min(2.0**attempt * 0.5, _MAX_BACKOFF_SECONDS))
