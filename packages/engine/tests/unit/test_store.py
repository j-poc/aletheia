"""Warehouse invariants.

The AAPL FY2008 diluted-EPS restatement (5.36 filed 2009-10-27, 6.78 filed
2010-01-25) is used as the fixture throughout because it is real, verifiable
against EDGAR, and exercises the one property that distinguishes this warehouse
from a vendor panel: the same period holding two different values, each with its
own knowledge date.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia.core.errors import IntegrityViolation, MigrationError
from aletheia.store.db import Warehouse
from tests._factories import FY2008_END, first_report, make_fact, make_filing, restatement

FIRST_REPORT = first_report()
RESTATEMENT = restatement()


class TestMigrations:
    def test_creates_expected_tables(self, warehouse: Warehouse) -> None:
        tables = {
            row[0]
            for row in warehouse.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert {
            "facts",
            "filings",
            "entities",
            "entity_identifiers",
            "macro_observations",
            "prices",
            "delistings",
            "raw_payloads",
            "ingest_runs",
            "schema_migrations",
        } <= tables

    def test_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "w.duckdb"
        with Warehouse.open(path) as first:
            applied = first.migrate()
        assert applied == [], "second migrate() in the same process must apply nothing"
        with Warehouse.open(path) as second:
            assert second.migrate() == []

    def test_rejects_edited_migration(self, tmp_path: Path) -> None:
        """Editing a shipped migration must fail loudly, not diverge silently."""
        path = tmp_path / "w.duckdb"
        with Warehouse.open(path):
            pass
        with Warehouse.open(path, migrate=False) as store:
            store.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
            with pytest.raises(MigrationError, match="changed after being applied"):
                store.migrate()


class TestFactWrites:
    def test_round_trips_with_full_provenance(self, warehouse: Warehouse) -> None:
        assert warehouse.write_facts([FIRST_REPORT]) == 1
        row = warehouse.execute(
            "SELECT value, filed_at, accn, source_uri, content_sha256, ingest_run_id FROM facts"
        ).fetchone()
        assert row is not None
        value, filed_at, accn, source_uri, sha, run_id = row
        assert value == Decimal("5.3600000000")  # stored at the declared scale
        assert filed_at == date(2009, 10, 27)
        assert accn == "0001193125-09-214859"
        assert source_uri.startswith("https://data.sec.gov/")
        assert len(sha) == 64
        assert run_id == "run-test-0001"

    def test_reingest_is_idempotent(self, warehouse: Warehouse) -> None:
        assert warehouse.write_facts([FIRST_REPORT, RESTATEMENT]) == 2
        assert warehouse.write_facts([FIRST_REPORT, RESTATEMENT]) == 0
        assert warehouse.count("facts") == 2

    def test_duplicates_within_a_batch_collapse(self, warehouse: Warehouse) -> None:
        assert warehouse.write_facts([FIRST_REPORT, FIRST_REPORT, FIRST_REPORT]) == 1

    def test_restatement_is_kept_not_deduplicated(self, warehouse: Warehouse) -> None:
        """Same company, concept and period; different filing. Two rows, not one.

        A vendor panel keeps only the second. Keeping both is the entire premise.
        """
        warehouse.write_facts([FIRST_REPORT, RESTATEMENT])
        values = [
            row[0]
            for row in warehouse.execute(
                "SELECT value FROM facts WHERE period_end = ? ORDER BY filed_at", [FY2008_END]
            ).fetchall()
        ]
        assert values == [Decimal("5.3600000000"), Decimal("6.7800000000")]

    def test_refuses_to_truncate_precision(self, warehouse: Warehouse) -> None:
        """Silently rounding a reported number is a data-integrity failure."""
        too_precise = make_fact(
            value="1.23456789012345", filed_at=date(2010, 1, 25), accn="0001193125-10-012091"
        )
        with pytest.raises(IntegrityViolation, match="decimal places"):
            warehouse.write_facts([too_precise])

    def test_empty_batch_is_a_no_op(self, warehouse: Warehouse) -> None:
        assert warehouse.write_facts([]) == 0


class TestRevisionView:
    def test_surfaces_the_value_change(self, warehouse: Warehouse) -> None:
        warehouse.write_facts([FIRST_REPORT, RESTATEMENT])
        rows = warehouse.execute(
            """
            SELECT report_seq, value, prior_value, prior_filed_at
              FROM v_fact_revisions
             WHERE period_end = ?
             ORDER BY report_seq
            """,
            [FY2008_END],
        ).fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, Decimal("5.3600000000"), None, None)
        assert rows[1][1] == Decimal("6.7800000000")
        assert rows[1][2] == Decimal("5.3600000000")
        assert rows[1][3] == date(2009, 10, 27)

    def test_revision_magnitude_matches_the_real_filing(self, warehouse: Warehouse) -> None:
        """+26.5% — the retrospective iPhone revenue-recognition change."""
        warehouse.write_facts([FIRST_REPORT, RESTATEMENT])
        row = warehouse.execute(
            "SELECT value, prior_value FROM v_fact_revisions WHERE prior_value IS NOT NULL"
        ).fetchone()
        assert row is not None
        new_value, old_value = row
        change = (new_value - old_value) / old_value
        assert round(change, 4) == Decimal("0.2649")


class TestProvenance:
    def test_run_lifecycle_is_recorded(self, warehouse: Warehouse) -> None:
        warehouse.finish_run("run-test-0001", status="ok", rows_written=2, bytes_fetched=1234)
        row = warehouse.execute(
            "SELECT status, rows_written, bytes_fetched, code_version, finished_at FROM ingest_runs"
        ).fetchone()
        assert row is not None
        assert row[0] == "ok"
        assert row[1] == 2
        assert row[2] == 1234
        assert row[3]  # a code version was stamped
        assert row[4] is not None

    def test_failed_runs_are_kept(self, warehouse: Warehouse) -> None:
        warehouse.finish_run("run-test-0001", status="failed", error="HTTP 403 from FMP")
        row = warehouse.execute("SELECT status, error FROM ingest_runs").fetchone()
        assert row == ("failed", "HTTP 403 from FMP")

    def test_rejects_unknown_terminal_status(self, warehouse: Warehouse) -> None:
        with pytest.raises(ValueError, match="invalid terminal status"):
            warehouse.finish_run("run-test-0001", status="maybe")


class TestReadOnlySchemaGuard:
    def test_a_stale_warehouse_refuses_to_serve_reads(self, tmp_path: Path) -> None:
        """A read-only handle cannot migrate, so it must not pretend it is current.

        Answering queries against a schema the code no longer matches produces
        results that look like data and are not.
        """
        path = tmp_path / "stale.duckdb"
        with Warehouse.open(path) as store:
            store.execute("DELETE FROM schema_migrations WHERE version = 4")
        with pytest.raises(MigrationError, match="missing migration"):
            Warehouse.open(path, read_only=True)

    def test_a_current_warehouse_opens_read_only(self, tmp_path: Path) -> None:
        """Control: the guard must not block the ordinary case."""
        path = tmp_path / "current.duckdb"
        with Warehouse.open(path):
            pass
        with Warehouse.open(path, read_only=True) as store:
            assert store.count("facts") == 0


class TestLargeValuesFromRealFilers:
    """A filer scaling error killed a 350-company ingest. It must not again.

    Advanced Energy Industries (CIK 927003) tagged ``EntityPublicFloat`` as
    2,563,579,586,000,000,000 USD in its 2020 10-K -- $2.56 quintillion, off by
    about a factor of a billion from its real float. The number fits DECIMAL(38,10)
    with room to spare, but quantising it to ten decimal places needs 29
    significant digits and Decimal's default context carries 28, so the write
    raised ``InvalidOperation`` and took the whole run down.
    """

    REAL_BAD_FLOAT = "2563579586000000000"

    def test_the_filers_value_is_stored_exactly_as_filed(self, warehouse: Warehouse) -> None:
        """Stored, not rejected. The record is of what was filed, not what is plausible."""
        warehouse.write_facts(
            [
                make_fact(
                    value=self.REAL_BAD_FLOAT,
                    filed_at=date(2021, 2, 18),
                    accn="0001558370-21-001513",
                    concept="EntityPublicFloat",
                    unit="USD",
                    period_start=None,
                    period_end=date(2020, 6, 30),
                    cik=927003,
                )
            ]
        )
        stored = warehouse.execute(
            "SELECT value FROM facts WHERE cik = 927003 AND concept = 'EntityPublicFloat'"
        ).fetchone()
        assert stored is not None
        assert Decimal(stored[0]) == Decimal(self.REAL_BAD_FLOAT)

    def test_a_value_too_large_for_the_column_is_refused_with_a_clear_reason(
        self, warehouse: Warehouse
    ) -> None:
        """The genuinely-unstorable case, distinguished from the one above."""
        too_big = "1" + "0" * 28  # 10^28, the first value outside DECIMAL(38,10)
        with pytest.raises(IntegrityViolation, match="too large for DECIMAL"):
            warehouse.write_facts(
                [
                    make_fact(
                        value=too_big,
                        filed_at=date(2021, 2, 18),
                        accn="0001558370-21-001513",
                        concept="Assets",
                        unit="USD",
                        period_start=None,
                        period_end=date(2020, 6, 30),
                    )
                ]
            )

    def test_the_largest_representable_value_is_accepted(self, warehouse: Warehouse) -> None:
        """Boundary control: one below the bound must go in, or the check is too tight."""
        largest = "9" * 28
        assert (
            warehouse.write_facts(
                [
                    make_fact(
                        value=largest,
                        filed_at=date(2021, 2, 18),
                        accn="0001558370-21-001513",
                        concept="Assets",
                        unit="USD",
                        period_start=None,
                        period_end=date(2020, 6, 30),
                    )
                ]
            )
            == 1
        )

    def test_excess_decimal_places_are_still_refused(self, warehouse: Warehouse) -> None:
        """The wider context must not have quietly relaxed the precision guarantee."""
        with pytest.raises(IntegrityViolation, match="more than 10 decimal places"):
            warehouse.write_facts(
                [
                    make_fact(
                        value="1.00000000001",
                        filed_at=date(2021, 2, 18),
                        accn="0001558370-21-001513",
                        concept="Assets",
                        unit="USD",
                        period_start=None,
                        period_end=date(2020, 6, 30),
                    )
                ]
            )


class TestFilingsAreVisibleToResearch:
    """`v_company_filings_pit` inner-joins `filing_filers`.

    A filing written without a filer row is present in the table and absent from
    every query that asks what a company published -- which is what happened to
    every filing pulled from the submissions endpoint until write_filings started
    linking them.
    """

    def test_a_written_filing_is_reachable_through_the_pit_view(self, warehouse: Warehouse) -> None:
        warehouse.write_filings(
            [make_filing(accn="0000320193-09-214859", filed_at=date(2009, 10, 27))]
        )
        rows = warehouse.execute("SELECT accn FROM v_company_filings_pit").fetchall()
        assert [row[0] for row in rows] == ["0000320193-09-214859"]

    def test_the_filer_link_is_created_by_the_write(self, warehouse: Warehouse) -> None:
        warehouse.write_filings(
            [make_filing(accn="0000320193-09-214859", filed_at=date(2009, 10, 27))]
        )
        assert warehouse.count("filing_filers") == 1

    def test_backfill_repairs_an_unlinked_filing(self, warehouse: Warehouse) -> None:
        warehouse.write_filings(
            [make_filing(accn="0000320193-09-214859", filed_at=date(2009, 10, 27))]
        )
        warehouse.execute("DELETE FROM filing_filers")
        assert warehouse.execute("SELECT count(*) FROM v_company_filings_pit").fetchone()[0] == 0

        assert warehouse.backfill_filing_filers() == 1
        assert warehouse.execute("SELECT count(*) FROM v_company_filings_pit").fetchone()[0] == 1

    def test_backfill_is_idempotent(self, warehouse: Warehouse) -> None:
        """Running it on a healthy warehouse must insert nothing.

        That is how a caller confirms the defect is gone rather than assuming it.
        """
        warehouse.write_filings(
            [make_filing(accn="0000320193-09-214859", filed_at=date(2009, 10, 27))]
        )
        assert warehouse.backfill_filing_filers() == 0
