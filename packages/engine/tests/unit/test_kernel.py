"""The backtest kernel, and the three leaks it must refuse.

Every canary test here is two-sided. A guard that always fires is
indistinguishable from a guard that fires correctly, so each leak test is paired
with a control that is identical except for the leak, and the control must pass
cleanly. Without the pair, an implementation that raised unconditionally would
show a green suite.

The price panel is synthetic and generated from an explicit rule, because the
question these tests answer is about the kernel's date arithmetic, and a
synthetic panel is the only kind whose leak properties are known by construction.
Whether the kernel produces sensible *returns* on real data is a separate
question, answered by the study that runs on the warehouse.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pytest

from aletheia.pit import PitPrice
from aletheia.research.costs import SpreadEstimate
from aletheia.research.kernel import (
    BacktestResult,
    Exclusion,
    LeakDetected,
    Position,
    RebalancePeriod,
    SignalObservation,
    annualise,
    run_quantile_sort,
)

START = date(2015, 1, 1)
FORMATION = date(2016, 1, 4)
NEXT_FORMATION = date(2017, 1, 4)
CAPITAL = 10_000_000.0


def _bar(symbol: str, day: date, close: float) -> PitPrice:
    """A bar with a deterministic 1% high-low range, so the spread estimate is stable."""
    return PitPrice(
        symbol=symbol,
        bar_date=day,
        open=close,
        high=close * 1.005,
        low=close * 0.995,
        close=close,
        adj_close=close,
        volume=1_000_000.0,
        tradable_from=day,
    )


class FakePrices:
    """A weekday price series per symbol, with an explicit annual drift.

    ``drift`` is the fraction the symbol gains over one year, so a test can state
    the return it expects rather than reading one off a run.
    """

    def __init__(self, drifts: dict[str, float], *, honour_end: bool = True) -> None:
        self.drifts = drifts
        self.honour_end = honour_end
        self.calls: list[tuple[str, date, date]] = []

    def __call__(self, symbol: str, *, start: date, end: date) -> Sequence[PitPrice]:
        self.calls.append((symbol, start, end))
        if symbol not in self.drifts:
            return []
        # A leaky loader ignores the upper bound -- the realistic shape of the bug,
        # since a hand-written query with a wrong bound returns too much, not too little.
        upper = end if self.honour_end else end + timedelta(days=365)
        bars = []
        day = start
        while day <= upper:
            if day.weekday() < 5:
                years = (day - START).days / 365.25
                bars.append(_bar(symbol, day, 100.0 * (1.0 + self.drifts[symbol]) ** years))
            day += timedelta(days=1)
        return bars


def _panel(
    values: dict[str, float], *, knowledge_date: date = FORMATION
) -> list[SignalObservation]:
    return [
        SignalObservation(symbol=symbol, cik=index, value=value, knowledge_date=knowledge_date)
        for index, (symbol, value) in enumerate(sorted(values.items()), start=1)
    ]


SIGNALS = {"AAA": -0.30, "BBB": -0.20, "CCC": -0.05, "DDD": 0.05, "EEE": 0.20, "FFF": 0.30}
DRIFTS = {"AAA": 0.30, "BBB": 0.25, "CCC": 0.15, "DDD": 0.05, "EEE": 0.00, "FFF": -0.05}
"""Low accruals drift up and high accruals drift down -- Sloan's prediction, built
into the fixture so the kernel's sort direction is testable against a known
answer. Six names so that excluding one still leaves enough to fill both
quantiles; otherwise the exclusion tests would be measuring the thin-period guard
instead of the exclusion accounting."""


def _run(
    prices: FakePrices,
    panels: dict[date, list[SignalObservation]] | None = None,
    **kwargs: Any,
) -> BacktestResult:
    return run_quantile_sort(
        label="test",
        panels=panels or {FORMATION: _panel(SIGNALS), NEXT_FORMATION: _panel(SIGNALS)},
        load_prices=prices,
        n_quantiles=2,
        capital_usd=CAPITAL,
        **kwargs,
    )


class TestTheControl:
    """A clean run. Every leak test below is this, with one thing changed."""

    def test_a_well_formed_panel_produces_a_period(self) -> None:
        result = _run(FakePrices(DRIFTS))
        assert len(result.periods) == 1
        assert not result.exclusions

    def test_the_sort_goes_long_the_low_quantile_by_default(self) -> None:
        """Accruals predict negative returns, so the profitable side is the low one."""
        period = _run(FakePrices(DRIFTS)).periods[0]
        longs = {p.symbol for p in period.positions if p.weight > 0}
        shorts = {p.symbol for p in period.positions if p.weight < 0}
        assert longs == {"AAA", "BBB", "CCC"}
        assert shorts == {"DDD", "EEE", "FFF"}

    def test_long_high_flips_both_sides(self) -> None:
        period = _run(FakePrices(DRIFTS), long_high=True).periods[0]
        assert {p.symbol for p in period.positions if p.weight > 0} == {"DDD", "EEE", "FFF"}

    def test_the_portfolio_is_dollar_neutral(self) -> None:
        period = _run(FakePrices(DRIFTS)).periods[0]
        assert sum(p.weight for p in period.positions) == pytest.approx(0.0)
        assert sum(abs(p.weight) for p in period.positions) == pytest.approx(2.0)

    def test_the_return_has_the_sign_the_fixture_was_built_to_produce(self) -> None:
        period = _run(FakePrices(DRIFTS)).periods[0]
        assert period.gross_return > 0, "longs drift up and shorts drift down by construction"

    def test_no_leak_is_reported_when_there_is_none(self) -> None:
        """The control that makes the three canary tests meaningful."""
        _run(FakePrices(DRIFTS))  # must not raise


class TestCanarySignalDate:
    def test_a_signal_dated_after_formation_is_refused(self) -> None:
        """A panel assembled with a wrong date filter passes its own filter.

        It does not pass this: the kernel re-checks every observation against the
        formation date it was handed, so the mistake has to survive two
        independent people writing the same bug to get through.
        """
        late = _panel(SIGNALS, knowledge_date=FORMATION + timedelta(days=1))
        with pytest.raises(LeakDetected, match="became knowable"):
            _run(FakePrices(DRIFTS), panels={FORMATION: late, NEXT_FORMATION: _panel(SIGNALS)})

    def test_a_signal_dated_on_the_formation_day_is_accepted(self) -> None:
        """Control: same-day is knowable. The boundary is inclusive, not off by one."""
        same_day = _panel(SIGNALS, knowledge_date=FORMATION)
        result = _run(
            FakePrices(DRIFTS), panels={FORMATION: same_day, NEXT_FORMATION: _panel(SIGNALS)}
        )
        assert len(result.periods) == 1


class TestCanaryCostWindow:
    def test_a_cost_window_containing_future_bars_is_refused(self) -> None:
        """Estimating the spread from ranges that had not printed yet.

        This leak changes no return, only the cost -- which is exactly why it
        survives casual review, and why it is checked separately.
        """
        with pytest.raises(LeakDetected, match="cost window"):
            _run(FakePrices(DRIFTS, honour_end=False))

    def test_a_cost_window_ending_at_formation_is_accepted(self) -> None:
        """Control: the same window, bounded correctly."""
        _run(FakePrices(DRIFTS, honour_end=True))


class TestExecutionLag:
    """The commonest silent lookahead in daily equity backtesting.

    A daily bar is only complete at its close, so a signal derived from that bar
    cannot also be traded at that close. The kernel takes no default for the lag:
    the caller must state it, and the test below shows the stated value actually
    changes which bar is used.
    """

    class _OffersSameDayBar(FakePrices):
        """A loader that puts a bar on the formation date itself.

        Realistic: the formation date usually *is* a trading day, so the tempting
        bar is genuinely there. Whether it is used is the whole question.
        """

        def __call__(self, symbol: str, *, start: date, end: date) -> Sequence[PitPrice]:
            bars = list(super().__call__(symbol, start=start, end=end))
            if start == FORMATION and symbol in self.drifts:
                return [_bar(symbol, FORMATION, 100.0), *bars]
            return bars

    def test_a_stated_lag_skips_the_same_day_bar(self) -> None:
        period = _run(self._OffersSameDayBar(DRIFTS), execution_lag_days=1).periods[0]
        assert all(position.entry_date > FORMATION for position in period.positions)

    def test_a_zero_lag_takes_it(self) -> None:
        """Control: the same panel, the same bar, a different stated assumption.

        Market-on-close execution is optimistic but legitimate. The kernel objects
        to the contradiction between the stated lag and the bar used, not to the
        date itself -- so with the lag set to zero, the bar it refused above is
        exactly the one it takes.
        """
        period = _run(self._OffersSameDayBar(DRIFTS), execution_lag_days=0).periods[0]
        assert all(position.entry_date == FORMATION for position in period.positions)

    def test_the_lag_holds_across_every_position_in_a_normal_run(self) -> None:
        for lag in (0, 1, 5):
            result = _run(FakePrices(DRIFTS), execution_lag_days=lag)
            for period in result.periods:
                for position in period.positions:
                    assert (position.entry_date - period.formation_date).days >= lag
                    assert position.exit_date > position.entry_date


class TestExclusionAccounting:
    def test_a_name_without_prices_is_counted_not_dropped(self) -> None:
        """The survivorship stamp.

        A backtest that silently skips the names it could not price has measured
        something other than what it claims, and those names are disproportionately
        the ones that failed.
        """
        prices = FakePrices({key: value for key, value in DRIFTS.items() if key != "FFF"})
        panels = {FORMATION: _panel(SIGNALS), NEXT_FORMATION: _panel(SIGNALS)}
        result = run_quantile_sort(
            label="test",
            panels=panels,
            load_prices=prices,
            n_quantiles=2,
            capital_usd=CAPITAL,
        )
        period = result.periods[0]
        assert ("FFF", Exclusion.NO_PRICE_HISTORY) in period.excluded
        assert period.n_ranked == 5
        assert result.exclusions == {Exclusion.NO_PRICE_HISTORY: 1}
        assert period.exclusion_rate == pytest.approx(1 / 6)

    def test_a_name_with_too_little_history_is_excluded_with_its_own_reason(self) -> None:
        class ShortHistory(FakePrices):
            def __call__(self, symbol: str, *, start: date, end: date) -> Sequence[PitPrice]:
                bars = list(super().__call__(symbol, start=start, end=end))
                return bars[-3:] if symbol == "FFF" and end == FORMATION else bars

        result = _run(ShortHistory(DRIFTS))
        assert result.exclusions == {Exclusion.INSUFFICIENT_HISTORY: 1}

    def test_too_few_tradable_names_produces_no_period_rather_than_a_thin_one(self) -> None:
        """Three names cannot populate two quantiles of at least two names each."""
        result = _run(FakePrices({"AAA": 0.3, "BBB": 0.2, "CCC": 0.1}))
        assert result.periods == ()

    def test_a_skipped_formation_is_reported_and_its_exclusions_still_counted(self) -> None:
        """The silent-truncation guard.

        A date where most of the universe could not be priced must not surface as
        a clean run with nothing to say. The formation date is listed and the
        exclusions that caused it are still in the tally.
        """
        result = _run(FakePrices({"AAA": 0.3, "BBB": 0.2, "CCC": 0.1}))
        assert result.skipped_formations == (FORMATION,)
        assert result.exclusions == {Exclusion.NO_PRICE_HISTORY: 3}


class TestCosts:
    def test_cost_reduces_the_return_on_both_legs(self) -> None:
        period = _run(FakePrices(DRIFTS)).periods[0]
        assert period.net_return < period.gross_return
        assert period.cost > 0
        for position in period.positions:
            assert position.cost > 0, "a short pays the spread too"

    def test_a_bigger_book_pays_more_impact(self) -> None:
        """Capacity: the square-root term means size is not free."""
        small = _run(FakePrices(DRIFTS)).periods[0]
        large = run_quantile_sort(
            label="large",
            panels={FORMATION: _panel(SIGNALS), NEXT_FORMATION: _panel(SIGNALS)},
            load_prices=FakePrices(DRIFTS),
            n_quantiles=2,
            capital_usd=CAPITAL * 100,
        ).periods[0]
        assert large.cost > small.cost
        assert large.gross_return == pytest.approx(small.gross_return), (
            "capital must change cost only, never the gross price move"
        )

    def test_turnover_counts_both_sides_of_the_round_trip(self) -> None:
        period = _run(FakePrices(DRIFTS)).periods[0]
        assert period.turnover == pytest.approx(4.0)


class TestRefusals:
    def test_capital_is_required_to_be_positive(self) -> None:
        with pytest.raises(ValueError, match="capital_usd must be positive"):
            run_quantile_sort(
                label="test",
                panels={FORMATION: _panel(SIGNALS)},
                load_prices=FakePrices(DRIFTS),
                capital_usd=0.0,
            )

    def test_a_single_quantile_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            run_quantile_sort(
                label="test",
                panels={FORMATION: _panel(SIGNALS)},
                load_prices=FakePrices(DRIFTS),
                n_quantiles=1,
                capital_usd=CAPITAL,
            )

    def test_a_negative_lag_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            _run(FakePrices(DRIFTS), execution_lag_days=-1)

    def test_a_non_finite_signal_is_refused_at_construction(self) -> None:
        """A NaN that reaches a sort orders arbitrarily and never announces itself."""
        with pytest.raises(ValueError, match="refusing to rank"):
            SignalObservation(symbol="AAA", cik=1, value=math.nan, knowledge_date=FORMATION)

    def test_the_last_panel_has_no_exit_and_is_not_traded(self) -> None:
        """Stated behaviour, not an off-by-one to be discovered later."""
        result = _run(FakePrices(DRIFTS))
        assert len(result.periods) == 1
        assert result.periods[0].formation_date == FORMATION


class TestAnnualise:
    def test_compounding_is_geometric_not_arithmetic(self) -> None:
        """-50% then +50% is a loss. An arithmetic mean reports it as flat."""
        assert annualise([-0.5, 0.5], periods_per_year=1) == pytest.approx(math.sqrt(0.75) - 1)

    def test_total_loss_returns_minus_one_rather_than_a_complex_root(self) -> None:
        assert annualise([-1.0, 0.2], periods_per_year=1) == -1.0

    def test_an_empty_series_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no returns"):
            annualise([], periods_per_year=1)


class TestTheShortLegCostConvention:
    """`Position.net_return` is stored unsigned, and that is deliberate.

    Read alone, the field looks like a sign bug: a short position with a real
    round-trip cost reports a *higher* net_return than its gross_return. It is
    not. `gross_return` is the raw price move, unsigned by side, and the second
    sign flip arrives when the signed `weight` multiplies it -- so the aggregate
    is a plain signed-weight sum instead of a per-side special case.

    This class exists because an adversarial reviewer read the field in isolation,
    grepped for consumers, missed `RebalancePeriod.net_return`, and proposed
    "correcting" it to `sign * gross - cost`. Applying that would have inflated
    the book's return eightfold on the worked example below, in the exact code
    path the pending flagship study depends on. The convention needs a test that
    fails loudly, not a comment.
    """

    LONG_GROSS = 0.10
    LONG_COST = 0.01
    SHORT_GROSS = 0.05
    """The shorted name's price ROSE 5% -- a loss for the short."""
    SHORT_COST = 0.02

    def _position(self, *, symbol: str, weight: float, gross: float, cost: float) -> Position:
        sign = 1.0 if weight > 0 else -1.0
        return Position(
            symbol=symbol,
            weight=weight,
            signal_value=0.0,
            entry_date=FORMATION,
            exit_date=NEXT_FORMATION,
            entry_price=100.0,
            exit_price=100.0 * (1.0 + gross),
            gross_return=gross,
            cost=cost,
            net_return=gross - sign * cost,
            spread=SpreadEstimate(
                proportional_spread=cost / 2.0,
                raw_mean=cost / 2.0,
                n_pairs_used=1,
                n_pairs_negative=0,
                n_pairs_unusable=0,
            ),
            participation_rate=0.0,
        )

    def _period(self) -> RebalancePeriod:
        return RebalancePeriod(
            formation_date=FORMATION,
            entry_date=FORMATION,
            exit_date=NEXT_FORMATION,
            positions=(
                self._position(
                    symbol="LONG", weight=1.0, gross=self.LONG_GROSS, cost=self.LONG_COST
                ),
                self._position(
                    symbol="SHORT", weight=-1.0, gross=self.SHORT_GROSS, cost=self.SHORT_COST
                ),
            ),
            excluded=(),
            n_ranked=2,
        )

    def test_the_aggregate_matches_what_the_book_actually_earns(self) -> None:
        """The reference number, computed independently of the field convention.

        A long up 10% paying 1% earns 9%. A short whose name rose 5% and paid 2%
        loses 7%. One unit of capital on each side nets +2%.
        """
        earned = (self.LONG_GROSS - self.LONG_COST) + (-self.SHORT_GROSS - self.SHORT_COST)
        assert earned == pytest.approx(0.02)
        assert self._period().net_return == pytest.approx(earned)

    def test_the_short_leg_cost_convention_is_not_a_sign_bug(self) -> None:
        """The 'obvious correction' must not reproduce the reference number.

        Positive control for the test above: if `sign * gross - cost` also gave
        +2%, the first test would prove nothing about which convention is right.
        """
        naive = sum(
            # The proposed field, aggregated the way the code already aggregates:
            # weight * (sign * gross - cost).
            position.weight
            * (math.copysign(1.0, position.weight) * position.gross_return - position.cost)
            for position in self._period().positions
        )
        assert naive != pytest.approx(0.02)
        assert naive == pytest.approx(0.16)

    def test_cost_is_always_a_drag_once_the_weight_is_applied(self) -> None:
        """Whichever side it is on, cost reduces the book's return."""
        period = self._period()
        costless = sum(position.weight * position.gross_return for position in period.positions)
        assert period.net_return < costless
        assert period.cost == pytest.approx(self.LONG_COST + self.SHORT_COST)
