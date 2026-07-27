"""Point-in-time semantics.

The first test in this file is the acceptance test for the entire system. Apple
restated FY2008 diluted EPS from 5.36 to 6.78 on 2010-01-25. A point-in-time
engine must return 5.36 on 2009-12-01 and 6.78 on 2010-06-01. A conventional
fundamentals panel returns 6.78 for both, which is how a backtest of 2009 comes
to trade on a number published in 2010.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aletheia.core.errors import AmbiguousPeriod, InsufficientData
from aletheia.core.types import Cik
from aletheia.pit import INSTANT, PitFact, as_of
from aletheia.store.db import Warehouse
from tests._factories import (
    FIRST_REPORT_FILED,
    FY2008_END,
    RESTATEMENT_FILED,
    first_report,
    make_fact,
    restatement,
)

AAPL = Cik(320193)
BEFORE_ANY_REPORT = date(2009, 10, 1)
AFTER_FIRST_REPORT = date(2009, 12, 1)
AFTER_RESTATEMENT = date(2010, 6, 1)


@pytest.fixture
def loaded(warehouse: Warehouse) -> Warehouse:
    warehouse.write_facts([first_report(), restatement()])
    return warehouse


class TestTheCoreGuarantee:
    def test_returns_the_value_that_was_public_at_the_time(self, loaded: Warehouse) -> None:
        """THE acceptance test. Same query, two dates, two answers."""
        assert as_of(loaded, AFTER_FIRST_REPORT).value(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        ) == Decimal("5.36")
        assert as_of(loaded, AFTER_RESTATEMENT).value(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        ) == Decimal("6.78")

    def test_the_default_path_picks_the_revision_as_well_as_the_period(
        self, loaded: Warehouse
    ) -> None:
        """The acceptance test again, with no period named.

        Omitting ``period_end`` makes two choices, not one: which period, and
        which of that period's revisions. The tests above only ever prove the
        second when the first is supplied explicitly, and until the guard was
        narrowed this path raised unconditionally, so it has never been
        exercised end to end. A run that returns the first report and a run that
        returns the latest knowable one are indistinguishable on any period that
        was never revised -- which is most of them.
        """
        early = as_of(loaded, AFTER_FIRST_REPORT).fact(AAPL, "EarningsPerShareDiluted")
        assert (early.value, early.report_seq) == (Decimal("5.36"), 1)
        late = as_of(loaded, AFTER_RESTATEMENT).fact(AAPL, "EarningsPerShareDiluted")
        assert (late.value, late.report_seq) == (Decimal("6.78"), 2)

    def test_the_boundary_date_is_inclusive(self, loaded: Warehouse) -> None:
        """A filing is knowable on the day it is published, not the day after."""
        on_the_day = as_of(loaded, RESTATEMENT_FILED)
        assert on_the_day.value(AAPL, "EarningsPerShareDiluted", period_end=FY2008_END) == Decimal(
            "6.78"
        )
        day_before = as_of(loaded, date(2010, 1, 24))
        assert day_before.value(AAPL, "EarningsPerShareDiluted", period_end=FY2008_END) == Decimal(
            "5.36"
        )

    def test_an_unpublished_period_raises_rather_than_returning_a_blank(
        self, loaded: Warehouse
    ) -> None:
        """Absence must be loud. A silent None becomes a NaN becomes a Sharpe ratio."""
        with pytest.raises(InsufficientData, match="had not been published"):
            as_of(loaded, BEFORE_ANY_REPORT).value(
                AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
            )

    def test_report_sequence_identifies_the_restatement(self, loaded: Warehouse) -> None:
        original = as_of(loaded, AFTER_FIRST_REPORT).fact(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        current = as_of(loaded, AFTER_RESTATEMENT).fact(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert original.is_first_report
        assert not current.is_first_report
        assert current.report_seq == 2

    def test_every_returned_fact_carries_its_provenance(self, loaded: Warehouse) -> None:
        fact = as_of(loaded, AFTER_RESTATEMENT).fact(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert fact.accn.value == "0001193125-10-012091"
        assert fact.source_uri.startswith("https://data.sec.gov/")
        assert len(fact.content_sha256) == 64


class TestFirstReportedVersusRestated:
    def test_first_reported_ignores_later_restatements(self, loaded: Warehouse) -> None:
        """What the market actually reacted to, at the time it reacted."""
        fact = as_of(loaded, AFTER_RESTATEMENT).first_reported(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert fact.value == Decimal("5.36")
        assert fact.knowledge_date == FIRST_REPORT_FILED

    def test_the_lookahead_accessor_is_named_so_it_cannot_be_typed_by_accident(
        self, loaded: Warehouse
    ) -> None:
        """This is what a vendor panel gives you — 6.78, months before it existed."""
        view = as_of(loaded, AFTER_FIRST_REPORT)
        restated = view.unsafe_latest_restated(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert restated.value == Decimal("6.78")
        assert restated.knowledge_date > view.as_of, "this is the lookahead, by design"

    def test_the_gap_between_them_is_the_bias_a_vendor_panel_introduces(
        self, loaded: Warehouse
    ) -> None:
        """Measuring the bias is the legitimate use of the unsafe accessor."""
        view = as_of(loaded, AFTER_FIRST_REPORT)
        honest = view.value(AAPL, "EarningsPerShareDiluted", period_end=FY2008_END)
        naive = view.unsafe_latest_restated(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        ).value
        assert round((naive - honest) / honest, 4) == Decimal("0.2649")


class TestRevisions:
    def test_a_revision_is_invisible_until_it_is_published(self, loaded: Warehouse) -> None:
        assert as_of(loaded, AFTER_FIRST_REPORT).revisions(AAPL) == []

    def test_a_published_revision_is_visible_with_its_magnitude(self, loaded: Warehouse) -> None:
        revisions = as_of(loaded, AFTER_RESTATEMENT).revisions(AAPL)
        assert len(revisions) == 1
        revision = revisions[0]
        assert revision.prior_value == Decimal("5.36")
        assert revision.new_value == Decimal("6.78")
        assert round(revision.relative_change, 4) == Decimal("0.2649")
        assert revision.days_to_revision == 90

    def test_a_magnitude_filter_excludes_small_changes(self, loaded: Warehouse) -> None:
        view = as_of(loaded, AFTER_RESTATEMENT)
        assert len(view.revisions(AAPL, min_relative_change=0.10)) == 1
        assert view.revisions(AAPL, min_relative_change=0.50) == []

    def test_a_zero_prior_value_refuses_a_ratio_instead_of_dividing(
        self, warehouse: Warehouse
    ) -> None:
        """A restatement away from zero is real; a division by it is not."""
        warehouse.write_facts(
            [
                make_fact(value="0", filed_at=date(2009, 10, 27), accn="0001193125-09-214859"),
                make_fact(value="1.5", filed_at=date(2010, 1, 25), accn="0001193125-10-012091"),
            ]
        )
        revision = as_of(warehouse, AFTER_RESTATEMENT).revisions(AAPL)[0]
        assert revision.absolute_change == Decimal("1.5")
        with pytest.raises(ZeroDivisionError, match="use absolute_change"):
            _ = revision.relative_change

    def test_a_magnitude_filter_keeps_revisions_off_a_zero_base(self, warehouse: Warehouse) -> None:
        """Undefined is not small.

        A move off zero has no relative change to compare, and requiring
        ``prior_value <> 0`` drops it -- so a caller asking for "revisions above
        50%" is silently not shown the unbounded ones. It is not a corner case:
        4,449 of the warehouse's 394,320 revisions start from zero, and the API
        applies this filter by default, so all 4,449 were missing from the
        revision explorer. Arconic restated equity in affiliates for 2017 from $0
        to $1.02bn and it appeared under no threshold at all.
        """
        warehouse.write_facts(
            [
                make_fact(value="0", filed_at=date(2009, 10, 27), accn="0001193125-09-214859"),
                make_fact(value="1.5", filed_at=date(2010, 1, 25), accn="0001193125-10-012091"),
            ]
        )
        view = as_of(warehouse, AFTER_RESTATEMENT)
        for threshold in (0.05, 0.50, 100.0):
            assert len(view.revisions(AAPL, min_relative_change=threshold)) == 1, threshold

    def test_but_a_zero_to_zero_pair_is_not_a_revision_at_any_threshold(
        self, warehouse: Warehouse
    ) -> None:
        """The other side. Passing every threshold must not mean passing on nothing."""
        warehouse.write_facts(
            [
                make_fact(value="0", filed_at=date(2009, 10, 27), accn="0001193125-09-214859"),
                make_fact(value="0", filed_at=date(2010, 1, 25), accn="0001193125-10-012091"),
            ]
        )
        view = as_of(warehouse, AFTER_RESTATEMENT)
        assert view.revisions(AAPL) == []
        assert view.revisions(AAPL, min_relative_change=0.05) == []


class TestMacroVintages:
    def test_returns_the_figure_published_at_the_time(self, warehouse: Warehouse) -> None:
        """Real 2020Q2 GDP as first printed, not as it stands after eight revisions."""
        _load_gdp_vintages(warehouse)
        view = as_of(warehouse, date(2020, 8, 1))
        assert view.macro("GDPC1", date(2020, 4, 1)) == pytest.approx(17205.822)

    def test_a_later_view_sees_the_revision(self, warehouse: Warehouse) -> None:
        _load_gdp_vintages(warehouse)
        assert as_of(warehouse, date(2026, 1, 1)).macro("GDPC1", date(2020, 4, 1)) == pytest.approx(
            19077.992
        )

    def test_a_figure_not_yet_published_raises(self, warehouse: Warehouse) -> None:
        """2020Q2 GDP was not published until 2020-07-30. In June it did not exist."""
        _load_gdp_vintages(warehouse)
        with pytest.raises(InsufficientData, match="had no published value"):
            as_of(warehouse, date(2020, 6, 1)).macro("GDPC1", date(2020, 4, 1))


class TestPriceExecutionLag:
    def test_a_bar_is_tradable_only_after_the_stated_lag(self, warehouse: Warehouse) -> None:
        _load_prices(warehouse)
        bars = as_of(warehouse, date(2024, 1, 31)).prices(
            "AAPL", start=date(2024, 1, 2), execution_lag_days=1
        )
        assert bars[0].bar_date == date(2024, 1, 2)
        assert bars[0].tradable_from == date(2024, 1, 3)

    def test_the_lag_must_be_stated(self, warehouse: Warehouse) -> None:
        """No default. Same-close execution is the commonest silent lookahead."""
        _load_prices(warehouse)
        with pytest.raises(TypeError):
            as_of(warehouse, date(2024, 1, 31)).prices("AAPL", start=date(2024, 1, 2))  # type: ignore[call-arg]

    def test_bars_after_the_as_of_date_are_not_returned(self, warehouse: Warehouse) -> None:
        _load_prices(warehouse)
        bars = as_of(warehouse, date(2024, 1, 3)).prices(
            "AAPL", start=date(2024, 1, 2), end=date(2024, 1, 31), execution_lag_days=1
        )
        assert [bar.bar_date for bar in bars] == [date(2024, 1, 2), date(2024, 1, 3)]

    def test_a_negative_lag_is_refused(self, warehouse: Warehouse) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            as_of(warehouse, date(2024, 1, 31)).prices(
                "AAPL", start=date(2024, 1, 2), execution_lag_days=-1
            )


def _load_gdp_vintages(warehouse: Warehouse) -> None:
    """The real published history of US real GDP for 2020Q2 (ALFRED, 2026-07-27)."""
    from aletheia.core.types import MacroObservation
    from tests._factories import RETRIEVED_AT, RUN_ID

    vintages = [
        ("2020-07-30", "2020-08-26", 17205.822),
        ("2020-08-27", "2020-09-29", 17282.188),
        ("2020-09-30", "2021-07-28", 17302.511),
        ("2021-07-29", "2022-09-28", 17258.205),
        ("2022-09-29", "2023-09-27", 17378.712),
        ("2023-09-28", "2024-09-25", 19034.830),
        ("2024-09-26", "2025-09-24", 19056.617),
        ("2025-09-25", "9999-12-31", 19077.992),
    ]
    warehouse.write_macro(
        MacroObservation(
            series_id="GDPC1",
            obs_date=date(2020, 4, 1),
            value=value,
            realtime_start=date.fromisoformat(start),
            realtime_end=date.fromisoformat(end),
            source_uri="https://api.stlouisfed.org/fred/series/observations?api_key=***",
            retrieved_at=RETRIEVED_AT,
            content_sha256="0" * 64,
            ingest_run_id=RUN_ID,
        )
        for start, end, value in vintages
    )


def _load_prices(warehouse: Warehouse) -> None:
    from aletheia.core.types import PriceBar
    from tests._factories import RETRIEVED_AT, RUN_ID

    warehouse.write_prices(
        PriceBar(
            symbol="AAPL",
            bar_date=date(2024, 1, day),
            open=185.0,
            high=186.0,
            low=184.0,
            close=185.5,
            adj_close=184.9,
            volume=1e6,
            source="fmp",
            source_uri="https://financialmodelingprep.com/stable/historical-price-eod/full?apikey=***",
            retrieved_at=RETRIEVED_AT,
            content_sha256="0" * 64,
            ingest_run_id=RUN_ID,
        )
        for day in (2, 3, 4, 5)
    )


class TestPinningToAnInstant:
    """A balance-sheet instant can collide with a duration, and it must be nameable.

    ``period_start=None`` means "do not filter" in every query, which is what most
    callers want. But an instant *has* no start date, so a caller holding one could
    not pin to it: handing back its own ``period_start`` widened the query to every
    period sharing the end date instead of narrowing it to the one meant. In a
    warehouse of 13.4M facts, 590 (cik, taxonomy, concept, unit, period_end) groups
    hold both an instant and a duration -- e.g. AAR Corp's
    AntidilutiveSecuritiesExcludedFromComputationOfEarningsPerShareAmount ending
    2023-05-31, one instant and two durations.

    Two call sites intended to pin and silently did not, so both raised
    AmbiguousPeriod on exactly the queries the pin existed to disambiguate.
    :data:`INSTANT` is the third state that makes the intent expressible.
    """

    SHARED_END = date(2023, 5, 31)
    QUARTER_START = date(2023, 3, 1)
    INSTANT_VALUE = "1400000"
    QUARTER_VALUE = "900000"
    FILED = date(2023, 7, 18)

    @pytest.fixture
    def instant_and_duration(self, warehouse: Warehouse) -> Warehouse:
        for start, value in (
            (None, self.INSTANT_VALUE),
            (self.QUARTER_START, self.QUARTER_VALUE),
        ):
            warehouse.write_facts(
                [
                    make_fact(
                        value=value,
                        filed_at=self.FILED,
                        accn="0000001750-23-000041",
                        concept="AntidilutiveSecurities",
                        unit="shares",
                        period_start=start,
                        period_end=self.SHARED_END,
                    )
                ]
            )
        return warehouse

    def test_an_instant_colliding_with_a_duration_is_ambiguous(
        self, instant_and_duration: Warehouse
    ) -> None:
        view = as_of(instant_and_duration, date(2024, 1, 1))
        with pytest.raises(AmbiguousPeriod) as caught:
            view.first_reported(AAPL, "AntidilutiveSecurities", period_end=self.SHARED_END)
        assert None in caught.value.candidates
        assert "instant" in str(caught.value)

    def test_none_cannot_pin_to_the_instant(self, instant_and_duration: Warehouse) -> None:
        """The defect itself, kept as a test so the two meanings stay separated.

        Passing the instant's own ``period_start`` back -- which is ``None`` -- is
        the natural thing to write and is what both broken call sites wrote. It
        does not narrow anything. If this ever stops raising, ``None`` has been
        given a second meaning and every caller that omits the argument has
        quietly started filtering to instants only.
        """
        view = as_of(instant_and_duration, date(2024, 1, 1))
        with pytest.raises(AmbiguousPeriod):
            view.first_reported(
                AAPL, "AntidilutiveSecurities", period_end=self.SHARED_END, period_start=None
            )

    def test_the_sentinel_pins_to_the_instant(self, instant_and_duration: Warehouse) -> None:
        view = as_of(instant_and_duration, date(2024, 1, 1))
        fact = view.first_reported(
            AAPL, "AntidilutiveSecurities", period_end=self.SHARED_END, period_start=INSTANT
        )
        assert fact.value == Decimal(self.INSTANT_VALUE)
        assert fact.period_start is None

    def test_the_duration_is_still_reachable_by_its_date(
        self, instant_and_duration: Warehouse
    ) -> None:
        """Control: the sentinel must not become the only reachable branch."""
        view = as_of(instant_and_duration, date(2024, 1, 1))
        fact = view.first_reported(
            AAPL,
            "AntidilutiveSecurities",
            period_end=self.SHARED_END,
            period_start=self.QUARTER_START,
        )
        assert fact.value == Decimal(self.QUARTER_VALUE)

    def test_a_facts_pin_round_trips_through_a_query(self, instant_and_duration: Warehouse) -> None:
        """``fact.pin`` is what makes the fix usable: re-ask about the same period."""
        view = as_of(instant_and_duration, date(2024, 1, 1))
        for pinned in (INSTANT, self.QUARTER_START):
            fact = view.first_reported(
                AAPL, "AntidilutiveSecurities", period_end=self.SHARED_END, period_start=pinned
            )
            again = view.first_reported(
                AAPL,
                "AntidilutiveSecurities",
                period_end=fact.period_end,
                period_start=fact.pin,
            )
            assert again.value == fact.value
            assert again.period_start == fact.period_start

    def test_the_sentinel_and_no_filter_are_different_objects(self) -> None:
        """Guards the one way this fix could be undone by a well-meaning cleanup."""
        assert INSTANT is not None
        assert bool(INSTANT) is True


class TestALaterFilingCannotUnanswerAnEarlierQuestion:
    """The restated-value read is unguarded by design, so it must be pinned.

    ``unsafe_latest_restated`` deliberately ignores the knowledge date -- comparing
    it against the first report is how the bias is measured. But that means it sees
    filings made after the date being asked about, and a period that was
    unambiguous then can have acquired a second period since.

    Real case: AAR Corp (CIK 1750) ProfitLoss ending 2018-11-30 was a single
    182-day period on 2018-12-19. The 2019-03-20 10-Q added a 90-day period with
    the same end date. Asking about 2018-12-19 therefore raised AmbiguousPeriod --
    a question answerable at the time made unanswerable by data from its future, in
    the one component whose entire purpose is that the future cannot reach back.
    """

    SHARED_END = date(2018, 11, 30)
    HALF_YEAR_START = date(2018, 6, 1)
    QUARTER_START = date(2018, 9, 1)
    KNOWN_ON = date(2018, 12, 19)
    LATER_FILING = date(2019, 3, 20)

    @pytest.fixture
    def second_period_arrives_later(self, warehouse: Warehouse) -> Warehouse:
        warehouse.write_facts(
            [
                make_fact(
                    value="22100000",
                    filed_at=self.KNOWN_ON,
                    accn="0001104659-18-073842",
                    concept="ProfitLoss",
                    unit="USD",
                    period_start=self.HALF_YEAR_START,
                    period_end=self.SHARED_END,
                )
            ]
        )
        warehouse.write_facts(
            [
                make_fact(
                    value="7000000",
                    filed_at=self.LATER_FILING,
                    accn="0001104659-19-016320",
                    concept="ProfitLoss",
                    unit="USD",
                    period_start=self.QUARTER_START,
                    period_end=self.SHARED_END,
                )
            ]
        )
        return warehouse

    def test_the_question_was_answerable_when_it_was_asked(
        self, second_period_arrives_later: Warehouse
    ) -> None:
        fact = as_of(second_period_arrives_later, self.KNOWN_ON).fact(
            AAPL, "ProfitLoss", period_end=self.SHARED_END
        )
        assert fact.value == Decimal("22100000")
        assert fact.period_start == self.HALF_YEAR_START

    def test_the_unpinned_full_vintage_read_is_what_broke_it(
        self, second_period_arrives_later: Warehouse
    ) -> None:
        """The defect, held in place: end date alone re-opens a settled question."""
        full = as_of(second_period_arrives_later, date(2026, 1, 1))
        with pytest.raises(AmbiguousPeriod):
            full.unsafe_latest_restated(AAPL, "ProfitLoss", period_end=self.SHARED_END)

    def test_pinning_to_the_answered_period_survives_the_later_filing(
        self, second_period_arrives_later: Warehouse
    ) -> None:
        known = as_of(second_period_arrives_later, self.KNOWN_ON).fact(
            AAPL, "ProfitLoss", period_end=self.SHARED_END
        )
        full = as_of(second_period_arrives_later, date(2026, 1, 1))
        restated = full.unsafe_latest_restated(
            AAPL,
            "ProfitLoss",
            period_end=known.period_end,
            period_start=known.pin,
            unit=known.unit,
        )
        assert restated.value == Decimal("22100000")
        assert restated.period_start == self.HALF_YEAR_START


class TestAPeriodEndDoesNotIdentifyAPeriod:
    """A fiscal year and its fourth quarter end on the same day.

    Real numbers from the warehouse: Apple's FY2015 net income is $53,394,000,000
    over 363 days and its Q4 2015 net income is $11,124,000,000 over 90 days, both
    tagged period_end 2015-09-26 at report_seq 1. 860,961 (cik, concept, unit,
    period_end) groups across a 778-filer warehouse hold more than one period_start.

    Before this guard, first_reported keyed on the end date alone, matched both,
    and returned whichever sorted first. An accruals ratio built on that divides a
    quarter's earnings by a year's cash flow and reports it as a signal.
    """

    FY2015_START = date(2014, 9, 28)
    Q4_2015_START = date(2015, 6, 28)
    SHARED_END = date(2015, 9, 26)
    FY_INCOME = "53394000000"
    Q4_INCOME = "11124000000"

    @pytest.fixture
    def year_and_quarter(self, warehouse: Warehouse) -> Warehouse:
        for start, value in (
            (self.FY2015_START, self.FY_INCOME),
            (self.Q4_2015_START, self.Q4_INCOME),
        ):
            warehouse.write_facts(
                [
                    make_fact(
                        value=value,
                        filed_at=date(2015, 10, 28),
                        accn="0001193125-15-356351",
                        concept="NetIncomeLoss",
                        unit="USD",
                        period_start=start,
                        period_end=self.SHARED_END,
                    )
                ]
            )
        return warehouse

    def test_an_ambiguous_request_raises_rather_than_guessing(
        self, year_and_quarter: Warehouse
    ) -> None:
        view = as_of(year_and_quarter, date(2016, 1, 1))
        with pytest.raises(AmbiguousPeriod, match="matches 2 reporting periods"):
            view.first_reported(AAPL, "NetIncomeLoss", period_end=self.SHARED_END)

    def test_the_error_names_the_candidates_so_the_caller_can_choose(
        self, year_and_quarter: Warehouse
    ) -> None:
        view = as_of(year_and_quarter, date(2016, 1, 1))
        try:
            view.first_reported(AAPL, "NetIncomeLoss", period_end=self.SHARED_END)
        except AmbiguousPeriod as exc:
            assert set(exc.candidates) == {self.FY2015_START, self.Q4_2015_START}
            assert "363d" in str(exc)
            assert "90d" in str(exc)
        else:  # pragma: no cover - the test above already asserts it raises
            pytest.fail("expected AmbiguousPeriod")

    def test_naming_the_period_start_returns_the_year(self, year_and_quarter: Warehouse) -> None:
        """Control: with the ambiguity resolved, the right number comes back."""
        view = as_of(year_and_quarter, date(2016, 1, 1))
        fact = view.first_reported(
            AAPL, "NetIncomeLoss", period_end=self.SHARED_END, period_start=self.FY2015_START
        )
        assert fact.value == Decimal(self.FY_INCOME)

    def test_naming_the_quarter_returns_the_quarter(self, year_and_quarter: Warehouse) -> None:
        view = as_of(year_and_quarter, date(2016, 1, 1))
        fact = view.first_reported(
            AAPL, "NetIncomeLoss", period_end=self.SHARED_END, period_start=self.Q4_2015_START
        )
        assert fact.value == Decimal(self.Q4_INCOME)

    def test_an_unambiguous_period_still_needs_no_start_date(self, loaded: Warehouse) -> None:
        """The guard must not make every ordinary call require a start date."""
        fact = as_of(loaded, AFTER_RESTATEMENT).first_reported(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert fact.value == Decimal("5.36")

    def test_the_lookahead_escape_hatch_is_guarded_too(self, year_and_quarter: Warehouse) -> None:
        """unsafe_latest_restated skips the date filter, not the sanity checks."""
        view = as_of(year_and_quarter, date(2016, 1, 1))
        with pytest.raises(AmbiguousPeriod):
            view.unsafe_latest_restated(AAPL, "NetIncomeLoss", period_end=self.SHARED_END)

    def test_omitting_the_period_end_asks_for_the_latest_and_is_not_ambiguous(
        self, loaded: Warehouse
    ) -> None:
        """Not naming a period is not the same as naming an unclear one.

        The guard was applied to the whole unfiltered result set, so any concept
        with more than one period on file raised -- which is every real company.
        Apple's net income has 49 periods in the live warehouse and asking for it
        without an end date returned ``AmbiguousPeriod: matches 49 reporting
        periods``, a false ambiguity: 49 years are 49 different questions, not 49
        candidate answers to one.

        This reached the public API. ``GET /api/asof/{ticker}`` declares
        ``period_end`` optional and its own default therefore returned HTTP 500 --
        on the single endpoint the system exists to demonstrate. Every test called
        ``fact()`` with an explicit end date, so nothing caught it.
        """
        fact = as_of(loaded, AFTER_RESTATEMENT).fact(AAPL, "EarningsPerShareDiluted")
        assert fact.period_end == FY2008_END

    def test_the_latest_period_is_still_checked_for_ambiguity(
        self, year_and_quarter: Warehouse
    ) -> None:
        """The narrowing must not disable the guard for the period it answers.

        Control for the test above: if the newest end date is itself shared by a
        fiscal year and its fourth quarter, picking one silently is the original
        bug. Omitting ``period_end`` must still refuse here.
        """
        view = as_of(year_and_quarter, date(2016, 1, 1))
        with pytest.raises(AmbiguousPeriod, match="matches 2 reporting periods"):
            view.fact(AAPL, "NetIncomeLoss")

    def test_the_error_labels_each_span_by_its_own_dates(self, year_and_quarter: Warehouse) -> None:
        """The message branched on the shared end date, not each candidate's start.

        Both candidates were reported as "instant" -- so the one error that exists
        to tell a caller which two periods collided named neither of them.

        The assertion reads the parenthesised span list rather than the whole
        message: the message also *mentions* "instant" now, to tell the caller how
        to name a period that has no start date, and a whole-string check would
        conflate that hint with a mislabelled candidate.
        """
        view = as_of(year_and_quarter, date(2016, 1, 1))
        with pytest.raises(AmbiguousPeriod) as caught:
            view.fact(AAPL, "NetIncomeLoss")
        message = str(caught.value)
        # Delimited by the surrounding text, not by the first bracket: each span
        # carries its own "(363d)" parentheses inside the list.
        spans = message.split("reporting periods (", 1)[1].split("); pass", 1)[0]
        assert "instant" not in spans
        assert "2014-09-28..2015-09-26 (363d)" in message
        assert "2015-06-28..2015-09-26 (90d)" in message


class TestWhatAFactKnowsAboutItsOwnChain:
    """The two columns that describe a period's whole filing history.

    ``report_seq`` counts documents. On the real warehouse 91.8% of the rows it
    numbers above 1 carry the first-reported figure forward untouched, so every
    caller reading it as "restated" was answering a question nobody asked.
    ``differs_from_first_report`` and ``period_distinct_values`` are the two
    questions they meant, and neither is derivable from a fact's position.

    Fixture: AAR Corp's accrued current liabilities at 2021-05-31, filed five
    times as 174.2m, 174.2m, 148.3m, 148.3m, 174.2m -- one re-presentation of
    each value, two distinct values, and endpoints that agree. Alongside it,
    riding the same filings, a concept that genuinely never moved.
    """

    AAR = Cik(1750)
    PERIOD_END = date(2021, 5, 31)
    CONCEPT = "AccruedLiabilitiesCurrent"
    ORIGINAL = Decimal("174200000")
    INTERIM = Decimal("148300000")
    CHAIN = (
        ("174200000", "0001104659-21-094125", date(2021, 7, 21)),
        ("174200000", "0001104659-21-118843", date(2021, 9, 23)),
        ("148300000", "0001104659-21-152249", date(2021, 12, 21)),
        ("148300000", "0001104659-22-036639", date(2022, 3, 22)),
        ("174200000", "0001104659-22-081498", date(2022, 7, 21)),
    )
    # The control. Without a period that never moved, a `period_distinct_values`
    # stuck at the constant 2 would satisfy every other assertion here.
    STEADY = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    STEADY_VALUE = Decimal("974400000")

    @pytest.fixture
    def chain(self, warehouse: Warehouse) -> Warehouse:
        for value, accn, filed in self.CHAIN:
            warehouse.write_facts(
                [
                    make_fact(
                        value=value,
                        filed_at=filed,
                        accn=accn,
                        concept=self.CONCEPT,
                        unit="USD",
                        cik=int(self.AAR),
                        period_start=None,
                        period_end=self.PERIOD_END,
                    ),
                    make_fact(
                        value=str(self.STEADY_VALUE),
                        filed_at=filed,
                        accn=accn,
                        concept=self.STEADY,
                        unit="USD",
                        cik=int(self.AAR),
                        period_start=None,
                        period_end=self.PERIOD_END,
                    ),
                ]
            )
        return warehouse

    def _on(self, chain: Warehouse, day: date, concept: str | None = None) -> PitFact:
        return as_of(chain, day).fact(
            self.AAR,
            concept or self.CONCEPT,
            period_end=self.PERIOD_END,
            period_start=INSTANT,
        )

    def test_a_republished_figure_knows_it_did_not_move(self, chain: Warehouse) -> None:
        """Second publication, same number. ``report_seq`` says 2 and means it."""
        fact = self._on(chain, date(2021, 11, 1))
        assert fact.value == self.ORIGINAL
        assert fact.report_seq == 2
        assert fact.is_first_report is False
        assert fact.differs_from_first_report is False

    def test_a_changed_figure_knows_it_moved(self, chain: Warehouse) -> None:
        fact = self._on(chain, date(2022, 1, 1))
        assert fact.value == self.INTERIM
        assert fact.report_seq == 3
        assert fact.differs_from_first_report is True

    def test_the_count_covers_the_whole_period_not_the_rows_before_it(
        self, chain: Warehouse
    ) -> None:
        """Every fact in a period reports the same total, the first one included.

        This is the ordered-window trap. ``count(DISTINCT value)`` over a window
        carrying an ORDER BY becomes cumulative -- "how many values had appeared
        by this row" -- which reads 1 on the first publication of every revised
        period in the warehouse and would tell a reader standing on 2021-08-01
        that this figure had never been anything else. The question is about the
        period, not about the row, so the answer is 2 at every point on the chain.
        """
        assert self._on(chain, date(2021, 8, 1)).period_distinct_values == 2
        assert self._on(chain, date(2021, 11, 1)).period_distinct_values == 2
        assert self._on(chain, date(2022, 1, 1)).period_distinct_values == 2
        assert self._on(chain, date(2026, 6, 1)).period_distinct_values == 2

    def test_the_first_fact_already_knows_the_value_will_move(self, chain: Warehouse) -> None:
        """And it is the fact where a cumulative count would say otherwise.

        ``value_ever_changed`` describes the period's whole history, which is
        knowable from the data even where it is not knowable to a reader on the
        knowledge date. That is a deliberate split: the *value* is filtered to
        what was public, the *chain description* is not, exactly as
        ``report_seq`` has always behaved.
        """
        first = self._on(chain, date(2021, 8, 1))
        assert first.report_seq == 1
        assert first.is_first_report is True
        assert first.differs_from_first_report is False
        assert first.value_ever_changed is True

    def test_the_last_filing_matches_the_first_and_the_chain_still_says_it_moved(
        self, chain: Warehouse
    ) -> None:
        """Where every two-point comparison goes blind.

        First-reported and latest are both 174.2m, so comparing them sees
        nothing. 10,080 of the warehouse's 357,101 revised us-gaap periods --
        2.82% -- are this shape.
        """
        latest = self._on(chain, date(2026, 6, 1))
        assert latest.value == self.ORIGINAL
        assert latest.report_seq == 5
        assert latest.differs_from_first_report is False
        assert latest.value_ever_changed is True

    def test_a_period_that_never_moved_is_not_swept_up(self, chain: Warehouse) -> None:
        """The discriminator. Same five filings, same period, one value throughout.

        Five republications and nothing to report: this is what the 91.8%
        looks like, and it is the row that must stay quiet.
        """
        steady = self._on(chain, date(2026, 6, 1), self.STEADY)
        assert steady.value == self.STEADY_VALUE
        assert steady.report_seq == 5
        assert steady.differs_from_first_report is False
        assert steady.period_distinct_values == 1
        assert steady.value_ever_changed is False
