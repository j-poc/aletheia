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
from aletheia.pit import as_of
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
        """
        view = as_of(year_and_quarter, date(2016, 1, 1))
        with pytest.raises(AmbiguousPeriod) as caught:
            view.fact(AAPL, "NetIncomeLoss")
        assert "instant" not in str(caught.value)
        assert "2014-09-28..2015-09-26 (363d)" in str(caught.value)
        assert "2015-06-28..2015-09-26 (90d)" in str(caught.value)
