"""Restatement contamination: the pre-registered control, then the grain rules.

The first test is the one that matters. D20 registered, before any aggregate had
been run, that the general population query must return **25 restated of 121
periods** for AAPL ``EarningsPerShareDiluted`` -- a figure measured independently
on 2026-07-27 and registered in D20 before this code existed. It is a known answer the
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
    MIN_CONDITIONAL_DENOMINATOR,
    PERIOD_BANDS,
    SURVIVAL_CUTOFFS,
    THRESHOLDS,
    contamination_by_survival,
    contamination_by_unit_class,
    cross_grain_spread,
    measure_contamination,
    survival_by_period_band,
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

    def test_the_three_directions_account_for_every_restated_fact(self, warehouse: Any) -> None:
        """Up, down and back-to-where-it-started must sum to the restated count.

        The third arm is easy to omit -- ``>`` and ``<`` leave equality
        uncounted -- and omitting it is silent: the two figures simply fail to
        reconcile with the headline, which is what happened on the first
        population run (156,058 + 191,687 against 357,842 restated).

        A fact that changes and changes back is genuinely restated. Its report
        sequence holds two distinct values, so a backtest reading the middle
        vintage saw 110 on a date the archive now shows as 100.
        """
        for accn, value in (("a", "100"), ("b", "110"), ("c", "100")):
            _fact(warehouse, accn=accn, value=value)
        _fact(warehouse, accn="d", value="50", concept="Up")
        _fact(warehouse, accn="e", value="70", concept="Up")
        _fact(warehouse, accn="f", value="90", concept="Down")
        _fact(warehouse, accn="g", value="20", concept="Down")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 3
        assert (result.revised_up, result.revised_down) == (1, 1)
        assert result.returned_to_first_value == 1
        total = result.revised_up + result.revised_down + result.returned_to_first_value
        assert total == result.restated_facts

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

    def test_zero_republished_as_zero_is_not_restated_at_all(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="0")
        _fact(warehouse, accn="b", value="0")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 0
        assert result.undefined_relative_change == 0

    def test_a_value_that_leaves_zero_and_returns_is_the_undefined_case(
        self, warehouse: Any
    ) -> None:
        """What the undefined bucket is actually for.

        A fact reported 0, then 5, then 0 has two distinct values, so it *is*
        restated -- a backtest reading the middle vintage saw 5 -- but its first
        and latest are both zero, so relative change has no denominator. It is
        also the case that distinguishes ``exactly one endpoint is zero`` from
        ``either endpoint is zero``: this fact must not be counted as a
        zero-endpoint transition, because nothing appeared or vanished between
        the endpoints.

        (An earlier version of this test claimed the bucket existed for ``0``
        versus ``-0``. It does not: DuckDB normalises negative zero in DECIMAL,
        so those are one distinct value. Checked directly rather than reasoned
        about.)
        """
        _fact(warehouse, accn="a", value="0")
        _fact(warehouse, accn="b", value="5")
        _fact(warehouse, accn="c", value="0")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 1
        assert result.undefined_relative_change == 1
        assert result.restated_from_or_to_zero == 0
        assert (result.revised_up, result.revised_down) == (0, 0), (
            "it ended where it started, so it is neither"
        )


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

    def test_a_fact_that_appears_from_zero_is_counted_as_such(self, warehouse: Any) -> None:
        """The spike at exactly 1.0, counted rather than inferred from its position.

        ``|x - 0| / max(|x|, 0)`` is 1 for every non-zero x, so every zero-endpoint
        restatement lands on the same point of the scale. That is a fact appearing
        or vanishing, not a fact being revised, and the two read very differently.
        """
        _fact(warehouse, accn="a", value="0")
        _fact(warehouse, accn="b", value="5")

        result = measure_contamination(warehouse)

        assert result.restated_from_or_to_zero == 1
        assert result.quantiles["p50"] == Decimal("1")

    def test_an_ordinary_revision_is_not_a_zero_endpoint(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="150")

        assert measure_contamination(warehouse).restated_from_or_to_zero == 0

    def test_both_endpoints_zero_is_neither_restated_nor_counted(self, warehouse: Any) -> None:
        """``exactly one`` is load-bearing: a naive ``OR`` would count this."""
        _fact(warehouse, accn="a", value="0")
        _fact(warehouse, accn="b", value="0")

        result = measure_contamination(warehouse)

        assert result.restated_facts == 0
        assert result.restated_from_or_to_zero == 0

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


class TestTheSplitDecomposition:
    """Which restatements a stock split could have caused.

    Post-hoc, and written because the AAPL control's quantiles turned out to be
    ``3/4`` and ``6/7`` exactly -- the arithmetic of Apple's 4:1 and 7:1 splits.
    A split is a real point-in-time restatement and not an accounting revision,
    so the two need separating before either is quoted.
    """

    def test_a_per_share_unit_is_classified_as_per_share(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="4.00", unit="USD/shares")

        panel = {row.unit_class: row for row in contamination_by_unit_class(warehouse)}

        assert set(panel) == {"per-share"}
        assert panel["per-share"].facts == 1

    def test_the_underscore_suffixed_per_share_unit_is_not_missed(self, warehouse: Any) -> None:
        """The regression for a predicate that was a guess.

        The first draft matched ``LIKE '%/shares'``, anchored at the end. The
        corpus also carries ``USD/shares_unit`` -- 103 facts, found by
        enumerating the 518 distinct units rather than by reasoning about what
        XBRL ought to contain -- and those were silently filed under ``other``,
        which is the one bucket the decomposition claims splits cannot touch.
        """
        _fact(warehouse, accn="a", value="4.00", unit="USD/shares_unit")

        panel = {row.unit_class: row for row in contamination_by_unit_class(warehouse)}

        assert set(panel) == {"per-share"}, "an anchored pattern files this under 'other'"

    def test_an_inverse_unit_is_not_a_per_share_quantity(self, warehouse: Any) -> None:
        """``shares/USD`` is shares *per dollar* -- the reciprocal, not a per-share value.

        The widened pattern has to stay narrow enough to exclude it, or the
        decomposition drifts toward classifying anything mentioning shares as
        split-exposed.
        """
        _fact(warehouse, accn="a", value="4.00", unit="shares/USD")

        panel = {row.unit_class: row for row in contamination_by_unit_class(warehouse)}

        assert set(panel) == {"other"}

    def test_share_counts_are_their_own_class(self, warehouse: Any) -> None:
        """Separate from per-share because a split moves them the opposite way."""
        _fact(warehouse, accn="a", value="1000", unit="shares")
        _fact(warehouse, accn="b", value="500", unit="USD")

        panel = {row.unit_class: row for row in contamination_by_unit_class(warehouse)}

        assert panel["share count"].facts == 1
        assert panel["other"].facts == 1

    def test_a_split_shaped_restatement_lands_on_the_split_ratio(self, warehouse: Any) -> None:
        """A 4:1 split puts every affected fact at exactly 0.75.

        ``|1 - 4| / max(4, 1)`` is ``3/4``. That is why the split is visible in
        the magnitude before it is visible in the rate, and why the median is
        reported per class.
        """
        _fact(warehouse, accn="a", value="4.00", unit="USD/shares")
        _fact(warehouse, accn="b", value="1.00", unit="USD/shares")

        panel = {row.unit_class: row for row in contamination_by_unit_class(warehouse)}

        assert panel["per-share"].restated_facts == 1
        assert panel["per-share"].median_relative_change == Decimal("0.75")
        assert panel["per-share"].downward_share == Decimal("1")

    def test_the_downward_share_excludes_facts_that_returned(self, warehouse: Any) -> None:
        """A fact that changed and changed back has no direction.

        Counting it in the denominator would drag every class's downward share
        toward one half, which is exactly the value the memo reads as "no skew".
        """
        for accn, value in (("a", "100"), ("b", "110"), ("c", "100")):
            _fact(warehouse, accn=accn, value=value)
        _fact(warehouse, accn="d", value="50", concept="Up")
        _fact(warehouse, accn="e", value="70", concept="Up")
        _fact(warehouse, accn="f", value="90", concept="Down")
        _fact(warehouse, accn="g", value="20", concept="Down")

        panel = {row.unit_class: row for row in contamination_by_unit_class(warehouse)}

        assert panel["other"].returned_to_first_value == 1
        assert panel["other"].downward_share == Decimal("0.5"), "1 of 2 directional, not 1 of 3"

    def test_the_classes_come_back_in_a_fixed_order(self, warehouse: Any) -> None:
        """Ordered by meaning, not by count.

        ``ORDER BY facts DESC`` would reorder the panel on a corpus where one
        class overtook another, which makes two runs of the same study look like
        a change in the result.

        The class sizes here are deliberately unequal, and in the exact reverse
        of the required order: ``other`` largest, ``per-share`` smallest. An
        earlier version used one fact per class, which made all three counts tie
        -- so ``ORDER BY facts DESC`` had no defined order to produce and DuckDB
        was free to return the right one by luck. The mutant for this rule
        survived intermittently as a result, passing on some runs and failing on
        others, which is worse than a missing test: a gate whose own verdict is
        not reproducible cannot be evidence for anything. With unequal counts the
        two orderings are always different and the mutant always dies.
        """
        for index in range(3):
            _fact(warehouse, accn=f"o{index}", value="1", unit="USD", concept=f"C{index}")
        for index in range(2):
            _fact(warehouse, accn=f"s{index}", value="1", unit="shares", concept=f"C{index}")
        _fact(warehouse, accn="p0", value="1", unit="USD/shares")

        panel = contamination_by_unit_class(warehouse)

        assert [row.facts for row in panel] == [1, 2, 3], "sizes must be strictly increasing"
        assert [row.unit_class for row in panel] == ["per-share", "share count", "other"]


class TestTheSurvivalSplit:
    """Whether filers that went dark restate differently from those still filing.

    Post-hoc, and written to replace a caveat that asserted this without
    measuring it -- and asserted it from a false premise about how the universe
    was drawn.
    """

    def test_every_cutoff_is_reported(self, warehouse: Any) -> None:
        """Reporting one cutoff would make it a choice made after seeing the answer."""
        _fact(warehouse, accn="a", value="100")

        splits = contamination_by_survival(warehouse)

        assert [split.cutoff for split in splits] == list(SURVIVAL_CUTOFFS)

    def test_the_two_cohorts_account_for_every_fact(self, warehouse: Any) -> None:
        """The join must not drop facts.

        A filer missing from the filings table would land in neither cohort, and
        a study about numbers going quietly missing should not quietly lose any.
        """
        _fact(warehouse, accn="a", value="100", cik=1)
        _fact(warehouse, accn="b", value="180", cik=1)
        _fact(warehouse, accn="c", value="100", cik=2)

        population = measure_contamination(warehouse)
        for split in contamination_by_survival(warehouse):
            assert split.active_facts + split.dormant_facts == population.facts
            assert split.active_restated + split.dormant_restated == population.restated_facts

    def test_a_filer_that_stopped_filing_lands_in_the_dormant_cohort(self, warehouse: Any) -> None:
        """``_fact`` derives filed_at from the accession, so the cohort is controllable.

        Single-letter accessions file in 2011, which is before every cutoff, so
        this whole warehouse is dormant throughout.
        """
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="180")

        for split in contamination_by_survival(warehouse):
            assert (split.dormant_facts, split.dormant_restated) == (1, 1)
            assert (split.active_facts, split.active_restated) == (0, 0)
            assert split.dormant_share == Decimal("1")

    def test_the_gap_is_dormant_minus_active(self, warehouse: Any) -> None:
        """Sign convention: positive means dead filers restate more.

        The caveat reads directly off this sign, so getting it backwards would
        invert the memo's conclusion about the direction of survivorship bias.
        """
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="180")

        split = contamination_by_survival(warehouse)[0]

        assert split.gap == split.dormant_share - split.active_share
        assert split.gap == Decimal("1"), "all dormant, all restated, none active"

    def test_an_empty_corpus_reports_zero_rather_than_dividing_by_zero(
        self, warehouse: Any
    ) -> None:
        for split in contamination_by_survival(warehouse):
            assert split.active_share == Decimal("0")
            assert split.dormant_share == Decimal("0")
            assert split.gap == Decimal("0")


class TestTheVintageConfound:
    """The stratification that retracted the survivorship finding.

    Pooled, dormant filers look like they restate more, at every cutoff. Split by
    accounting period, they restate less, in every band. Both are arithmetic;
    the pooled version is reading the period mix and calling it dormancy.
    """

    def test_every_band_is_reported(self, warehouse: Any) -> None:
        _fact(warehouse, accn="a", value="100")

        bands = survival_by_period_band(warehouse)

        assert [label for label, _ in bands] == [label for label, _ in PERIOD_BANDS]

    def test_the_bands_partition_the_corpus(self, warehouse: Any) -> None:
        """No fact may fall between two bands or into both.

        The bands are built from a moving lower bound, which is exactly the shape
        that produces an off-by-one gap or overlap at a boundary.
        """
        _fact(warehouse, accn="a", value="100")
        _fact(warehouse, accn="b", value="180")
        _fact(warehouse, accn="c", value="100", concept="Other")

        population = measure_contamination(warehouse)
        bands = survival_by_period_band(warehouse)
        total = sum(split.active_facts + split.dormant_facts for _, split in bands)
        restated = sum(split.active_restated + split.dormant_restated for _, split in bands)

        assert total == population.facts
        assert restated == population.restated_facts

    def test_the_bias_is_strictly_smaller_than_the_cohort_gap(self, warehouse: Any) -> None:
        """The conflation the memo made, and the fixture that can actually see it.

        ``active_only_bias`` is ``gap`` scaled by the dormant share of the
        corpus, so it is strictly smaller whenever any active facts exist.
        Quoting the gap in its place therefore always overstates -- which is what
        the memo did, threefold to tenfold.

        The first version of this test used a warehouse with no active filer at
        all. With an empty active cohort the two quantities coincide, so it
        passed against the mutation that replaces one with the other, and the
        mutation gate caught the weak test. Both cohorts are now populated, with
        deliberately different rates, so the two numbers cannot agree by accident:
        active 1 of 2 restated (50%), dormant 2 of 2 (100%), pooled 3 of 4 (75%).
        Gap is 50pp; bias is 25pp.

        ``_fact`` derives ``filed_at`` from the accession length and first
        letter, so an eight-character accession files in 2018 and a
        one-character one in 2011 -- which straddles the first cutoff.
        """
        _fact(warehouse, accn="aaaaaaa1", value="100", cik=1, concept="A")
        _fact(warehouse, accn="aaaaaaa2", value="150", cik=1, concept="A")
        _fact(warehouse, accn="aaaaaaa3", value="100", cik=1, concept="B")
        _fact(warehouse, accn="a", value="100", cik=2, concept="A")
        _fact(warehouse, accn="b", value="150", cik=2, concept="A")
        _fact(warehouse, accn="c", value="100", cik=2, concept="B")
        _fact(warehouse, accn="d", value="190", cik=2, concept="B")

        split = contamination_by_survival(warehouse)[0]

        assert (split.active_facts, split.active_restated) == (2, 1)
        assert (split.dormant_facts, split.dormant_restated) == (2, 2)
        assert split.active_share == Decimal("0.5")
        assert split.dormant_share == Decimal("1")
        assert split.pooled_share == Decimal("0.75")
        assert split.gap == Decimal("0.5")
        assert split.active_only_bias == Decimal("0.25")
        assert abs(split.active_only_bias) < abs(split.gap)


class TestTheOpportunityConfound:
    """The confound the *fix* introduced, found by reading the shipped output.

    Stratifying by accounting period removes the vintage entanglement. It also
    maximises a second one, which the banded table then reports as a finding: a
    filer that went dark stopped filing, so inside a band its facts were mostly
    published once -- and a fact published once cannot be restated, whatever its
    filer would have done. On the real corpus the 2023-onward dormant cell has 67
    republished facts against 444,629 active ones.

    The fixture is built so the two confounds point opposite ways, which is the
    situation the real data is in. Raw, the dormant cohort restates LESS
    (1/4 against 1/3). Conditioned on having had a second report, it restates
    MORE (1/1 against 1/2). Nothing about the fixture changed between those two
    sentences except the denominator.
    """

    @staticmethod
    def _opposing_cohorts(warehouse: Any) -> None:
        """Active cik=1 (files 2018), dormant cik=2 (files 2011).

        The load-bearing rows are the ones published twice with the SAME value --
        ``cik=1`` concepts C and D, and ``cik=2`` concept C. Without them
        ``reports >= 2`` and ``distinct_values >= 2`` select identically and the
        mutation that swaps one for the other survives. The first version of this
        fixture had such a row for the active cohort but not the dormant one, and
        the gate caught it: the banded mutant lived. **Both** cohorts need
        ``restatable > restated``, which is the whole distinction being tested.
        This is the third round in which a fixture rather than the code was the
        weak link, so it is asserted directly rather than assumed.

        Active:  6 facts, 4 republished, 2 restated -> 33.33% raw, 50.00% cond.
        Dormant: 7 facts, 3 republished, 2 restated -> 28.57% raw, 66.67% cond.
        Raw says dormant restate less; conditional says they restate more.
        """
        # Active: A and B genuinely restated, C and D republished unchanged,
        # E and F filed once and therefore incapable of showing a restatement.
        for accn, value, concept in (
            ("aaaaaa01", "100", "A"),
            ("aaaaaa02", "150", "A"),
            ("aaaaaa03", "100", "B"),
            ("aaaaaa04", "150", "B"),
            ("aaaaaa05", "100", "C"),
            ("aaaaaa06", "100", "C"),
            ("aaaaaa07", "100", "D"),
            ("aaaaaa08", "100", "D"),
            ("aaaaaa09", "100", "E"),
            ("aaaaaa10", "100", "F"),
        ):
            _fact(warehouse, accn=accn, value=value, cik=1, concept=concept)
        # Dormant: A and B restated, C republished unchanged, D-G filed once.
        for accn, value, concept in (
            ("a", "100", "A"),
            ("b", "150", "A"),
            ("c", "100", "B"),
            ("d", "150", "B"),
            ("e", "100", "C"),
            ("f", "100", "C"),
            ("g", "100", "D"),
            ("h", "100", "E"),
            ("i", "100", "F"),
            ("j", "100", "G"),
        ):
            _fact(warehouse, accn=accn, value=value, cik=2, concept=concept)

    def test_republication_is_counted_apart_from_restatement(self, warehouse: Any) -> None:
        """A fact republished with an unchanged value is restatable, not restated."""
        self._opposing_cohorts(warehouse)

        split = contamination_by_survival(warehouse)[0]

        assert (split.active_facts, split.active_restatable, split.active_restated) == (6, 4, 2)
        assert (split.dormant_facts, split.dormant_restatable, split.dormant_restated) == (7, 3, 2)

    def test_conditioning_on_a_second_report_moves_the_sign(self, warehouse: Any) -> None:
        """The whole reason these columns exist.

        The raw gap says the dormant cohort restates less. The conditional gap,
        on the same rows, says it restates more. Neither is a dormancy effect --
        the point is that the answer is a function of which confound is left in.
        """
        self._opposing_cohorts(warehouse)

        split = contamination_by_survival(warehouse)[0]

        assert split.active_share == Decimal("0.33333333")
        assert split.dormant_share == Decimal("0.28571429")
        assert split.gap < 0, "raw: dormant appear to restate less"

        assert split.active_conditional_share == Decimal("0.5")
        assert split.dormant_conditional_share == Decimal("0.66666667")
        assert split.conditional_gap > 0, "conditioned: they appear to restate more"

    def test_the_cohorts_differ_in_opportunity_itself(self, warehouse: Any) -> None:
        """The measurement that makes the banded comparison unsafe to read."""
        self._opposing_cohorts(warehouse)

        split = contamination_by_survival(warehouse)[0]

        assert split.active_restatable_share == Decimal("0.66666667")
        assert split.dormant_restatable_share == Decimal("0.42857143")
        assert split.active_restatable_share > split.dormant_restatable_share

    def test_restated_never_exceeds_restatable_never_exceeds_facts(self, warehouse: Any) -> None:
        """A fact must be republished before its value can differ. Holds per band."""
        self._opposing_cohorts(warehouse)

        for _, split in survival_by_period_band(warehouse):
            assert split.active_restated <= split.active_restatable <= split.active_facts
            assert split.dormant_restated <= split.dormant_restatable <= split.dormant_facts

    def test_a_thin_cell_serialises_as_null_and_not_as_a_rate(self, warehouse: Any) -> None:
        """The guard has to live in the data, not only in the console formatter.

        It once lived only in the printer. The evidence card -- the one artifact
        meant to be read without any surrounding prose -- got the unguarded rate
        anyway, which is the single place where a misleading number does the most
        damage. Both cohorts are checked: only the dormant side is thin on the
        real corpus, so a one-sided guard would be correct by accident.
        """
        _fact(warehouse, accn="aaaaaaa1", value="100", cik=1, concept="A")
        _fact(warehouse, accn="aaaaaaa2", value="150", cik=1, concept="A")
        _fact(warehouse, accn="a", value="100", cik=2, concept="A")

        row = contamination_by_survival(warehouse)[0].as_dict()

        assert row["dormant_conditional_share"] is None
        assert row["active_conditional_share"] is None
        assert row["conditional_gap"] is None
        assert "censoring artefact" in row["conditional_suppressed_reason"]
        assert "conditional_caveat" not in row, "no caveat on a row with nothing to caveat"
        # The counts themselves are never suppressed -- only rates over them.
        assert row["dormant_restatable"] == 0
        assert row["active_restatable"] == 1

    def test_one_fat_cohort_does_not_license_a_rate_on_a_thin_one(self, warehouse: Any) -> None:
        """The shape the real corpus actually has, which the test above does not.

        In the 2023-onward band the active cohort holds 444,629 republished facts
        and the dormant one holds 67. A guard that asks whether *either* cohort is
        large enough passes that cell and reports a rate built on 67 facts. The
        previous test has both cohorts thin, so it cannot tell ``min`` from
        ``max`` -- and the mutation gate caught exactly that: the one-sided-guard
        mutant survived it. This is the fixture that kills it.
        """
        for i in range(MIN_CONDITIONAL_DENOMINATOR):
            _fact(warehouse, accn=f"{i:07d}x", value="100", cik=1, concept=f"C{i}")
            _fact(warehouse, accn=f"{i:07d}y", value="150", cik=1, concept=f"C{i}")
        _fact(warehouse, accn="000000x", value="100", cik=2, concept="A")
        _fact(warehouse, accn="000000y", value="150", cik=2, concept="A")

        split = contamination_by_survival(warehouse)[0]

        assert split.active_restatable >= MIN_CONDITIONAL_DENOMINATOR
        assert split.dormant_restatable == 1
        assert not split.conditional_is_reportable, "one fat cohort cannot license the thin one"
        assert split.as_dict()["conditional_gap"] is None

    def test_a_reportable_row_carries_its_disclaimer_into_the_card(self, warehouse: Any) -> None:
        """A shipped number travels with the sentence that says what it is not.

        The pooled conditional gap is the largest and most stable directional
        figure the study computes, and it is the least identified: pooled across
        periods *and* conditioned on republication, so it carries both confounds.
        It was persisted to the card with no caveat while the prose disclaimed it
        elsewhere -- a reader of the card alone got a clean positive finding.
        """
        # ``_fact`` derives the filing year from ``len(accn) % 9``, so the active
        # cohort needs 8-character accessions (2018, after the first cutoff) and
        # the dormant one 7 (2017, before it). Both cohorts must clear the
        # threshold or the guard suppresses the row and the test proves nothing.
        for i in range(MIN_CONDITIONAL_DENOMINATOR):
            for cik, stem in ((1, f"{i:07d}"), (2, f"{i:06d}")):
                _fact(warehouse, accn=f"{stem}x", value="100", cik=cik, concept=f"C{i}")
                _fact(warehouse, accn=f"{stem}y", value="150", cik=cik, concept=f"C{i}")

        row = contamination_by_survival(warehouse)[0].as_dict()

        assert row["conditional_gap"] is not None
        assert "NOT A FINDING" in row["conditional_caveat"]
        assert "conditional_suppressed_reason" not in row

    def test_the_band_table_carries_the_same_counts(self, warehouse: Any) -> None:
        """Every fixture fact sits in a 2008 period, so one band holds them all."""
        self._opposing_cohorts(warehouse)

        bands = dict(survival_by_period_band(warehouse, cutoff="2018-01-01"))
        held = bands["through 2014"]

        assert (held.active_restatable, held.dormant_restatable) == (4, 3)
        assert sum(s.active_restatable + s.dormant_restatable for s in bands.values()) == 7


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
