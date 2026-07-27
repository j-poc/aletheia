"""Ingest orchestration.

One rule governs every method here: **a run either records what it did or records
that it failed.** There is no path that writes rows and leaves no run, and none
that fails silently. An ingest whose failures are invisible produces a warehouse
that looks complete and is not — and nothing downstream can detect the
difference.

Idempotence is a property of the whole pipeline, not just the writes: re-running
any of these against unchanged upstream data leaves row counts identical, because
payloads are content-addressed and every insert conflicts on a real key.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from aletheia.core.clock import Clock
from aletheia.core.config import Settings
from aletheia.core.errors import AletheiaError, SourceError
from aletheia.core.hashing import canonical_hash
from aletheia.core.types import Cik
from aletheia.sources.base import ParseReport, parse_date
from aletheia.sources.edgar import EdgarClient
from aletheia.sources.fred import FredClient
from aletheia.sources.prices import DelistedCoverageError, FmpPriceSource
from aletheia.store.db import Warehouse
from aletheia.store.records import (
    DelistingRecord,
    DisseminatedFiling,
    EntityRecord,
    IdentifierRecord,
)

CONSECUTIVE_FAILURE_LIMIT: Final = 5
"""Stop a batch after this many failures in a row.

An exhausted API quota looks identical on every subsequent symbol, so retrying
226 of them costs twenty minutes and yields nothing but a list of identical
errors. Five in a row is well past coincidence and well short of aborting on a
transient blip. A per-name entitlement gap deliberately does not count -- that is
the survivorship measurement, not a fault."""


@dataclass(slots=True)
class IngestOutcome:
    """What one ingest run wrote, skipped, and could not reach."""

    run_id: str
    source: str
    rows_written: int = 0
    report: ParseReport = field(default_factory=ParseReport)
    unreachable: list[str] = field(default_factory=list)
    """Identifiers the source declined to serve — the survivorship hole, enumerated."""
    failed: list[str] = field(default_factory=list)
    aborted_after: str | None = None
    """Set when a run stopped early because the source stopped answering.

    A batch that grinds through 200 more names against an exhausted quota
    produces nothing but a long list of identical failures, and reports it as a
    completed run with poor coverage -- which reads like a data problem rather
    than an entitlement one."""

    def summary(self) -> str:
        parts = [f"{self.source}: {self.rows_written} rows written", self.report.summary()]
        if self.aborted_after:
            parts.append(f"ABORTED: {self.aborted_after}")
        if self.unreachable:
            parts.append(f"{len(self.unreachable)} unreachable (entitlement)")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return " | ".join(parts)


class Ingestor:
    """Drives sources into the warehouse, one recorded run at a time."""

    def __init__(
        self,
        *,
        settings: Settings,
        warehouse: Warehouse,
        clock: Clock,
        edgar: EdgarClient,
        fred: FredClient | None = None,
        prices: FmpPriceSource | None = None,
    ) -> None:
        self._settings = settings
        self._warehouse = warehouse
        self._clock = clock
        self._edgar = edgar
        self._fred = fred
        self._prices = prices

    # -------------------------------------------------------------- universe --

    def ingest_universe(self) -> IngestOutcome:
        """Current ticker↔CIK observations. Cheap, and the entry point to everything."""
        observed_at = self._clock.today()
        outcome = self._begin("edgar.universe", {"observed_at": observed_at})
        try:
            mappings, result = self._edgar.company_tickers(observed_at=observed_at)
            self._record_payload("edgar", result, outcome.run_id)
            records = [
                IdentifierRecord(
                    cik=mapping.cik,
                    ticker=mapping.ticker,
                    exchange=None,
                    observed_at=mapping.observed_at,
                    source_uri=result.stored.source_uri,
                    retrieved_at=result.stored.retrieved_at,
                    content_sha256=result.content_sha256,
                    ingest_run_id=outcome.run_id,
                )
                for mapping in mappings
            ]
            outcome.rows_written = self._warehouse.write_identifiers(records)
            outcome.report.parsed = len(records)
        except (AletheiaError, OSError) as exc:
            return self._fail(outcome, exc)
        return self._succeed(outcome)

    # ---------------------------------------------------------------- filings --

    def ingest_company(self, cik: Cik, *, with_facts: bool = True) -> IngestOutcome:
        """One company's filing index, metadata, and (optionally) every XBRL fact."""
        outcome = self._begin("edgar.company", {"cik": int(cik), "with_facts": with_facts})
        try:
            submissions, report = self._edgar.submissions(cik, run_id=outcome.run_id)
            outcome.report.merge(report)

            self._warehouse.write_entity(
                EntityRecord(
                    cik=submissions.cik,
                    name=submissions.name,
                    entity_type=submissions.entity_type,
                    sic=submissions.sic,
                    sic_description=submissions.sic_description,
                    fiscal_year_end=submissions.fiscal_year_end,
                    state_of_incorp=submissions.state_of_incorporation,
                    observed_at=self._clock.today(),
                    source_uri=submissions.source_uri,
                    retrieved_at=submissions.retrieved_at,
                    content_sha256=submissions.content_sha256,
                    ingest_run_id=outcome.run_id,
                )
            )
            outcome.rows_written += self._warehouse.write_filings(submissions.filings)

            # Exchange listings come from the submissions payload, which knows them;
            # the ticker file does not.
            exchange = submissions.exchanges[0] if submissions.exchanges else None
            outcome.rows_written += self._warehouse.write_identifiers(
                IdentifierRecord(
                    cik=submissions.cik,
                    ticker=ticker,
                    exchange=exchange,
                    observed_at=self._clock.today(),
                    source_uri=submissions.source_uri,
                    retrieved_at=submissions.retrieved_at,
                    content_sha256=submissions.content_sha256,
                    ingest_run_id=outcome.run_id,
                )
                for ticker in submissions.tickers
            )

            if with_facts:
                facts, fact_report = self._edgar.company_facts(cik, run_id=outcome.run_id)
                outcome.report.merge(fact_report)
                outcome.rows_written += self._warehouse.write_facts(facts)
        except (AletheiaError, OSError) as exc:
            return self._fail(outcome, exc)
        return self._succeed(outcome)

    def ingest_companies(self, ciks: Sequence[Cik], *, with_facts: bool = True) -> IngestOutcome:
        """Many companies. One company's failure never aborts the batch.

        A single 404 in a 500-name pull must not discard the other 499 — but the
        failure is recorded per name, so partial coverage is visible afterwards
        rather than inferred from a row count that looks plausible.
        """
        outcome = self._begin("edgar.companies", {"n": len(ciks), "with_facts": with_facts})
        for cik in ciks:
            single = self.ingest_company(cik, with_facts=with_facts)
            outcome.rows_written += single.rows_written
            outcome.report.merge(single.report)
            outcome.failed.extend(single.failed)
        return self._succeed(outcome)

    def ingest_daily_index(self, day: date) -> IngestOutcome:
        """Every filing disseminated on ``day`` — the forward-capture path.

        Everything captured here is perfect point-in-time by construction: it was
        recorded on the day it became public, so no reconstruction is involved.
        """
        outcome = self._begin("edgar.daily_index", {"day": day})
        try:
            entries, report = self._edgar.daily_index(day, run_id=outcome.run_id)
            outcome.report.merge(report)
            outcome.rows_written = self._warehouse.record_dissemination(
                DisseminatedFiling(
                    accn=entry.accn,
                    cik=entry.cik,
                    form=entry.form,
                    filed_at=entry.filed_at,
                    disseminated_at=day,
                    source_uri=entry.document_uri,
                    retrieved_at=self._clock.now(),
                    content_sha256="",
                    ingest_run_id=outcome.run_id,
                )
                for entry in entries
            )
            late = sum(1 for entry in entries if entry.filed_at < day)
            if late:
                outcome.report.skip("disseminated later than filed", late)
        except (AletheiaError, OSError) as exc:
            return self._fail(outcome, exc)
        return self._succeed(outcome)

    # ------------------------------------------------------------------ macro --

    def ingest_macro(self, series_ids: Iterable[str]) -> IngestOutcome:
        """Every vintage of each series — the revisions, not just the latest value."""
        ids = list(series_ids)
        outcome = self._begin("fred.vintages", {"series": sorted(ids)})
        if self._fred is None:
            return self._fail(outcome, RuntimeError("FRED client not configured"))
        for series_id in ids:
            try:
                observations, report = self._fred.all_vintages(series_id, run_id=outcome.run_id)
                outcome.report.merge(report)
                outcome.rows_written += self._warehouse.write_macro(observations)
            except (AletheiaError, OSError) as exc:
                outcome.failed.append(f"{series_id}: {exc}")
        return self._succeed(outcome)

    # ----------------------------------------------------------------- prices --

    def ingest_prices(self, symbols: Sequence[str], *, start: date, end: date) -> IngestOutcome:
        """Daily bars for the reachable universe.

        Symbols the vendor will not serve are collected in ``unreachable`` rather
        than raised. That list is the survivorship exposure: it is the set of
        names a backtest over this window silently would not have traded.
        """
        outcome = self._begin("fmp.prices", {"n": len(symbols), "start": start, "end": end})
        if self._prices is None:
            return self._fail(outcome, RuntimeError("price source not configured"))

        consecutive = 0
        for index, symbol in enumerate(symbols):
            try:
                bars, report = self._prices.daily_bars(
                    symbol, start=start, end=end, run_id=outcome.run_id
                )
                outcome.report.merge(report)
                outcome.rows_written += self._warehouse.write_prices(bars)
                consecutive = 0
            except DelistedCoverageError:
                # An entitlement gap for one name says nothing about the next, so
                # this deliberately does not trip the breaker. It is the
                # survivorship measurement, not a fault.
                outcome.unreachable.append(symbol)
                consecutive = 0
            except (SourceError, OSError) as exc:
                outcome.failed.append(f"{symbol}: {exc}")
                consecutive += 1
                if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                    outcome.aborted_after = (
                        f"{consecutive} consecutive failures ending at {symbol} "
                        f"({index + 1} of {len(symbols)} attempted); last error: {exc}"
                    )
                    break
        return self._succeed(outcome)

    def ingest_delistings(self) -> IngestOutcome:
        """Which names left an exchange, so the price gap can be quantified."""
        outcome = self._begin("fmp.delistings", {})
        if self._prices is None:
            return self._fail(outcome, RuntimeError("price source not configured"))
        try:
            rows, report = self._prices.delisted_companies(run_id=outcome.run_id)
            outcome.report.merge(report)
            observed_at = self._clock.today()
            outcome.rows_written = self._warehouse.write_delistings(
                DelistingRecord(
                    symbol=str(row["symbol"]).upper(),
                    exchange=row.get("exchange"),
                    company_name=row.get("companyName"),
                    ipo_date=parse_date(row.get("ipoDate")),
                    delisted_date=parse_date(row.get("delistedDate")),
                    observed_at=observed_at,
                    source="fmp",
                    source_uri="https://financialmodelingprep.com/stable/delisted-companies",
                    retrieved_at=self._clock.now(),
                    content_sha256="",
                    ingest_run_id=outcome.run_id,
                )
                for row in rows
            )
        except (AletheiaError, OSError) as exc:
            return self._fail(outcome, exc)
        return self._succeed(outcome)

    # ---------------------------------------------------------------- private --

    def _begin(self, source: str, params: dict[str, object]) -> IngestOutcome:
        stamp = self._clock.now().strftime("%Y%m%dT%H%M%S")
        run_id = f"{source}-{stamp}-{canonical_hash(params)[:8]}"
        self._warehouse.start_run(source=source, params=params, run_id=run_id)
        return IngestOutcome(run_id=run_id, source=source)

    def _succeed(self, outcome: IngestOutcome) -> IngestOutcome:
        self._warehouse.finish_run(
            outcome.run_id,
            status="ok",
            rows_written=outcome.rows_written,
            error="; ".join(outcome.failed[:5]) if outcome.failed else None,
        )
        return outcome

    def _fail(self, outcome: IngestOutcome, exc: Exception) -> IngestOutcome:
        outcome.failed.append(str(exc))
        self._warehouse.finish_run(
            outcome.run_id, status="failed", rows_written=outcome.rows_written, error=str(exc)[:500]
        )
        return outcome

    def _record_payload(self, source: str, result: object, run_id: str) -> None:
        stored = getattr(result, "stored", None)
        if stored is None:
            return
        self._warehouse.record_payload(
            content_sha256=stored.content_sha256,
            source=source,
            source_uri=stored.source_uri,
            retrieved_at=stored.retrieved_at,
            byte_len=stored.byte_len,
            stored_path=stored.path,
            ingest_run_id=run_id,
            http_status=stored.http_status,
        )
