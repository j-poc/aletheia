"""Deterministic test data builders.

The recurring fixture is the real Apple FY2008 diluted-EPS restatement: 5.36 filed
2009-10-27 (accn 0001193125-09-214859), restated to 6.78 filed 2010-01-25 (accn
0001193125-10-012091). Using a real, verifiable case rather than invented numbers
means a test that passes is evidence about EDGAR, not only about our code.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from aletheia.core.types import Accession, Cik, Fact, Filing

RETRIEVED_AT = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)
RUN_ID = "run-test-0001"

AAPL_CIK = 320193
FY2008_START = date(2007, 9, 30)
FY2008_END = date(2008, 9, 27)
FIRST_REPORT_ACCN = "0001193125-09-214859"
FIRST_REPORT_FILED = date(2009, 10, 27)
FIRST_REPORT_EPS = "5.36"
RESTATEMENT_ACCN = "0001193125-10-012091"
RESTATEMENT_FILED = date(2010, 1, 25)
RESTATEMENT_EPS = "6.78"


def make_fact(
    *,
    value: str,
    filed_at: date,
    accn: str,
    concept: str = "EarningsPerShareDiluted",
    unit: str = "USD/shares",
    period_start: date | None = FY2008_START,
    period_end: date = FY2008_END,
    cik: int = AAPL_CIK,
    form: str = "10-K",
    run_id: str = RUN_ID,
) -> Fact:
    return Fact(
        cik=Cik(cik),
        taxonomy="us-gaap",
        concept=concept,
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        value=Decimal(value),
        accn=Accession(accn),
        form=form,
        filed_at=filed_at,
        fy=2008,
        fp="FY",
        frame=None,
        source_uri=(
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{concept}.json"
        ),
        retrieved_at=RETRIEVED_AT,
        content_sha256="0" * 64,
        ingest_run_id=run_id,
    )


def make_filing(
    *,
    accn: str,
    filed_at: date,
    form: str = "10-K",
    cik: int = AAPL_CIK,
    items: tuple[str, ...] = (),
    period_of_report: date | None = FY2008_END,
    run_id: str = RUN_ID,
) -> Filing:
    return Filing(
        accn=Accession(accn),
        cik=Cik(cik),
        form=form,
        filed_at=filed_at,
        accepted_at=None,
        period_of_report=period_of_report,
        primary_document="aapl-10k.htm",
        items=items,
        is_xbrl=True,
        source_uri=f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
        retrieved_at=RETRIEVED_AT,
        content_sha256="1" * 64,
        ingest_run_id=run_id,
    )


def first_report() -> Fact:
    """AAPL FY2008 diluted EPS as originally published."""
    return make_fact(value=FIRST_REPORT_EPS, filed_at=FIRST_REPORT_FILED, accn=FIRST_REPORT_ACCN)


def restatement() -> Fact:
    """The same period, restated 90 days later. +26.5%."""
    return make_fact(value=RESTATEMENT_EPS, filed_at=RESTATEMENT_FILED, accn=RESTATEMENT_ACCN)
