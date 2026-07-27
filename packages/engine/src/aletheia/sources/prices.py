"""Price adapter, with FMP as the first implementation.

Prices sit behind a protocol for one reason: **this machine cannot obtain price
history for delisted companies.** Verified live 2026-07-27 — FMP returns HTTP 402
for SIVB and SGRP, Yahoo returns 404, Stooq is behind a JavaScript proof-of-work
wall, and the Bloomberg gateway answers 404. Fundamentals are survivorship-free
because the SEC never deletes a filing; prices are not.

That is a real limitation and it is handled in three places, none of which is
"ignore it":

1. **Here** — the source is a protocol, so a survivorship-free vendor drops in
   without touching a line of research code.
2. **:mod:`aletheia.store`** — the ``delistings`` table records which names left
   an exchange and when, so the gap is enumerable rather than invisible.
3. **The evidence card** — every backtest reports its survivorship exposure: how
   many universe members it could not price and what weight they carried.

Two contract facts that matter for correctness, both verified live:

* ``historical-price-eod/full`` returns **as-traded** prices. Across Apple's
  2020-08-31 four-for-one split the close moves 131.40 → 499-ish unadjusted; a
  return computed from this column across a split is wrong by the split ratio.
* ``historical-price-eod/dividend-adjusted`` returns ``adjOpen``/``adjHigh``/
  ``adjLow``/``adjClose``. It is rebased by the vendor on each pull, so adjusted
  *levels* from different pulls are not comparable — only returns computed
  within a single pull are meaningful. Both columns are stored so the trap is
  visible in the data rather than hidden in a note.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Protocol

from aletheia.core.config import Secret
from aletheia.core.errors import ContractViolation, PermanentSourceError
from aletheia.core.types import PriceBar
from aletheia.sources.base import ParseReport, parse_date
from aletheia.sources.http import Fetcher, redact


@dataclass(frozen=True, slots=True)
class FetchedRows:
    """Rows plus the provenance of the exact payload they came from."""

    rows: Sequence[Any]
    uri: str
    content_sha256: str
    retrieved_at: datetime


FMP_SOURCE: Final = "fmp"
FMP_BASE: Final = "https://financialmodelingprep.com/stable"


class PriceSource(Protocol):
    """What research needs from a price vendor, and nothing more."""

    @property
    def name(self) -> str: ...

    def daily_bars(
        self, symbol: str, *, start: date, end: date, run_id: str
    ) -> tuple[list[PriceBar], ParseReport]: ...


class DelistedCoverageError(PermanentSourceError):
    """The vendor will not serve this symbol under the current entitlement.

    Raised as its own type so an ingest can count exactly how much of the
    universe it is missing, rather than treating the gap as a generic failure.
    """


class FmpPriceSource:
    """Financial Modeling Prep daily bars, as-traded and dividend-adjusted."""

    def __init__(self, fetcher: Fetcher, *, api_key: Secret) -> None:
        self._fetch = fetcher
        self._api_key = api_key

    @property
    def name(self) -> str:
        return FMP_SOURCE

    def daily_bars(
        self, symbol: str, *, start: date, end: date, run_id: str
    ) -> tuple[list[PriceBar], ParseReport]:
        """As-traded bars joined to their adjusted closes, by date."""
        report = ParseReport()
        raw = self._get_rows("historical-price-eod/full", symbol, start=start, end=end)
        adjusted = self._get_rows(
            "historical-price-eod/dividend-adjusted", symbol, start=start, end=end
        )
        adjusted_by_date = {
            row["date"]: row for row in adjusted.rows if isinstance(row, dict) and "date" in row
        }

        # Provenance follows the as-traded payload: that is the column the bar's
        # OHLC values came from. Stamping it with the adjusted pull's hash would
        # point an auditor at bytes that do not contain the number in question.
        raw_rows = raw.rows
        uri = raw.uri
        bars: list[PriceBar] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                report.skip("bar is not an object")
                continue
            bar_date = parse_date(row.get("date"))
            if bar_date is None:
                report.skip("bar without a date")
                continue
            try:
                bars.append(
                    PriceBar(
                        symbol=symbol,
                        bar_date=bar_date,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        adj_close=_adjusted_close(adjusted_by_date.get(row.get("date", ""))),
                        volume=float(row.get("volume", 0.0)),
                        source=FMP_SOURCE,
                        source_uri=uri,
                        retrieved_at=raw.retrieved_at,
                        content_sha256=raw.content_sha256,
                        ingest_run_id=run_id,
                    )
                )
                report.parsed += 1
            except (KeyError, TypeError, ValueError):
                report.skip("bar missing an OHLC field")
        return bars, report

    def delisted_companies(self, *, run_id: str) -> tuple[list[dict[str, Any]], ParseReport]:
        """Names known to have left an exchange.

        Only page 0 is available on the current entitlement (verified: page 5
        returns HTTP 402), so this is a recent slice rather than full history.
        The ingest records that ceiling instead of implying the list is complete.
        """
        fetched = self._get_rows("delisted-companies", symbol=None, page=0)
        report = ParseReport()
        parsed: list[dict[str, Any]] = []
        for row in fetched.rows:
            if not isinstance(row, dict) or not row.get("symbol"):
                report.skip("delisting row without a symbol")
                continue
            parsed.append(row)
            report.parsed += 1
        return parsed, report

    # -------------------------------------------------------------- private --

    def _get_rows(
        self,
        endpoint: str,
        symbol: str | None,
        *,
        start: date | None = None,
        end: date | None = None,
        page: int | None = None,
    ) -> FetchedRows:
        params = [("apikey", self._api_key.reveal())]
        if symbol:
            params.insert(0, ("symbol", symbol))
        if start:
            params.append(("from", start.isoformat()))
        if end:
            params.append(("to", end.isoformat()))
        if page is not None:
            params.append(("page", str(page)))
        uri = f"{FMP_BASE}/{endpoint}?" + "&".join(f"{k}={v}" for k, v in params)
        safe_uri = redact(uri)

        try:
            result = self._fetch.get(uri, source=FMP_SOURCE)
        except PermanentSourceError as exc:
            # 402 means "your plan does not cover this symbol" — overwhelmingly
            # the delisted names. Distinguished so survivorship exposure can be
            # counted rather than mistaken for a transport failure.
            if "402" in str(exc):
                raise DelistedCoverageError(
                    f"{symbol or endpoint} is not covered by the current FMP entitlement",
                    source=FMP_SOURCE,
                    uri=safe_uri,
                ) from exc
            raise

        payload = json.loads(result.body)
        if isinstance(payload, dict):
            # FMP signals plan and quota problems with HTTP 200 and an object
            # body. Treating that as an empty result set would record "this
            # symbol has no history" for a symbol we simply were not served.
            message = payload.get("Error Message") or payload.get("message") or str(payload)[:200]
            raise ContractViolation(
                f"FMP returned an object, not rows: {message}", source=FMP_SOURCE, uri=safe_uri
            )
        if not isinstance(payload, list):
            raise ContractViolation(
                f"expected a list of rows, got {type(payload).__name__}",
                source=FMP_SOURCE,
                uri=safe_uri,
            )
        return FetchedRows(
            rows=payload,
            uri=safe_uri,
            content_sha256=result.content_sha256,
            retrieved_at=result.stored.retrieved_at,
        )


def _adjusted_close(row: Any) -> float | None:
    if not isinstance(row, dict):
        return None
    value = row.get("adjClose")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
