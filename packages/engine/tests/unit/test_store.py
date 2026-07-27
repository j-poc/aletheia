"""Warehouse invariants.

The AAPL FY2008 diluted-EPS restatement (5.36 filed 2009-10-27, 6.78 filed
2010-01-25) is used as the fixture throughout because it is real, verifiable
against EDGAR, and exercises the one property that distinguishes this warehouse
from a vendor panel: the same period holding two different values, each with its
own knowledge date.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia.core import version as version_module
from aletheia.core.errors import IntegrityViolation, MigrationError
from aletheia.core.hashing import sha256_bytes
from aletheia.core.types import Accession, Cik
from aletheia.pit import as_of
from aletheia.store.db import Warehouse, _discover_migrations
from aletheia.store.records import DisseminatedFiling
from tests._factories import (
    FY2008_END,
    RETRIEVED_AT,
    RUN_ID,
    first_report,
    make_fact,
    make_filing,
    restatement,
)

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

    def test_shipped_migrations_are_byte_frozen(self) -> None:
        """Pin the checksum of every migration that has shipped.

        :meth:`Warehouse.migrate` already refuses to open a warehouse whose
        applied migrations no longer match the files on disk. That check only
        fires against a warehouse that *has* the old version applied -- so it
        protects users and misses the author, because every test here builds a
        fresh warehouse and re-applies whatever is currently on disk.

        The hole is not hypothetical: a one-character comment change to
        ``001_initial.sql`` shipped in a commit with the whole suite green, and
        made every pre-existing warehouse -- including this machine's 1.36M-filing
        one -- refuse to open. It surfaced only by chance, running an unrelated
        script.

        A migration that has shipped is immutable, comments included. Changing
        one means adding the next file, not editing this list. If this test fails,
        the fix is almost always ``git checkout`` on the migration, not a new hash
        pasted in here.
        """
        frozen = {
            1: "ab0e40174fd8763d6dc748713430cd7e0ede9c1b55424528d919b9dc3929dbac",
            2: "655ed8004b83affc2baaba96719d5e551d4f87f88314d73709473f951e9ceff2",
            3: "1858eaec15eb265eb7af1fc719a61b19694dd1f5ca4a612d3bedf1df492f99ea",
            4: "ce47cd88dc52afcc874832135eab61ed676cf820ec24378c0099f29ac816dacb",
            5: "e081c20dd28bbad075f84ccdd9874f2638ed593f71e87c0636d5f84336e679d5",
            6: "3f8640aa6b7e014627fcf402a8ba2ae935fa8eb8f152cc3c0092f12ec7e34347",
        }
        # Every migration on disk must be pinned, not merely the ones somebody
        # remembered to add. 005 shipped unpinned and this list stayed green,
        # which is precisely the hole the test was written to close -- the check
        # below only guards migrations that appear above it.
        on_disk_versions = {version for version, _name, _path in _discover_migrations()}
        assert on_disk_versions == frozen.keys(), (
            f"unpinned migration(s): {sorted(on_disk_versions - frozen.keys())}. "
            f"A migration that ships is immutable; add its checksum here."
        )
        on_disk = {
            version: sha256_bytes(path.read_text(encoding="utf-8").encode("utf-8"))
            for version, _name, path in _discover_migrations()
        }
        assert on_disk.keys() >= frozen.keys(), "a shipped migration file has disappeared"
        for version, checksum in frozen.items():
            assert on_disk[version] == checksum, (
                f"migration {version:03d} was edited after shipping. Existing warehouses "
                f"will refuse to open. Revert it and add a new migration instead."
            )


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


class TestTheRevisionViewOrdersByWhenAFilingBecamePublic:
    """Which report came first is decided once, in ``v_facts_pit``.

    Migration 001 wrote this view's window straight off ``facts`` with
    ``ORDER BY filed_at, accn``, before the filed-vs-disseminated distinction
    existed. That is a second, independent answer to the question ``report_seq``
    already answers, and the two differ on exactly one input: a filing that
    became public later than it was filed. 122 of the 3,168 filings captured
    from the dissemination feed are that shape -- one draft registration
    statement by 331 days.

    No such chain exists in the warehouse today: a query over all 13.4m facts
    returns zero rows where the two orderings disagree, because dissemination
    dates only arrive with forward capture and none of those filings carry XBRL
    facts yet. So this fixture is constructed rather than drawn from the record,
    and it is constructed to be the case that has not happened yet -- the first
    late-disseminated filing to revise a period, which would have been ordered
    wrongly here while looking perfectly correct one view over.

    The filer is deliberately a synthetic CIK. Inventing a dissemination date for
    a real accession number would make this look like a claim about a filing that
    was never late.
    """

    CIK = 1234567
    EARLIER_FILED_LATER_PUBLIC = "0001213900-26-000101"
    LATER_FILED_SOONER_PUBLIC = "0001213900-26-000202"

    @pytest.fixture
    def reordered(self, warehouse: Warehouse) -> Warehouse:
        """Filed January, public in June -- and beaten to the public record.

        The March filing is disseminated on time, so a reader's first sight of
        this period is 6.78. The January filing surfaces months later carrying
        5.36, which makes it the revision despite being written first.
        """
        warehouse.record_dissemination(
            [
                DisseminatedFiling(
                    accn=Accession(self.EARLIER_FILED_LATER_PUBLIC),
                    cik=Cik(self.CIK),
                    form="10-K",
                    filed_at=date(2026, 1, 12),
                    disseminated_at=date(2026, 6, 30),
                    source_uri="https://www.sec.gov/Archives/edgar/data/1234567/a.txt",
                    retrieved_at=RETRIEVED_AT,
                    content_sha256="",
                    ingest_run_id=RUN_ID,
                ),
                DisseminatedFiling(
                    accn=Accession(self.LATER_FILED_SOONER_PUBLIC),
                    cik=Cik(self.CIK),
                    form="10-K/A",
                    filed_at=date(2026, 3, 10),
                    disseminated_at=date(2026, 3, 10),
                    source_uri="https://www.sec.gov/Archives/edgar/data/1234567/b.txt",
                    retrieved_at=RETRIEVED_AT,
                    content_sha256="",
                    ingest_run_id=RUN_ID,
                ),
            ]
        )
        warehouse.write_facts(
            [
                make_fact(
                    value="5.36",
                    filed_at=date(2026, 1, 12),
                    accn=self.EARLIER_FILED_LATER_PUBLIC,
                    cik=self.CIK,
                ),
                make_fact(
                    value="6.78",
                    filed_at=date(2026, 3, 10),
                    accn=self.LATER_FILED_SOONER_PUBLIC,
                    cik=self.CIK,
                    form="10-K/A",
                ),
            ]
        )
        return warehouse

    def _chain(self, warehouse: Warehouse) -> list[tuple[object, ...]]:
        return warehouse.execute(
            """
            SELECT report_seq, value, prior_value, prior_knowledge_date, knowledge_date
              FROM v_fact_revisions
             WHERE cik = ?
             ORDER BY report_seq
            """,
            [self.CIK],
        ).fetchall()

    def test_the_first_report_is_the_first_one_anybody_could_read(
        self, reordered: Warehouse
    ) -> None:
        """Ordered by ``filed_at`` this row is 5.36 with no prior. It is neither."""
        first = self._chain(reordered)[0]
        assert first[0] == 1
        assert first[1] == Decimal("6.7800000000")
        assert first[2] is None, "the first publicly readable report revises nothing"
        assert first[4] == date(2026, 3, 10)

    def test_the_earlier_filing_is_the_revision(self, reordered: Warehouse) -> None:
        """Filed first, public second -- so it revises, and 6.78 is what it revised."""
        second = self._chain(reordered)[1]
        assert second[0] == 2
        assert second[1] == Decimal("5.3600000000")
        assert second[2] == Decimal("6.7800000000")
        assert second[3] == date(2026, 3, 10)
        assert second[4] == date(2026, 6, 30), "knowledge date is dissemination, not filing"

    def test_the_view_and_report_seq_cannot_disagree(self, reordered: Warehouse) -> None:
        """The structural point, asserted rather than assumed.

        ``v_fact_revisions`` is built on ``v_facts_pit``, so its row order is
        that view's ``report_seq`` by construction. Two hand-written windows
        would only be equal until someone corrected one of them.
        """
        disagreements = reordered.execute(
            """
            SELECT count(*)
              FROM v_fact_revisions AS r
              JOIN v_facts_pit AS p USING (cik, taxonomy, concept, unit,
                                           period_start, period_end, accn)
             WHERE r.report_seq IS DISTINCT FROM p.report_seq
            """
        ).fetchone()
        assert disagreements is not None
        assert disagreements[0] == 0

    def test_the_pit_layer_reports_the_revision_the_same_way(self, reordered: Warehouse) -> None:
        """``PitView.revisions()`` reads this view now, so it inherits the order.

        Standing on 2026-07-01, after both filings are public.
        """
        revisions = as_of(reordered, date(2026, 7, 1)).revisions(Cik(self.CIK))
        assert len(revisions) == 1
        assert revisions[0].prior_value == Decimal("6.7800000000")
        assert revisions[0].new_value == Decimal("5.3600000000")
        assert revisions[0].new_accn == Accession(self.EARLIER_FILED_LATER_PUBLIC)

    def test_a_revision_nobody_could_see_yet_is_not_reported(self, reordered: Warehouse) -> None:
        """The control on the test above.

        On 2026-04-01 the January filing exists on disk and has an earlier
        ``filed_at`` than anything else here, but is not yet public. Point-in-time
        filtering still happens in the query layer, so it must be absent -- and
        the period must read 6.78, the only value a reader could have held.
        """
        view = as_of(reordered, date(2026, 4, 1))
        assert view.revisions(Cik(self.CIK)) == []
        assert view.fact(
            Cik(self.CIK), "EarningsPerShareDiluted", period_end=FY2008_END
        ).value == Decimal("6.7800000000")


def _discoverable_repository() -> Path | None:
    """The repository containing the imported engine, found by walking up for ``.git``.

    Deliberately arithmetic-free. The obvious implementation reuses
    ``version.source_tree_root()``, which is a ``parents[N]`` expression -- and
    then a regression in that expression would make ``code_version`` return
    ``"unknown"`` *and* make the test below skip, so the one thing that would
    catch the regression would quietly decline to run. Walking upward shares no
    logic with what it is checking, so "no repository" now means what it says.
    """
    start = Path(version_module.__file__).resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_short_sha(root: Path) -> str | None:
    """The short sha git reports for ``root``, or None if git cannot answer.

    Shells out rather than calling ``code_version``: a test that checked the
    stamp against the function that produced it would pass whatever that
    function did.
    """
    executable = shutil.which("git")
    if executable is None:
        return None
    completed = subprocess.run(  # noqa: S603 - resolved absolute path, fixed argv, no shell
        [executable, "-C", str(root), "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


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

    def test_the_stamp_is_the_real_commit_when_the_source_is_in_a_repository(
        self, warehouse: Warehouse
    ) -> None:
        """`assert row[3]` above passes on the literal string "unknown".

        That is the correct value outside a repository, so the check above cannot
        be tightened -- but on its own it would let the stamp degrade to
        "unknown" everywhere without a single test noticing, and a run whose
        code version is unknown is a run nobody can reproduce.

        The expected sha is obtained from git directly rather than from
        ``code_version``, so this compares the stamp against an independent
        source instead of against itself. Skipped, with the reason stated, when
        there is genuinely no repository -- which is the case inside the mutation
        gate's sandbox copy, and is why the provenance stamp is one of the few
        things that harness cannot mutation-test. Note the order: the skip is
        decided by ``_discoverable_repository``, so a repository that exists but
        cannot be read is a failure rather than a silent skip.
        """
        repository = _discoverable_repository()
        if repository is None:
            pytest.skip("source tree is not in a git repository, so there is no sha to expect")
        expected = _git_short_sha(repository)
        assert expected is not None, f"{repository} is a repository but git would not name its HEAD"

        warehouse.finish_run("run-test-0001", status="ok", rows_written=2, bytes_fetched=1234)
        stamped = warehouse.execute("SELECT code_version FROM ingest_runs").fetchone()
        assert stamped is not None
        assert stamped[0] in (expected, f"{expected}-dirty"), (
            f"stamped {stamped[0]!r}, expected {expected!r} or {expected!r}-dirty"
        )

    def test_the_derived_root_is_the_actual_repository_root(self) -> None:
        """`code_version` walked to `<root>/packages`, not `<root>`.

        It worked: ``git -C`` searches upward, so any directory inside the
        working tree answers correctly, and the off-by-one was invisible. It is
        only invisible in one direction, though -- one level further up leaves
        the repository and every stamp silently becomes ``"unknown"``. A depth
        that is right by accident is a depth nobody will notice going wrong, so
        it is pinned against git's own answer here.
        """
        repository = _discoverable_repository()
        if repository is None:
            pytest.skip("source tree is not in a git repository, so there is no root to compare")
        assert version_module.source_tree_root() == repository

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


class TestReconcilingInterruptedRuns:
    """A killed process leaves its run row claiming the system is mid-ingest.

    Five such rows were sitting in the production warehouse: a crashed company
    ingest, a killed price run, and three throttled attempts. The row is a small
    lie that makes every later coverage question harder to answer.
    """

    def test_a_stale_running_row_becomes_interrupted(self, warehouse: Warehouse) -> None:
        warehouse.reconcile_interrupted_runs()  # the fixture opens one of its own
        warehouse.start_run(source="edgar.company", params={}, run_id="run-dead-0001")
        assert warehouse.reconcile_interrupted_runs() == 1
        status = warehouse.execute(
            "SELECT status FROM ingest_runs WHERE run_id = 'run-dead-0001'"
        ).fetchone()
        assert status is not None
        assert status[0] == "interrupted"

    def test_interrupted_is_neither_ok_nor_failed(self, warehouse: Warehouse) -> None:
        """What was written is real; what was not is unknown. Say so."""
        warehouse.reconcile_interrupted_runs()
        warehouse.start_run(source="edgar.company", params={}, run_id="run-dead-0002")
        warehouse.reconcile_interrupted_runs()
        rows = warehouse.execute(
            "SELECT count(*) FROM ingest_runs WHERE status IN ('ok', 'failed')"
        ).fetchone()
        assert rows is not None
        assert rows[0] == 0

    def test_a_second_pass_finds_nothing(self, warehouse: Warehouse) -> None:
        """Idempotent, so a caller can confirm the warehouse is clean."""
        warehouse.start_run(source="edgar.company", params={}, run_id="run-dead-0003")
        warehouse.reconcile_interrupted_runs()
        assert warehouse.reconcile_interrupted_runs() == 0


class TestPayloadLedgerCoverageIsReportedNotFaked:
    """The store keeps bytes keyed by hash and no metadata.

    So a warehouse written before payload indexing moved into the fetcher has the
    blobs on disk and no way to reconstruct source_uri or retrieved_at. The gap is
    counted and shown rather than filled with invented provenance, which could not
    be told apart from the real thing.
    """

    def test_the_gap_between_index_and_disk_is_visible(
        self, warehouse: Warehouse, tmp_path: Path
    ) -> None:
        raw = tmp_path / "raw"
        (raw / "ab" / "cd").mkdir(parents=True)
        for name in ("one.json.gz", "two.json.gz", "three.json.gz"):
            (raw / "ab" / "cd" / name).write_bytes(b"x")

        indexed, on_disk = warehouse.payload_ledger_coverage(raw)
        assert indexed == 0
        assert on_disk == 3

    def test_a_missing_raw_directory_reports_zero_rather_than_raising(
        self, warehouse: Warehouse, tmp_path: Path
    ) -> None:
        assert warehouse.payload_ledger_coverage(tmp_path / "absent") == (0, 0)
