"""The cost model, checked against ground truth rather than against itself.

The Corwin-Schultz estimator is validated by simulation: a true price path is
generated, a *known* spread is imposed on it by widening the observed high and
low, and the estimator must recover that spread. This catches an error in the
published formula as readily as an error in the transcription of it, which
comparing against a number copied out of the paper would not.

Every simulation is seeded, so a failure is reproducible rather than occasional.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import pairwise

import numpy as np
import pytest

from aletheia.pit import PitPrice
from aletheia.research.costs import (
    corwin_schultz_spread,
    square_root_impact,
    trade_cost,
)

SEED = 20260727
START = date(2016, 1, 4)
DAILY_VOL = 0.02


def _positive_only_mean(bars: list[PitPrice]) -> float:
    """The biased estimator, reimplemented here purely so the bias can be shown.

    Not exported: this is what the library deliberately does *not* do.
    """
    values = []
    for first, second in pairwise(bars):
        beta = math.log(first.high / first.low) ** 2 + math.log(second.high / second.low) ** 2
        gamma = math.log(max(first.high, second.high) / min(first.low, second.low)) ** 2
        denominator = 3.0 - 2.0 * math.sqrt(2.0)
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / denominator - math.sqrt(
            gamma / denominator
        )
        spread = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        if spread > 0:
            values.append(spread)
    return sum(values) / len(values) if values else 0.0


def _flat_bars(level: float, n_days: int = 50) -> list[PitPrice]:
    """Bars with no intraday range whatsoever, at a given price level."""
    return [
        PitPrice(
            symbol="FLAT",
            bar_date=START + timedelta(days=index),
            open=level,
            high=level,
            low=level,
            close=level,
            adj_close=level,
            volume=1.0,
            tradable_from=START + timedelta(days=index),
        )
        for index in range(n_days)
    ]


def _simulate(*, true_spread: float, n_days: int = 2000, seed: int = SEED) -> list[PitPrice]:
    """A price path with a known proportional spread imposed on the quotes.

    The efficient price follows a random walk. Within each day it wanders, giving a
    true high and low; the observed quotes are then the true extremes widened by
    half the spread on each side, which is what a trader actually sees.
    """
    rng = np.random.default_rng(seed)
    bars: list[PitPrice] = []
    level = 100.0
    for index in range(n_days):
        steps = level * np.exp(np.cumsum(rng.normal(0.0, DAILY_VOL / 8.0, size=64)))
        true_high, true_low, close = float(steps.max()), float(steps.min()), float(steps[-1])
        bars.append(
            PitPrice(
                symbol="SIM",
                bar_date=START + timedelta(days=index),
                open=level,
                high=true_high * (1.0 + true_spread / 2.0),
                low=true_low * (1.0 - true_spread / 2.0),
                close=close,
                adj_close=close,
                volume=1_000_000.0,
                tradable_from=START + timedelta(days=index),
            )
        )
        level = close
    return bars


class TestCorwinSchultzAgainstKnownGroundTruth:
    @pytest.mark.parametrize("true_spread", [0.002, 0.005, 0.010, 0.020])
    def test_it_recovers_an_imposed_spread(self, true_spread: float) -> None:
        """The estimate must land near the spread that was actually imposed.

        The tolerance is wide (half the true value) because the estimator is noisy
        by construction -- it infers a spread from a range that is mostly
        volatility. A tighter bar would fail on sampling noise rather than on a
        defect, which is a worse test, not a stricter one.
        """
        estimate = corwin_schultz_spread(_simulate(true_spread=true_spread))
        assert estimate.proportional_spread == pytest.approx(true_spread, rel=0.5), (
            f"imposed {true_spread:.4f}, estimated {estimate.proportional_spread:.4f}"
        )

    def test_a_wider_spread_reads_wider(self) -> None:
        """Monotonicity: the ordering must survive even where the level is noisy."""
        estimates = [
            corwin_schultz_spread(_simulate(true_spread=spread)).proportional_spread
            for spread in (0.001, 0.005, 0.020)
        ]
        assert estimates == sorted(estimates)

    def test_a_zero_spread_path_reads_near_zero(self) -> None:
        """Negative control: with no spread imposed, there is nothing to find.

        Most pairs produce a negative raw estimate here, which is the expected
        behaviour of the estimator on a spreadless series.
        """
        estimate = corwin_schultz_spread(_simulate(true_spread=0.0))
        assert estimate.proportional_spread < 0.002
        assert estimate.n_pairs_negative > estimate.n_pairs_used / 3, (
            "a spreadless series should produce negative estimates constantly"
        )


class TestNegativeEstimatesStayInTheAverage:
    def test_dropping_negatives_would_manufacture_a_spread_from_nothing(self) -> None:
        """The bug this treatment exists to avoid, measured rather than asserted.

        On a path with no spread imposed, averaging only the positive per-pair
        estimates reports a large spread out of pure noise. Averaging all of them
        reports approximately nothing, which is the truth.
        """
        bars = _simulate(true_spread=0.0)
        estimate = corwin_schultz_spread(bars)
        assert estimate.n_pairs_negative > 0

        positives_only = _positive_only_mean(bars)
        assert positives_only > 0.010, "the biased estimator really is badly biased here"
        assert estimate.proportional_spread < positives_only / 5

    def test_the_raw_mean_is_reported_alongside_the_floored_one(self) -> None:
        """The floor is presentation; the unfloored mean stays visible."""
        estimate = corwin_schultz_spread(_simulate(true_spread=0.0))
        assert estimate.raw_mean < 0
        assert estimate.proportional_spread == 0.0
        assert not estimate.is_informative or estimate.n_pairs_used > 0

    def test_a_series_with_no_range_at_all_reads_zero_and_says_so_confidently(self) -> None:
        """A degenerate but real answer: no range means no spread.

        Distinct from the unknown case below. A price that never moves within the
        day genuinely has nothing for the estimator to attribute to a spread, and
        reporting zero with a full sample behind it is correct, not a failure.
        """
        estimate = corwin_schultz_spread(_flat_bars(100.0))
        assert estimate.proportional_spread == 0.0
        assert estimate.n_pairs_used == 49
        assert estimate.is_informative

    def test_non_positive_prices_leave_the_estimate_unknown_not_free(self) -> None:
        """``n_pairs_used == 0`` means unknown. A caller must not read it as free."""
        estimate = corwin_schultz_spread(_flat_bars(0.0))
        assert estimate.proportional_spread == 0.0
        assert estimate.n_pairs_used == 0
        assert estimate.n_pairs_unusable == 49
        assert not estimate.is_informative

    def test_non_positive_prices_are_discarded_rather_than_raising(self) -> None:
        bars = _simulate(true_spread=0.005, n_days=40)
        broken = [
            PitPrice(
                symbol=bar.symbol,
                bar_date=bar.bar_date,
                open=bar.open,
                high=bar.high,
                low=0.0 if index == 10 else bar.low,
                close=bar.close,
                adj_close=bar.adj_close,
                volume=bar.volume,
                tradable_from=bar.tradable_from,
            )
            for index, bar in enumerate(bars)
        ]
        estimate = corwin_schultz_spread(broken)
        assert estimate.n_pairs_unusable >= 2, "both pairs touching the bad bar"

    def test_too_few_bars_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            corwin_schultz_spread(_simulate(true_spread=0.005, n_days=1))


class TestSquareRootImpact:
    def test_quadrupling_size_doubles_impact(self) -> None:
        """The defining property: cost per share grows as the square root of size."""
        small = square_root_impact(participation_rate=0.01, daily_volatility=DAILY_VOL)
        large = square_root_impact(participation_rate=0.04, daily_volatility=DAILY_VOL)
        assert large == pytest.approx(2.0 * small)

    def test_impact_scales_linearly_in_volatility(self) -> None:
        base = square_root_impact(participation_rate=0.01, daily_volatility=0.01)
        double = square_root_impact(participation_rate=0.01, daily_volatility=0.02)
        assert double == pytest.approx(2.0 * base)

    def test_zero_size_is_free(self) -> None:
        assert square_root_impact(participation_rate=0.0, daily_volatility=DAILY_VOL) == 0.0

    def test_the_magnitude_is_plausible_for_a_realistic_order(self) -> None:
        """1% of a day's volume in a 2%-vol name: single-digit basis points.

        Not a claim of precision -- a check that the units are right. An impact
        model that returned 5% here would be wrong by two orders of magnitude and
        would silently kill any strategy run through it.
        """
        impact = square_root_impact(participation_rate=0.01, daily_volatility=DAILY_VOL)
        assert 1e-4 < impact < 1e-3

    def test_negative_inputs_are_refused(self) -> None:
        with pytest.raises(ValueError, match="participation_rate"):
            square_root_impact(participation_rate=-0.1, daily_volatility=DAILY_VOL)
        with pytest.raises(ValueError, match="daily_volatility"):
            square_root_impact(participation_rate=0.1, daily_volatility=-1.0)


class TestTradeCost:
    def test_total_is_the_half_spread_plus_impact(self) -> None:
        spread = corwin_schultz_spread(_simulate(true_spread=0.005))
        cost = trade_cost(spread=spread, participation_rate=0.01, daily_volatility=DAILY_VOL)
        assert cost.total == pytest.approx(cost.spread_cost + cost.impact_cost)
        assert cost.spread_cost == pytest.approx(spread.proportional_spread / 2.0)

    def test_crossing_costs_half_the_quoted_spread_not_all_of_it(self) -> None:
        """A round trip pays the full spread; each single crossing pays half."""
        spread = corwin_schultz_spread(_simulate(true_spread=0.005))
        assert spread.half_spread == pytest.approx(spread.proportional_spread / 2.0)

    def test_the_explanation_states_the_arithmetic(self) -> None:
        spread = corwin_schultz_spread(_simulate(true_spread=0.005))
        text = trade_cost(
            spread=spread, participation_rate=0.01, daily_volatility=DAILY_VOL
        ).explain()
        assert "bp" in text
        assert "daily volume" in text

    def test_impact_grows_with_participation_within_a_full_cost(self) -> None:
        spread = corwin_schultz_spread(_simulate(true_spread=0.005))
        cheap = trade_cost(spread=spread, participation_rate=0.001, daily_volatility=DAILY_VOL)
        dear = trade_cost(spread=spread, participation_rate=0.10, daily_volatility=DAILY_VOL)
        assert dear.total > cheap.total
        assert dear.spread_cost == cheap.spread_cost, "size must not move the spread term"
        assert dear.impact_cost / cheap.impact_cost == pytest.approx(math.sqrt(100.0))
