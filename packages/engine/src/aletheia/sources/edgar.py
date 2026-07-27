"""SEC EDGAR client.

EDGAR is the spine of this system for one reason: **its facts are natively
bitemporal**. Every XBRL datapoint arrives carrying the accession number of the
filing that published it and the date that filing was made public. That is the
knowledge date, supplied by the publisher rather than reconstructed by us — and
it is what commercial fundamentals panels discard when they overwrite a period's
value with its latest restatement.

Filings for delisted companies are never removed, so the filer universe here is
survivorship-free by construction.

Contracts verified live 2026-07-27 against:
  * ``https://www.sec.gov/files/company_tickers.json``            (10,432 rows)
  * ``https://data.sec.gov/submissions/CIK0000320193.json``       (1,000 recent + chunks)
  * ``https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json``  (503 concepts)
  * ``https://www.sec.gov/Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx``

Access conditions (not suggestions): a descriptive User-Agent identifying the
requester, and roughly ten requests per second. Both are enforced upstream in
:mod:`aletheia.sources.http`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final

from aletheia.core.errors import ContractViolation
from aletheia.core.types import Accession, Cik, Fact, Filing
from aletheia.sources.base import (
    ParseReport,
    parse_compact_date,
    parse_date,
    parse_decimal,
    parse_instant,
    require_key,
    require_mapping,
    split_items,
)
from aletheia.sources.http import Fetcher, FetchResult

SOURCE: Final = "edgar"
DATA_BASE: Final = "https://data.sec.gov"
WWW_BASE: Final = "https://www.sec.gov"

# Taxonomies whose facts we ingest. `dei` carries entity-level facts (shares
# outstanding, document period) that the financial concepts alone do not.
IN_SCOPE_TAXONOMIES: Final = frozenset({"us-gaap", "ifrs-full", "dei"})


@dataclass(frozen=True, slots=True)
class TickerMapping:
    """A CIK↔ticker observation, valid as of the day it was retrieved.

    The SEC publishes only a *current* snapshot, so this is explicitly an
    observation rather than a historical mapping. Treating it as history is a
    quiet source of survivorship bias: a ticker that has been reassigned points
    at the wrong company for every date before the reassignment.
    """

    cik: Cik
    ticker: str
    title: str
    observed_at: date


class EdgarClient:
    """Typed reads of the EDGAR endpoints this system depends on."""

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetch = fetcher

    # ------------------------------------------------------------- universe --

    def company_tickers(self, *, observed_at: date) -> tuple[list[TickerMapping], FetchResult]:
        """Current ticker→CIK map. ~10,400 rows, one JSON object keyed by index."""
        uri = f"{WWW_BASE}/files/company_tickers.json"
        result = self._fetch.get(uri, source=SOURCE)
        payload = require_mapping(
            json.loads(result.body), source=SOURCE, uri=uri, what="company_tickers"
        )
        mappings: list[TickerMapping] = []
        for row in payload.values():
            if not isinstance(row, dict) or "cik_str" not in row or "ticker" not in row:
                raise ContractViolation(
                    "company_tickers row lost its cik_str/ticker shape", source=SOURCE, uri=uri
                )
            ticker = str(row["ticker"]).strip().upper()
            if not ticker:
                continue
            mappings.append(
                TickerMapping(
                    cik=Cik(int(row["cik_str"])),
                    ticker=ticker,
                    title=str(row.get("title", "")),
                    observed_at=observed_at,
                )
            )
        return mappings, result

    # ------------------------------------------------------------- filings ---

    def submissions(self, cik: Cik, *, run_id: str) -> tuple[EntitySubmissions, ParseReport]:
        """Full filing history for one company, following the older-filing chunks.

        ``filings.recent`` holds the newest 1,000; anything older lives in
        separate files listed under ``filings.files``. Reading only ``recent``
        silently truncates the history of any active filer — a company filing
        Form 4s weekly can exhaust 1,000 slots in under three years.
        """
        uri = f"{DATA_BASE}/submissions/CIK{cik.padded}.json"
        result = self._fetch.get(uri, source=SOURCE)
        payload = require_mapping(
            json.loads(result.body), source=SOURCE, uri=uri, what="submissions"
        )

        report = ParseReport()
        filings_block = require_mapping(
            require_key(payload, "filings", source=SOURCE, uri=uri),
            source=SOURCE,
            uri=uri,
            what="filings",
        )
        filings = list(
            _parse_filing_arrays(
                filings_block.get("recent", {}),
                cik=cik,
                uri=uri,
                sha=result.content_sha256,
                retrieved_at=result.stored.retrieved_at,
                run_id=run_id,
                report=report,
            )
        )

        for chunk in filings_block.get("files", []):
            name = chunk.get("name") if isinstance(chunk, dict) else None
            if not name:
                continue
            chunk_uri = f"{DATA_BASE}/submissions/{name}"
            chunk_result = self._fetch.get(chunk_uri, source=SOURCE)
            chunk_payload = require_mapping(
                json.loads(chunk_result.body),
                source=SOURCE,
                uri=chunk_uri,
                what="submissions chunk",
            )
            filings.extend(
                _parse_filing_arrays(
                    chunk_payload,
                    cik=cik,
                    uri=chunk_uri,
                    sha=chunk_result.content_sha256,
                    retrieved_at=chunk_result.stored.retrieved_at,
                    run_id=run_id,
                    report=report,
                )
            )

        entity = EntitySubmissions(
            cik=cik,
            name=str(payload.get("name", "")),
            entity_type=str(payload.get("entityType", "")) or None,
            sic=str(payload.get("sic", "")) or None,
            sic_description=str(payload.get("sicDescription", "")) or None,
            fiscal_year_end=str(payload.get("fiscalYearEnd", "")) or None,
            state_of_incorporation=str(payload.get("stateOfIncorporation", "")) or None,
            tickers=tuple(str(t).upper() for t in payload.get("tickers", []) if t),
            exchanges=tuple(str(e) for e in payload.get("exchanges", []) if e),
            filings=filings,
            source_uri=uri,
            content_sha256=result.content_sha256,
            retrieved_at=result.stored.retrieved_at,
        )
        return entity, report

    def daily_index(self, day: date, *, run_id: str) -> tuple[list[DailyIndexEntry], ParseReport]:
        """Every filing disseminated on one day.

        Uses the pipe-delimited ``master`` index rather than the fixed-width
        ``form`` index: column positions in the latter shift with long company
        names, and a parser that splits on whitespace corrupts those rows without
        raising.
        """
        quarter = (day.month - 1) // 3 + 1
        stamp = day.strftime("%Y%m%d")
        uri = f"{WWW_BASE}/Archives/edgar/daily-index/{day.year}/QTR{quarter}/master.{stamp}.idx"
        result = self._fetch.get(uri, source=SOURCE, suffix=".idx")

        report = ParseReport()
        entries: list[DailyIndexEntry] = []
        for line in result.body.decode("latin-1").splitlines():
            if line.count("|") != 4:
                continue  # header block and the dashed separator
            cik_text, company, form, filed_text, path = line.split("|")
            if not cik_text.strip().isdigit():
                continue  # the "CIK|Company Name|..." header row
            filed_at = parse_compact_date(filed_text)
            if filed_at is None:
                report.skip("unparseable filing date")
                continue
            accn_text = path.rsplit("/", 1)[-1].removesuffix(".txt")
            try:
                accn = Accession.parse(accn_text)
            except ValueError:
                report.skip("unparseable accession in path")
                continue
            entries.append(
                DailyIndexEntry(
                    cik=Cik(int(cik_text)),
                    company_name=company.strip(),
                    form=form.strip(),
                    filed_at=filed_at,
                    accn=accn,
                    document_uri=f"{WWW_BASE}/Archives/{path.strip()}",
                )
            )
            report.parsed += 1
        if not entries:
            raise ContractViolation(
                "daily index parsed to zero entries — the format or the URL changed",
                source=SOURCE,
                uri=uri,
            )
        return entries, report

    # --------------------------------------------------------------- facts ---

    def company_facts(self, cik: Cik, *, run_id: str) -> tuple[list[Fact], ParseReport]:
        """Every XBRL fact this company has ever filed, with its knowledge date."""
        uri = f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik.padded}.json"
        result = self._fetch.get(uri, source=SOURCE)
        # parse_float=Decimal: EPS values such as 6.78 must not pass through a
        # binary float on the way to a Decimal column.
        payload = require_mapping(
            json.loads(result.body, parse_float=Decimal),
            source=SOURCE,
            uri=uri,
            what="companyfacts",
        )
        facts_block = require_mapping(
            require_key(payload, "facts", source=SOURCE, uri=uri),
            source=SOURCE,
            uri=uri,
            what="facts",
        )

        report = ParseReport()
        facts = list(
            _parse_fact_tree(
                facts_block,
                cik=cik,
                uri=uri,
                sha=result.content_sha256,
                retrieved_at=result.stored.retrieved_at,
                run_id=run_id,
                report=report,
            )
        )
        return facts, report

    def company_concept(
        self, cik: Cik, *, taxonomy: str, concept: str, run_id: str
    ) -> tuple[list[Fact], ParseReport]:
        """One concept's full reporting history — every filing that stated it.

        This is the cheapest way to see a restatement: the same period appears
        once per filing that reported it, each with its own accession and date.
        """
        uri = f"{DATA_BASE}/api/xbrl/companyconcept/CIK{cik.padded}/{taxonomy}/{concept}.json"
        result = self._fetch.get(uri, source=SOURCE)
        payload = require_mapping(
            json.loads(result.body, parse_float=Decimal),
            source=SOURCE,
            uri=uri,
            what="companyconcept",
        )
        units = require_mapping(
            require_key(payload, "units", source=SOURCE, uri=uri),
            source=SOURCE,
            uri=uri,
            what="units",
        )
        report = ParseReport()
        facts = list(
            _parse_units(
                units,
                cik=cik,
                taxonomy=taxonomy,
                concept=concept,
                uri=uri,
                sha=result.content_sha256,
                retrieved_at=result.stored.retrieved_at,
                run_id=run_id,
                report=report,
            )
        )
        return facts, report


@dataclass(frozen=True, slots=True)
class EntitySubmissions:
    """Company metadata plus its complete filing index."""

    cik: Cik
    name: str
    entity_type: str | None
    sic: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_incorporation: str | None
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    filings: list[Filing]
    source_uri: str
    content_sha256: str
    retrieved_at: Any


@dataclass(frozen=True, slots=True)
class DailyIndexEntry:
    """One line of the daily dissemination feed."""

    cik: Cik
    company_name: str
    form: str
    filed_at: date
    accn: Accession
    document_uri: str


def _parse_filing_arrays(
    block: Any,
    *,
    cik: Cik,
    uri: str,
    sha: str,
    retrieved_at: Any,
    run_id: str,
    report: ParseReport,
) -> Iterator[Filing]:
    """EDGAR stores filings column-wise: parallel arrays, one index per filing."""
    if not isinstance(block, dict) or "accessionNumber" not in block:
        return
    accessions = block["accessionNumber"]
    count = len(accessions)
    for name, values in block.items():
        if isinstance(values, list) and len(values) != count:
            raise ContractViolation(
                f"submissions column {name!r} has {len(values)} entries but "
                f"accessionNumber has {count} — the parallel-array contract broke",
                source=SOURCE,
                uri=uri,
            )

    def column(name: str, index: int) -> Any:
        values = block.get(name)
        return values[index] if isinstance(values, list) else None

    for index in range(count):
        filed_at = parse_date(column("filingDate", index))
        if filed_at is None:
            report.skip("filing without a filing date")
            continue
        try:
            accn = Accession.parse(str(accessions[index]))
        except ValueError:
            report.skip("malformed accession number")
            continue
        yield Filing(
            accn=accn,
            cik=cik,
            form=str(column("form", index) or ""),
            filed_at=filed_at,
            accepted_at=parse_instant(column("acceptanceDateTime", index)),
            period_of_report=parse_date(column("reportDate", index)),
            primary_document=str(column("primaryDocument", index) or "") or None,
            items=split_items(column("items", index)),
            is_xbrl=bool(column("isXBRL", index)),
            source_uri=uri,
            retrieved_at=retrieved_at,
            content_sha256=sha,
            ingest_run_id=run_id,
        )
        report.parsed += 1


def _parse_fact_tree(
    facts_block: dict[str, Any],
    *,
    cik: Cik,
    uri: str,
    sha: str,
    retrieved_at: Any,
    run_id: str,
    report: ParseReport,
) -> Iterator[Fact]:
    for taxonomy, concepts in facts_block.items():
        if taxonomy not in IN_SCOPE_TAXONOMIES:
            report.skip(f"taxonomy out of scope: {taxonomy}")
            continue
        if not isinstance(concepts, dict):
            raise ContractViolation(
                f"taxonomy {taxonomy!r} is not an object of concepts", source=SOURCE, uri=uri
            )
        for concept, body in concepts.items():
            units = body.get("units") if isinstance(body, dict) else None
            if not isinstance(units, dict):
                report.skip("concept without units")
                continue
            yield from _parse_units(
                units,
                cik=cik,
                taxonomy=taxonomy,
                concept=concept,
                uri=uri,
                sha=sha,
                retrieved_at=retrieved_at,
                run_id=run_id,
                report=report,
            )


def _parse_units(
    units: dict[str, Any],
    *,
    cik: Cik,
    taxonomy: str,
    concept: str,
    uri: str,
    sha: str,
    retrieved_at: Any,
    run_id: str,
    report: ParseReport,
) -> Iterator[Fact]:
    for unit, entries in units.items():
        if not isinstance(entries, list):
            report.skip("unit without a list of facts")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                report.skip("fact entry is not an object")
                continue
            period_end = parse_date(entry.get("end"))
            if period_end is None:
                report.skip("fact without a period end")
                continue
            filed_at = parse_date(entry.get("filed"))
            if filed_at is None:
                # Without a knowledge date the fact is unusable here: it could
                # not be placed in time, and placing it wrongly is worse than
                # not having it.
                report.skip("fact without a filing date")
                continue
            value = parse_decimal(entry.get("val"))
            if value is None:
                report.skip("fact without a numeric value")
                continue
            accn_raw = entry.get("accn")
            if not accn_raw:
                report.skip("fact without an accession number")
                continue
            try:
                accn = Accession.parse(str(accn_raw))
            except ValueError:
                report.skip("malformed accession number")
                continue
            yield Fact(
                cik=cik,
                taxonomy=taxonomy,
                concept=concept,
                unit=unit,
                period_start=parse_date(entry.get("start")),
                period_end=period_end,
                value=value,
                accn=accn,
                form=str(entry.get("form") or ""),
                filed_at=filed_at,
                fy=int(entry["fy"]) if isinstance(entry.get("fy"), int) else None,
                fp=str(entry.get("fp")) if entry.get("fp") else None,
                frame=str(entry.get("frame")) if entry.get("frame") else None,
                source_uri=uri,
                retrieved_at=retrieved_at,
                content_sha256=sha,
                ingest_run_id=run_id,
            )
            report.parsed += 1
