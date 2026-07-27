"""Knowledge date resolution: filed vs. disseminated, and joint filings.

Both behaviours in this file were discovered by live contract tests rather than
designed up front, and both are point-in-time correctness issues:

* A draft registration statement filed 2025-08-27 first appeared in the public
  feed on 2026-07-24 — 331 days later. Treating ``filed_at`` as the knowledge
  date claims we could read it eleven months before anyone could.
* 740 of one day's 3,168 filings were joint submissions. Keeping only the first
  co-registrant answers "did this company file anything" with a wrong "no".
"""

from __future__ import annotations

from datetime import date

from aletheia.core.types import Accession, Cik
from aletheia.store.db import Warehouse
from aletheia.store.records import DisseminatedFiling
from tests._factories import RETRIEVED_AT, RUN_ID

CONFIDENTIAL_DRAFT = DisseminatedFiling(
    accn=Accession("0001213900-25-081198"),
    cik=Cik(1234567),
    form="DRSLTR",
    filed_at=date(2025, 8, 27),
    disseminated_at=date(2026, 7, 24),
    source_uri="https://www.sec.gov/Archives/edgar/data/1234567/0001213900-25-081198.txt",
    retrieved_at=RETRIEVED_AT,
    content_sha256="",
    ingest_run_id=RUN_ID,
)

SAME_DAY = DisseminatedFiling(
    accn=Accession("0001104659-26-086424"),
    cik=Cik(1750),
    form="8-K",
    filed_at=date(2026, 7, 24),
    disseminated_at=date(2026, 7, 24),
    source_uri="https://www.sec.gov/Archives/edgar/data/1750/0001104659-26-086424.txt",
    retrieved_at=RETRIEVED_AT,
    content_sha256="",
    ingest_run_id=RUN_ID,
)


class TestKnowledgeDate:
    def test_a_late_disseminated_filing_is_not_knowable_when_filed(
        self, warehouse: Warehouse
    ) -> None:
        warehouse.record_dissemination([CONFIDENTIAL_DRAFT])
        row = warehouse.execute(
            "SELECT knowledge_date, was_disseminated_late, dissemination_lag_days "
            "FROM v_filings_pit WHERE accn = ?",
            [CONFIDENTIAL_DRAFT.accn.value],
        ).fetchone()
        assert row is not None
        assert row[0] == date(2026, 7, 24), "knowledge date is when it became public"
        assert row[1] is True
        assert row[2] == 331

    def test_a_same_day_filing_is_knowable_when_filed(self, warehouse: Warehouse) -> None:
        """Control: the ordinary case must not be pushed later by this machinery."""
        warehouse.record_dissemination([SAME_DAY])
        row = warehouse.execute(
            "SELECT knowledge_date, was_disseminated_late FROM v_filings_pit WHERE accn = ?",
            [SAME_DAY.accn.value],
        ).fetchone()
        assert row == (date(2026, 7, 24), False)

    def test_filings_without_a_dissemination_record_fall_back_to_filed_at(
        self, warehouse: Warehouse
    ) -> None:
        """The submissions API does not report dissemination; the fallback is explicit."""
        from tests._factories import make_filing

        warehouse.write_filings(
            [make_filing(accn="0000320193-09-000001", filed_at=date(2009, 10, 27))]
        )
        row = warehouse.execute(
            "SELECT knowledge_date, disseminated_at, was_disseminated_late FROM v_filings_pit "
            "WHERE accn = ?",
            ["0000320193-09-000001"],
        ).fetchone()
        assert row == (date(2009, 10, 27), None, False)

    def test_the_earliest_dissemination_wins(self, warehouse: Warehouse) -> None:
        """Seeing a filing again later does not make it public later."""
        warehouse.record_dissemination([SAME_DAY])
        later = DisseminatedFiling(**{**_as_dict(SAME_DAY), "disseminated_at": date(2026, 7, 30)})
        warehouse.record_dissemination([later])
        row = warehouse.execute(
            "SELECT disseminated_at FROM filings WHERE accn = ?", [SAME_DAY.accn.value]
        ).fetchone()
        assert row == (date(2026, 7, 24),)


class TestJointFilings:
    def test_every_co_registrant_is_linked(self, warehouse: Warehouse) -> None:
        filers = [
            DisseminatedFiling(**{**_as_dict(SAME_DAY), "cik": Cik(cik)})
            for cik in (1750, 2000, 3000, 4000)
        ]
        warehouse.record_dissemination(filers)
        rows = warehouse.execute(
            "SELECT count(*) FROM filing_filers WHERE accn = ?", [SAME_DAY.accn.value]
        ).fetchone()
        assert rows == (4,)

    def test_the_filing_itself_is_stored_once(self, warehouse: Warehouse) -> None:
        filers = [
            DisseminatedFiling(**{**_as_dict(SAME_DAY), "cik": Cik(cik)}) for cik in (1750, 2000)
        ]
        warehouse.record_dissemination(filers)
        assert warehouse.count("filings") == 1

    def test_a_co_registrant_can_find_its_own_filing(self, warehouse: Warehouse) -> None:
        """The question this table exists to answer correctly."""
        filers = [
            DisseminatedFiling(**{**_as_dict(SAME_DAY), "cik": Cik(cik)}) for cik in (1750, 9999)
        ]
        warehouse.record_dissemination(filers)
        row = warehouse.execute(
            "SELECT accn, knowledge_date FROM v_company_filings_pit WHERE cik = 9999"
        ).fetchone()
        assert row == (SAME_DAY.accn.value, date(2026, 7, 24))

    def test_relinking_is_idempotent(self, warehouse: Warehouse) -> None:
        filers = [
            DisseminatedFiling(**{**_as_dict(SAME_DAY), "cik": Cik(cik)}) for cik in (1750, 2000)
        ]
        warehouse.record_dissemination(filers)
        warehouse.record_dissemination(filers)
        assert warehouse.count("filing_filers") == 2


def _as_dict(entry: DisseminatedFiling) -> dict[str, object]:
    return {field: getattr(entry, field) for field in DisseminatedFiling.__slots__}
