"""EDGAR parser contracts.

Fixtures are trimmed copies of real payloads, keeping the exact field names,
types and quirks the live API returns (empty strings for absent dates,
comma-packed 8-K item codes, 0/1 integers for booleans, parallel arrays). A
parser tested against invented data proves only that it agrees with our
imagination.

The live counterparts of these assertions are in ``test_live.py`` and run under
``-m live``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia.core.errors import ContractViolation
from aletheia.core.types import Cik
from aletheia.provenance.payloads import PayloadStore
from aletheia.sources.edgar import EdgarClient
from tests._fakes import RecordedFetcher

RUN_ID = "run-contract-0001"

# Real payload shape: AAPL FY2008 diluted EPS as first reported and as restated.
COMPANY_CONCEPT = {
    "cik": 320193,
    "taxonomy": "us-gaap",
    "tag": "EarningsPerShareDiluted",
    "units": {
        "USD/shares": [
            {
                "start": "2007-09-30",
                "end": "2008-09-27",
                "val": 5.36,
                "accn": "0001193125-09-214859",
                "fy": 2009,
                "fp": "FY",
                "form": "10-K",
                "filed": "2009-10-27",
            },
            {
                "start": "2007-09-30",
                "end": "2008-09-27",
                "val": 6.78,
                "accn": "0001193125-10-012091",
                "fy": 2010,
                "fp": "Q1",
                "form": "10-K/A",
                "filed": "2010-01-25",
            },
            {
                # Instantaneous facts have no "start" — the parser must not
                # invent one, and must not drop the row either.
                "end": "2008-09-27",
                "val": 4.03,
                "accn": "0001193125-09-214859",
                "fy": 2009,
                "fp": "FY",
                "form": "10-K",
                "filed": "2009-10-27",
            },
        ]
    },
}

SUBMISSIONS = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "entityType": "operating",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "fiscalYearEnd": "0926",
    "stateOfIncorporation": "CA",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-26-000011", "0001193125-09-214859"],
            "filingDate": ["2026-04-30", "2009-10-27"],
            "reportDate": ["2026-03-28", ""],
            "acceptanceDateTime": ["2026-04-30T20:30:41.000Z", "2009-10-27T16:37:18.000Z"],
            "form": ["8-K", "10-K"],
            "items": ["2.02,9.01", ""],
            "isXBRL": [0, 1],
            "primaryDocument": ["aapl-20260328.htm", "d10k.htm"],
        },
        "files": [],
    },
}

MASTER_IDX = b"""Description:           Daily Index of EDGAR Dissemination Feed
Last Data Received:    Jul 24, 2026

CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1750|AAR CORP|8-K|20260724|edgar/data/1750/0001104659-26-086424.txt
320193|Apple Inc.|4|20260724|edgar/data/320193/0001140361-26-025622.txt
"""


@pytest.fixture
def client(tmp_path: Path) -> EdgarClient:
    return EdgarClient(RecordedFetcher(PayloadStore(tmp_path / "raw")))


class TestCompanyConcept:
    def test_keeps_both_reports_of_the_same_period(self, client: EdgarClient) -> None:
        """The restatement and the original are two facts, not one overwritten."""
        _record(client, COMPANY_CONCEPT)
        facts, report = client.company_concept(
            Cik(320193), taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
        )
        eps = [f for f in facts if f.period_start == date(2007, 9, 30)]
        assert [(f.filed_at, f.value) for f in eps] == [
            (date(2009, 10, 27), Decimal("5.36")),
            (date(2010, 1, 25), Decimal("6.78")),
        ]
        assert report.total_skipped == 0

    def test_values_survive_as_exact_decimals(self, client: EdgarClient) -> None:
        """6.78 must not arrive as 6.779999999999999.

        json.loads is called with parse_float=Decimal for exactly this reason.
        """
        _record(client, COMPANY_CONCEPT)
        facts, _ = client.company_concept(
            Cik(320193), taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
        )
        restated = next(f for f in facts if f.filed_at == date(2010, 1, 25))
        assert restated.value == Decimal("6.78")
        assert str(restated.value) == "6.78"

    def test_instantaneous_facts_keep_a_null_period_start(self, client: EdgarClient) -> None:
        _record(client, COMPANY_CONCEPT)
        facts, _ = client.company_concept(
            Cik(320193), taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
        )
        instant = [f for f in facts if f.period_start is None]
        assert len(instant) == 1
        assert instant[0].value == Decimal("4.03")

    def test_carries_provenance_from_the_payload(self, client: EdgarClient) -> None:
        _record(client, COMPANY_CONCEPT)
        facts, _ = client.company_concept(
            Cik(320193), taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
        )
        fact = facts[0]
        assert len(fact.content_sha256) == 64
        assert fact.source_uri.startswith("https://data.sec.gov/api/xbrl/companyconcept/")
        assert fact.ingest_run_id == RUN_ID

    def test_a_fact_without_a_filing_date_is_skipped_and_counted(self, client: EdgarClient) -> None:
        """No knowledge date means no place in time. Dropping it silently is the bug."""
        payload = json.loads(json.dumps(COMPANY_CONCEPT))
        del payload["units"]["USD/shares"][0]["filed"]
        _record(client, payload)
        facts, report = client.company_concept(
            Cik(320193), taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
        )
        assert len(facts) == 2
        assert report.skipped["fact without a filing date"] == 1

    def test_missing_units_block_is_a_contract_violation(self, client: EdgarClient) -> None:
        _record(client, {"cik": 320193, "tag": "X"})
        with pytest.raises(ContractViolation, match="units"):
            client.company_concept(
                Cik(320193), taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
            )


class TestSubmissions:
    def test_parses_the_parallel_array_layout(self, client: EdgarClient) -> None:
        _record(client, SUBMISSIONS)
        entity, report = client.submissions(Cik(320193), run_id=RUN_ID)
        assert entity.name == "Apple Inc."
        assert entity.tickers == ("AAPL",)
        assert entity.exchanges == ("Nasdaq",)
        assert len(entity.filings) == 2
        assert report.parsed == 2

    def test_unpacks_comma_separated_8k_items(self, client: EdgarClient) -> None:
        _record(client, SUBMISSIONS)
        entity, _ = client.submissions(Cik(320193), run_id=RUN_ID)
        eight_k = next(f for f in entity.filings if f.form == "8-K")
        assert eight_k.items == ("2.02", "9.01")
        assert not eight_k.has_non_reliance_item

    def test_empty_report_date_becomes_none_not_an_epoch(self, client: EdgarClient) -> None:
        """EDGAR uses "" for absent. Coercing that to 1970-01-01 would be a lie."""
        _record(client, SUBMISSIONS)
        entity, _ = client.submissions(Cik(320193), run_id=RUN_ID)
        ten_k = next(f for f in entity.filings if f.form == "10-K")
        assert ten_k.period_of_report is None

    def test_parses_acceptance_instants_as_utc(self, client: EdgarClient) -> None:
        _record(client, SUBMISSIONS)
        entity, _ = client.submissions(Cik(320193), run_id=RUN_ID)
        eight_k = next(f for f in entity.filings if f.form == "8-K")
        assert eight_k.accepted_at == datetime(2026, 4, 30, 20, 30, 41, tzinfo=UTC)

    def test_ragged_parallel_arrays_are_a_contract_violation(self, client: EdgarClient) -> None:
        """A short column would silently misalign every field after it."""
        payload = json.loads(json.dumps(SUBMISSIONS))
        payload["filings"]["recent"]["form"] = ["8-K"]  # one entry, two accessions
        _record(client, payload)
        with pytest.raises(ContractViolation, match="parallel-array contract broke"):
            client.submissions(Cik(320193), run_id=RUN_ID)


class TestDailyIndex:
    def test_parses_the_pipe_delimited_master_index(self, client: EdgarClient) -> None:
        fetcher = _fetcher(client)
        fetcher.record(MASTER_IDX)
        entries, report = client.daily_index(date(2026, 7, 24), run_id=RUN_ID)
        assert report.parsed == 2
        assert [e.form for e in entries] == ["8-K", "4"]
        assert entries[0].accn.value == "0001104659-26-086424"
        assert entries[0].filed_at == date(2026, 7, 24)
        assert entries[0].document_uri.endswith("edgar/data/1750/0001104659-26-086424.txt")

    def test_company_names_containing_pipes_would_be_rejected_not_mangled(
        self, client: EdgarClient
    ) -> None:
        """Defensive: a row with the wrong field count is skipped, never split wrong."""
        fetcher = _fetcher(client)
        fetcher.record(MASTER_IDX + b"1|A|B|C|D|E\n")
        entries, _ = client.daily_index(date(2026, 7, 24), run_id=RUN_ID)
        assert len(entries) == 2

    def test_an_empty_index_is_a_contract_violation(self, client: EdgarClient) -> None:
        """Zero filings on a business day means the format or URL changed."""
        fetcher = _fetcher(client)
        fetcher.record(b"Description: nothing here\n")
        with pytest.raises(ContractViolation, match="zero entries"):
            client.daily_index(date(2026, 7, 24), run_id=RUN_ID)


def _fetcher(client: EdgarClient) -> RecordedFetcher:
    fetcher = client._fetch  # noqa: SLF001 - test reaching into its own fixture
    assert isinstance(fetcher, RecordedFetcher)
    return fetcher


def _record(client: EdgarClient, payload: object) -> None:
    _fetcher(client).record(json.dumps(payload).encode())
