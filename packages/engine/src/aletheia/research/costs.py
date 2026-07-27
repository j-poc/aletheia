"""What trading the signal would have cost.

A gross return is not a result. The accrual anomaly is a cross-sectional sort
that rebalances annually across hundreds of names, and the difference between its
gross and net performance is the difference between a finding and a fee.

Two components, because they answer different questions:

* **Spread** -- what you pay to cross the market on a normal-sized order. Estimated
  from the daily high-low range using Corwin & Schultz (2012), because a
  historical quoted spread is not obtainable from free daily data. The estimator
  exploits the fact that the high-low *range* contains both volatility, which
  scales with the length of the interval, and the spread, which does not; two
  consecutive days therefore separate them.
* **Impact** -- what you additionally pay for being large relative to the volume
  available. Modelled as a square root of participation rate, the functional form
  that has survived repeated empirical scrutiny (Almgren et al. 2005; Torre &
  Ferrari 1997) and which follows from an order book whose depth grows linearly
  with distance from the mid.

Both are *estimates from public daily data*, and are labelled as such. A fund with
its own execution record would replace them with measured slippage. The purpose
here is not a precise cost figure -- it is that no return in this system is ever
reported without one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from aletheia.pit import PitPrice

MIN_BARS_FOR_SPREAD: Final = 2
DEFAULT_IMPACT_COEFFICIENT: Final = 0.1
"""Almgren et al. (2005) estimate the coefficient on the square-root term at
roughly 0.1 for US equities when impact is expressed in units of daily
volatility. Reported alongside every result so it can be argued with."""


@dataclass(frozen=True, slots=True)
class SpreadEstimate:
    """A round-trip spread estimate with the sample behind it."""

    proportional_spread: float
    """Estimated bid-ask spread as a fraction of price. Round trip costs this once."""
    raw_mean: float
    """Mean of the per-pair estimates before the floor at zero. May be negative."""
    n_pairs_used: int
    n_pairs_negative: int
    """How many per-pair estimates came out negative. A noise diagnostic: a high
    share means the range is nearly all volatility and the estimate is weak."""
    n_pairs_unusable: int
    """Pairs skipped for a non-positive or non-finite price, not for being negative."""

    @property
    def half_spread(self) -> float:
        """Cost of a single crossing: half the quoted spread."""
        return self.proportional_spread / 2.0

    @property
    def is_informative(self) -> bool:
        """False when there was nothing usable to average. Not the same as free."""
        return self.n_pairs_used > 0


@dataclass(frozen=True, slots=True)
class TradeCost:
    """The modelled cost of one trade, as a fraction of notional."""

    total: float
    spread_cost: float
    impact_cost: float
    participation_rate: float
    """Order size as a fraction of the day's volume."""

    def explain(self) -> str:
        return (
            f"{self.total * 1e4:.1f} bp = {self.spread_cost * 1e4:.1f} bp half-spread "
            f"+ {self.impact_cost * 1e4:.1f} bp impact at "
            f"{self.participation_rate:.2%} of daily volume"
        )


def corwin_schultz_spread(bars: Sequence[PitPrice]) -> SpreadEstimate:
    """Estimate the proportional bid-ask spread from daily high-low ranges.

    Corwin & Schultz (2012). For each pair of consecutive days, the sum of the two
    single-day log ranges and the log range over the two days combined imply the
    spread, because volatility scales with time and the spread does not.

    **Negative per-pair estimates are kept in the average, not dropped.** They occur
    constantly -- the estimator infers a spread from a range that is mostly
    volatility, so noise pushes individual pairs either way. Dropping them (or
    clipping each to zero) truncates only the low tail and biases the mean upward:
    measured on a simulated path with *no* spread at all, discarding negatives
    reports 128 basis points out of nothing. Averaging the raw estimates and
    flooring the *mean* at zero, which is what Corwin & Schultz prescribe, reports
    approximately zero on the same path. ``n_pairs_negative`` is returned as the
    noise diagnostic that the discard count used to be.

    ``n_pairs_used == 0`` means the estimate is unknown. A caller must not read
    that as free.
    """
    if len(bars) < MIN_BARS_FOR_SPREAD:
        raise ValueError(
            f"need at least {MIN_BARS_FOR_SPREAD} consecutive bars to estimate a spread; "
            f"got {len(bars)}"
        )

    estimates: list[float] = []
    unusable = 0
    for first, second in pairwise(bars):
        if min(first.low, second.low) <= 0 or min(first.high, second.high) <= 0:
            unusable += 1
            continue
        # Single-day log ranges, and the range over the two days taken together.
        beta = math.log(first.high / first.low) ** 2 + math.log(second.high / second.low) ** 2
        high = max(first.high, second.high)
        low = min(first.low, second.low)
        gamma = math.log(high / low) ** 2

        denominator = 3.0 - 2.0 * math.sqrt(2.0)
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / denominator - math.sqrt(
            gamma / denominator
        )
        spread = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        if not math.isfinite(spread):
            unusable += 1
            continue
        estimates.append(spread)

    if not estimates:
        return SpreadEstimate(
            proportional_spread=0.0,
            raw_mean=0.0,
            n_pairs_used=0,
            n_pairs_negative=0,
            n_pairs_unusable=unusable,
        )
    raw_mean = sum(estimates) / len(estimates)
    return SpreadEstimate(
        # Floor the mean, not the individual estimates -- see the docstring.
        proportional_spread=max(0.0, raw_mean),
        raw_mean=raw_mean,
        n_pairs_used=len(estimates),
        n_pairs_negative=sum(1 for value in estimates if value < 0),
        n_pairs_unusable=unusable,
    )


def square_root_impact(
    *,
    participation_rate: float,
    daily_volatility: float,
    coefficient: float = DEFAULT_IMPACT_COEFFICIENT,
) -> float:
    """Market impact as ``coefficient * volatility * sqrt(participation)``.

    ``participation_rate`` is order size over the day's volume. The square root,
    rather than a linear term, is what makes capacity finite but not brutal:
    trading ten times the size costs about three times as much per share, so a
    strategy does not simply scale.
    """
    if participation_rate < 0:
        raise ValueError("participation_rate cannot be negative")
    if daily_volatility < 0:
        raise ValueError("daily_volatility cannot be negative")
    return coefficient * daily_volatility * math.sqrt(participation_rate)


def trade_cost(
    *,
    spread: SpreadEstimate,
    participation_rate: float,
    daily_volatility: float,
    coefficient: float = DEFAULT_IMPACT_COEFFICIENT,
) -> TradeCost:
    """Total one-way cost of a trade as a fraction of its notional."""
    spread_cost = spread.half_spread
    impact = square_root_impact(
        participation_rate=participation_rate,
        daily_volatility=daily_volatility,
        coefficient=coefficient,
    )
    return TradeCost(
        total=spread_cost + impact,
        spread_cost=spread_cost,
        impact_cost=impact,
        participation_rate=participation_rate,
    )
