"""Restatement contamination: the pre-registered control, then the grain rules.

The first test is the one that matters. D20 registered, before any aggregate had
been run, that the general population query must return **25 restated of 121
periods** for AAPL ``EarningsPerShareDiluted`` -- a figure measured independently
on 2026-07-27 and quoted in the plan and the README. It is a known answer the
query is fully capable of getting wrong, which is what makes it a control rather
than a restatement of the same computation.

It runs against a committed fixture of the real filings rather than against
``data/warehouse.duckdb``, deliberately. A test that reads the developer's
warehouse would have to skip everywhere else, and D18 is about what happens when
a control is allowed to skip: the skip condition eventually shares logic with the
thing being checked, and the test declines to run against exactly the regression
it exists to catch. The fixture is 334 real fact rows and their 71 filings --
public SEC data, no credentials, no PII -- so this control runs on every machine,
every time, with nothing to opt out of.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aletheia.corpus.contamination import (
    THRESHOLDS,
    cross_grain_spread,
    measure_contamination,
)
from aletheia.store.db import Warehouse
from tests._factories import RUN_ID

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "aapl_eps_diluted.json"

AAPL_CIK = 320193
EPS = "EarningsPerShareDiluted"

EXPECTED_PERIODS = 121
"""Registered in D20 before the population aggregate was run."""
EXPECTED_RESTATED = 25


def _load_fixture(warehouse: Warehouse) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for table, columns_key, rows_key in (
        ("filings", "filing_columns", "filings"),
        ("facts", "fact_columns", "facts"),
    ):
        columns = payload[columns_key]
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(f'"{column}"' for column in columns)
        for row in payload[rows_key]:
            warehouse.execute(
                f"INSERT INTO {table} ({names}) VALUES ({placeholders})",  # noqa: S608
                row,
            )


@pytest.fixture
def aapl() -> Any:
    """A warehouse holding every XBRL report of AAPL diluted EPS, and nothing else."""
    with Warehouse.in_memory() as store:
        store.start_run(source="test", params={"fixture": "aapl_eps_diluted"}, run_id=RUN_ID)
        _load_fixture(store)
        yield store


class TestTheRegisteredControl:
    def test_the_aapl_slice_reproduces_the_independently_measured_answer(self, aapl: Any) -> None:
        """The gate from D20. If this fails, the population number is wrong."""
        result = measure_contamination(aapl, cik=AAPL_CIK, concept=EPS)

        assert result.facts == EXPECTED_PERIODS
        assert result.restated_facts == EXPECTED_RESTATED

    def test_the_control_would_notice_a_grain_that_collapsed(self, aapl: Any) -> None:
        """A guard on the guard.

        If the grain silently lost a column the fact count would collapse toward
        the number of distinct periods and the control above would still be
        checking *something*, just not the thing it claims to. Pinning the row
        count alongside makes that visible: 334 reports across 121 facts is a
        mean of ~2.8 republications each, and a collapsed grain moves both.
        """
        result = measure_contamination(aapl, cik=AAPL_CIK, concept=EPS)

        assert result.rows == 334
        assert result.restatable_facts + result.facts_reported_once == result.facts

    def test_the_slice_narrowing_actually_narrows(self, aapl: Any) -> None:
        """A predicate that matches nothing must return zeroes, not the whole corpus.

        Without this, a broken ``WHERE`` clause would make every sliced
        measurement silently report the population -- and the control above would
        pass for the wrong reason on any warehouse holding only AAPL.
        """
        empty = measure_contamination(aapl, cik=AAPL_CIK, concept="NoSuchConcept")

        assert empty.facts == 0
        assert empty.restated_facts == 0
        assert empty.fact_share == Decimal("0")


class TestTheGrain:
    def test_a_unit_change_is_two_facts_and_not_a_restatement(self, warehouse: Any) -> None:
        """The exclusion D20 registered, enforced by the grain rather than a filter."""
        _fact(warehouse, accn="a", value="100", unit="USD")
        _fact(warehouse, accn="b", value="100", unit="USD/shares")

        result = measure_contamination(warehouse)

        assert result.facts == 2
        assert result.restated_facts == 0

    def test_a_taxonomy_change_is_two_facts_and_not_a_restatement(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100", taxonomy="us-gaap")
        _fact(warehouse, accn="b", value="100", taxonomy="ifrs-full")

        result = measure_contamination(warehouse)

        assert result.facts == 2
        assert result.restated_facts == 0

    def test_a_value_change_under_one_grain_is_a_restatement(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="126")

        result = measure_contamination(warehouse)

        assert result.facts == 1
        assert result.restated_facts == 1
        assert result.fact_share == Decimal("1")

    def test_republishing_the_same_value_is_not_a_restatement(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="100")
        _fact(warehouse, accn="c", value="100")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 0
        assert result.restatable_facts == 1, "it was republished, it just did not change"

    def test_row_grain_and_fact_grain_differ_and_both_are_reported(self, warehouse: Any) -> None:
        """Why the headline is at fact grain.

        One fact restated across four reports contributes three flagged rows. At
        row grain that reads as 75% contaminated; at fact grain it is one fact.
        Counting rows would scale the headline by how often filers republish,
        which is not what the number claims to measure.
        """
        for i, value in enumerate(("100", "110", "120", "130")):
            _fact(warehouse, accn=f"a{i}", value=value)

        result = measure_contamination(warehouse)

        assert (result.facts, result.restated_facts) == (1, 1)
        assert (result.rows, result.restated_rows) == (4, 3)
        assert result.row_share == Decimal("0.75")
        assert result.fact_share == Decimal("1")


class TestDirection:
    """The only statistics that depend on *first* meaning first-published.

    Everything else in :class:`Contamination` is symmetric in the two values, so
    replacing ``arg_min(value, report_seq)`` with ``min(value)`` -- which reads
    almost identically and is wrong -- would leave the rest of this file green.
    These two tests are what makes the ordering load-bearing.
    """

    def test_a_downward_revision_is_counted_as_downward(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="80")

        result = measure_contamination(warehouse)

        assert (result.revised_down, result.revised_up) == (1, 0)

    def test_an_upward_revision_is_counted_as_upward(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="80")
        _fact(warehouse, accn="b", value="100")

        result = measure_contamination(warehouse)

        assert (result.revised_up, result.revised_down) == (1, 0)

    def test_aapl_fy2008_eps_was_revised_upward(self, aapl: Any) -> None:
        """5.36 -> 6.78, the case the whole project opens with, read off the
        general aggregate rather than a bespoke query."""
        result = measure_contamination(aapl, cik=AAPL_CIK, concept=EPS)

        assert result.revised_up + result.revised_down == result.restated_facts
        assert result.revised_up >= 1


class TestTheDistribution:
    def test_a_sign_flip_is_counted_separately(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="-100")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 1
        assert result.sign_flips == 1

    def test_an_ordinary_restatement_is_not_a_sign_flip(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="150")

        assert measure_contamination(warehouse).sign_flips == 0

    def test_thresholds_are_reported_side_by_side(self, warehouse: Any) -> None:
        """5.36 -> 6.78 is +26%, so it clears every registered cutoff."""
        _fact(warehouse, accn="a", value="5.36")
        _fact(warehouse, accn="b", value="6.78")

        result = measure_contamination(warehouse)

        assert set(result.threshold_counts) == set(THRESHOLDS)
        assert all(count == 1 for count in result.threshold_counts.values())

    def test_a_small_revision_clears_only_the_small_cutoff(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="102")

        result = measure_contamination(warehouse)

        assert result.threshold_counts["0.01"] == 1
        assert result.threshold_counts["0.05"] == 0
        assert result.threshold_counts["0.10"] == 0

    def test_the_relative_change_denominator_is_the_larger_magnitude(self, warehouse: Any) -> None:
        """0 -> 5 is a 100% change, not an infinite one.

        Dividing by the first value would make every restatement away from zero
        infinite, and any percentile of the distribution meaningless.
        """
        _fact(warehouse, accn="a", value="0")
        _fact(warehouse, accn="b", value="5")

        result = measure_contamination(warehouse)

        assert result.undefined_relative_change == 0
        assert result.quantiles["p50"] == Decimal("1")

    def test_zero_to_zero_has_no_relative_change_and_is_counted(self, warehouse: Any) -> None:
        """Both values zero cannot be a restatement, so it never reaches the
        denominator -- but a fact whose values are 0 and 0 is not restated at all,
        so the undefined bucket stays empty. The bucket exists for the case where
        distinct values are 0 and -0, which DuckDB treats as distinct decimals."""
        _fact(warehouse, accn="a", value="0")
        _fact(warehouse, accn="b", value="0")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 0
        assert result.undefined_relative_change == 0


class TestTheSignFlipExcludedPanel:
    """The post-hoc panel, added after the population run saturated at the cap.

    The relative-change measure cannot exceed 2 -- dividing by the larger
    magnitude caps a full sign flip at ``2x/x`` -- so a distribution whose upper
    quantiles read exactly 2.0 is reporting sign flips, which the module already
    says are usually presentation conventions rather than restatements.
    """

    def test_a_sign_flip_sits_exactly_at_the_cap(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="-100")

        result = measure_contamination(warehouse)

        assert result.quantiles["p50"] == Decimal("2")

    def test_the_panel_removes_the_sign_flip_from_the_distribution(self, warehouse: Any) -> None:
        """One flip and one ordinary 2% revision: the pre-registered median sits
        between them, the panel median is the ordinary revision alone."""
        _fact(warehouse, accn="a", value="100", cik=1)
        _fact(warehouse, accn="b", value="-100", cik=1)
        _fact(warehouse, accn="c", value="100", cik=2)
        _fact(warehouse, accn="d", value="102", cik=2)

        result = measure_contamination(warehouse)

        assert result.restated_facts == 2
        assert result.sign_flips == 1
        assert result.quantiles_excluding_sign_flips["p50"] == Decimal("0.01960784")
        assert result.threshold_counts["0.10"] == 1, "the flip clears every cutoff"
        assert result.threshold_counts_excluding_sign_flips["0.10"] == 0

    def test_a_panel_with_nothing_left_reads_zero_next_to_a_zero_count(
        self, warehouse: Any
    ) -> None:
        """Every restated fact a sign flip. The quantile has no value; reporting
        zero is only safe because the count beside it is zero too."""
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="-100")

        result = measure_contamination(warehouse)

        assert result.quantiles_excluding_sign_flips["p50"] == Decimal("0")
        assert result.threshold_counts_excluding_sign_flips["0.01"] == 0

    def test_the_panel_leaves_an_ordinary_restatement_alone(self, warehouse: Any) -> None:
        """A guard on the guard: the exclusion must not remove non-flips."""
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="150")

        result = measure_contamination(warehouse)

        assert result.quantiles_excluding_sign_flips == result.quantiles
        assert result.threshold_counts_excluding_sign_flips == result.threshold_counts, (
            "with no sign flips present the two panels are the same numbers"
        )

    def test_a_revision_through_zero_is_not_treated_as_a_flip(self, warehouse: Any) -> None:
        """``sign(0)`` is 0, so a value that lands on zero would compare unequal to
        its predecessor's sign. The non-zero guards are what stop that being
        counted as a presentation convention."""
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="0")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 1
        assert result.sign_flips == 0
        assert result.threshold_counts_excluding_sign_flips["0.10"] == 1


class TestCrossGrainSpread:
    def test_it_counts_what_the_grain_absorbs(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100", unit="USD")
        _fact(warehouse, accn="b", value="100", unit="USD/shares")
        _fact(warehouse, accn="c", value="7", concept="Other")

        spread = cross_grain_spread(warehouse)

        assert spread.triples == 2, "the unit pair collapses to one triple"
        assert spread.multi_unit == 1
        assert spread.multi_taxonomy == 0


def _fact(
    warehouse: Any,
    *,
    accn: str,
    value: str,
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    concept: str = "Revenues",
    cik: int = 1,
) -> None:
    """Insert one reported value. ``filed_at`` follows ``accn`` so ordering is explicit."""
    day = f"20{10 + len(accn) % 9:02d}-01-{1 + (ord(accn[0]) - ord('a')) % 28:02d}"
    warehouse.execute(
        """
        INSERT INTO facts (fact_key, cik, taxonomy, concept, unit, period_start, period_end,
                           "value", accn, form, filed_at, source_uri, retrieved_at,
                           content_sha256, ingest_run_id)
        VALUES (?, ?, ?, ?, ?, DATE '2008-01-01', DATE '2008-12-31', ?, ?, '10-K',
                CAST(? AS DATE), 'https://example.invalid',
                TIMESTAMPTZ '2026-01-01 00:00:00+00', ?, ?)
        """,
        [
            f"{cik}-{taxonomy}-{concept}-{unit}-{accn}",
            cik,
            taxonomy,
            concept,
            unit,
            Decimal(value),
            accn,
            day,
            "0" * 64,
            RUN_ID,
        ],
    )
