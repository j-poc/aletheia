"""Event-time cross-sectional backtest kernel.

A long/short quantile sort, rebalanced on a stated schedule, with every date the
simulation touches checked against when the information behind it became public.

**The two clocks.** Every observation carries two: the date its information became
knowable, and the date the trade could be executed. The kernel never lets the
second precede the first plus the stated execution lag. This is asserted at the
point of use rather than filtered at the point of query, because the failure mode
worth catching is a caller who assembled the panel wrongly -- and a filter written
by the same caller would contain the same mistake.

**What is deliberately absent.** No optimisation, no volatility targeting, no
position limits, no factor neutralisation. Equal weights across a quantile. Each
of those would be a defensible choice, and each is also a free parameter -- and a
free parameter tried and discarded is a trial that belongs in the ledger. The
kernel is kept parameter-poor so that a result from it is close to being a
property of the signal rather than of a search over portfolio construction.

**Names that cannot be traded are counted, never dropped quietly.** A symbol in
the signal panel with no usable price becomes a :class:`Exclusion` with a reason.
The count and the reasons are part of the result, because a backtest that silently
skips the names it could not price has measured something other than what it
claims -- and those names are disproportionately the ones that failed.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Final, Protocol

from aletheia.core.errors import AletheiaError
from aletheia.pit import PitPrice
from aletheia.research.costs import (
    DEFAULT_IMPACT_COEFFICIENT,
    SpreadEstimate,
    corwin_schultz_spread,
    trade_cost,
)

TRADING_DAYS_PER_YEAR: Final = 252
MIN_NAMES_PER_SIDE: Final = 2
SPREAD_WINDOW_DAYS: Final = 60
"""Lookback used to estimate spread and volatility at formation. Ends on the
formation date, so it uses only bars that had already printed."""
MIN_BARS_FOR_COSTS: Final = 20
"""Fewer than a month of bars gives spread and volatility estimates too noisy to
use, and a name with that little history at formation is usually a recent listing
rather than an established one."""


class LeakDetected(AletheiaError):
    """The simulation was handed information it could not have had.

    Distinct from :class:`~aletheia.core.errors.LookaheadViolation`, which the PIT
    layer raises about stored rows. This one is about the *simulation's* own
    arithmetic: a price used before it printed, or a signal used before it was
    filed.
    """


class Exclusion(StrEnum):
    """Why a name in the signal panel was not traded."""

    NO_PRICE_HISTORY = "no price history"
    NO_ENTRY_BAR = "no tradable bar at entry"
    NO_EXIT_BAR = "no tradable bar at exit"
    NON_POSITIVE_PRICE = "non-positive price"
    INSUFFICIENT_HISTORY = "too little history to estimate costs"


@dataclass(frozen=True, slots=True)
class SignalObservation:
    """One name's signal value, and the date the value became knowable."""

    symbol: str
    cik: int
    value: float
    knowledge_date: date

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(f"signal for {self.symbol} is {self.value}; refusing to rank on it")


class PriceLoader(Protocol):
    """Supplies daily bars. Implemented over the PIT layer in :mod:`aletheia.pit`.

    A protocol rather than a concrete class so the kernel can be exercised
    against a synthetic panel whose leak properties are known by construction.
    """

    def __call__(self, symbol: str, *, start: date, end: date) -> Sequence[PitPrice]: ...


@dataclass(frozen=True, slots=True)
class Position:
    """One name held over one rebalance period."""

    symbol: str
    weight: float
    """Signed. Positive is long."""
    signal_value: float
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    gross_return: float
    cost: float
    """Round-trip cost as a fraction of notional."""
    net_return: float
    spread: SpreadEstimate
    participation_rate: float


@dataclass(frozen=True, slots=True)
class RebalancePeriod:
    """One holding period from formation to the next rebalance."""

    formation_date: date
    entry_date: date
    exit_date: date
    positions: tuple[Position, ...]
    excluded: tuple[tuple[str, Exclusion], ...]
    n_ranked: int
    """Names that had a signal and a tradable price -- the population actually sorted."""

    @property
    def gross_return(self) -> float:
        return sum(position.weight * position.gross_return for position in self.positions)

    @property
    def net_return(self) -> float:
        return sum(position.weight * position.net_return for position in self.positions)

    @property
    def cost(self) -> float:
        return self.gross_return - self.net_return

    @property
    def turnover(self) -> float:
        """Gross notional traded per unit of capital, entering and exiting."""
        return 2.0 * sum(abs(position.weight) for position in self.positions)

    @property
    def exclusion_rate(self) -> float:
        """Share of the signal panel that could not be traded."""
        total = self.n_ranked + len(self.excluded)
        return len(self.excluded) / total if total else 0.0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Every period, plus the accounting that makes the return interpretable."""

    label: str
    periods: tuple[RebalancePeriod, ...]
    n_quantiles: int
    execution_lag_days: int
    capital_usd: float
    skipped_formations: tuple[date, ...] = ()
    """Formation dates where too few names could be priced to fill the quantiles.

    Reported rather than dropped: a study whose early years quietly produced no
    portfolio has a shorter sample than its date range advertises."""
    exclusions: dict[Exclusion, int] = field(default_factory=dict)

    @property
    def gross_returns(self) -> tuple[float, ...]:
        return tuple(period.gross_return for period in self.periods)

    @property
    def net_returns(self) -> tuple[float, ...]:
        return tuple(period.net_return for period in self.periods)

    @property
    def mean_turnover(self) -> float:
        return statistics.fmean(period.turnover for period in self.periods) if self.periods else 0.0

    @property
    def total_excluded(self) -> int:
        return sum(self.exclusions.values())

    def explain(self) -> str:
        if not self.periods:
            return f"{self.label}: no periods produced a portfolio"
        gross = statistics.fmean(self.gross_returns)
        net = statistics.fmean(self.net_returns)
        return (
            f"{self.label}: {len(self.periods)} periods, mean gross {gross:+.2%} "
            f"net {net:+.2%} per period, turnover {self.mean_turnover:.2f}x, "
            f"{self.total_excluded} name-periods excluded"
        )


def run_quantile_sort(
    *,
    label: str,
    panels: dict[date, Sequence[SignalObservation]],
    load_prices: PriceLoader,
    n_quantiles: int = 5,
    execution_lag_days: int = 1,
    capital_usd: float,
    long_high: bool = False,
    impact_coefficient: float = DEFAULT_IMPACT_COEFFICIENT,
) -> BacktestResult:
    """Sort each panel into quantiles, hold the extremes until the next panel.

    ``panels`` maps a formation date to the signals available on it. Consecutive
    formation dates define the holding periods; the last panel has no exit and is
    not traded, which is stated here rather than silently producing one fewer
    period than the caller expected.

    ``long_high`` is ``False`` by default because the signals this system was
    built for -- accruals, revision magnitude -- predict *negative* subsequent
    returns. Flipping it is a legitimate hypothesis and a separate trial.

    ``capital_usd`` is required: participation rate, and therefore market impact,
    is meaningless without a size. A backtest that does not state the capital it
    assumes has not modelled cost.
    """
    if n_quantiles < 2:
        raise ValueError("n_quantiles must be at least 2 for there to be extremes to trade")
    if execution_lag_days < 0:
        raise ValueError("execution_lag_days cannot be negative")
    if capital_usd <= 0:
        raise ValueError("capital_usd must be positive; impact cost scales with position size")

    formation_dates = sorted(panels)
    periods: list[RebalancePeriod] = []
    skipped: list[date] = []
    exclusions: dict[Exclusion, int] = {}

    for formation, exit_formation in pairwise(formation_dates):
        period = _run_one_period(
            formation=formation,
            exit_formation=exit_formation,
            observations=panels[formation],
            load_prices=load_prices,
            n_quantiles=n_quantiles,
            execution_lag_days=execution_lag_days,
            capital_usd=capital_usd,
            long_high=long_high,
            impact_coefficient=impact_coefficient,
        )
        # Exclusions are accumulated even when the period was too thin to trade.
        # Discarding them would report a clean run for a date on which most of the
        # universe could not be priced -- the exact shape of a silent truncation.
        for _, reason in period.excluded:
            exclusions[reason] = exclusions.get(reason, 0) + 1
        if period.positions:
            periods.append(period)
        else:
            skipped.append(formation)

    return BacktestResult(
        label=label,
        periods=tuple(periods),
        skipped_formations=tuple(skipped),
        n_quantiles=n_quantiles,
        execution_lag_days=execution_lag_days,
        capital_usd=capital_usd,
        exclusions=exclusions,
    )


def _run_one_period(
    *,
    formation: date,
    exit_formation: date,
    observations: Sequence[SignalObservation],
    load_prices: PriceLoader,
    n_quantiles: int,
    execution_lag_days: int,
    capital_usd: float,
    long_high: bool,
    impact_coefficient: float,
) -> RebalancePeriod:
    tradable: list[tuple[SignalObservation, _Quote]] = []
    excluded: list[tuple[str, Exclusion]] = []

    for observation in observations:
        # Canary 1: the signal itself. A panel assembled with a wrong date filter
        # would pass its own filter; it does not pass this.
        if observation.knowledge_date > formation:
            raise LeakDetected(
                f"signal for {observation.symbol} became knowable on "
                f"{observation.knowledge_date}, after the formation date {formation}"
            )
        quote = _quote(
            observation.symbol,
            load_prices=load_prices,
            formation=formation,
            exit_formation=exit_formation,
            execution_lag_days=execution_lag_days,
        )
        if isinstance(quote, Exclusion):
            excluded.append((observation.symbol, quote))
        else:
            tradable.append((observation, quote))

    if len(tradable) < n_quantiles * MIN_NAMES_PER_SIDE:
        # Too thin to fill the quantiles. Returned as a period with no positions
        # rather than as None, so the exclusions that caused it are still counted.
        return RebalancePeriod(
            formation_date=formation,
            entry_date=formation,
            exit_date=exit_formation,
            positions=(),
            excluded=tuple(excluded),
            n_ranked=len(tradable),
        )

    # Ties break on the symbol, not on input order. Python's sort is stable, so
    # sorting on the value alone would let two firms with identical accruals land
    # on either side of a quantile boundary depending on the order the caller
    # happened to build the panel in -- and a panel built by iterating a set is
    # ordered differently in every process. Adding the symbol makes the sort a
    # total order, so the result depends on the data and nothing else.
    tradable.sort(key=lambda pair: (pair[0].value, pair[0].symbol))
    bucket = len(tradable) // n_quantiles
    low = tradable[:bucket]
    high = tradable[-bucket:]
    long_side, short_side = (high, low) if long_high else (low, high)

    positions: list[Position] = []
    for side, sign in ((long_side, 1.0), (short_side, -1.0)):
        weight = sign / len(side)
        for observation, quote in side:
            notional = abs(weight) * capital_usd
            participation = (
                notional / quote.dollar_volume if quote.dollar_volume > 0 else float("inf")
            )
            cost = trade_cost(
                spread=quote.spread,
                participation_rate=min(participation, 1.0),
                daily_volatility=quote.daily_volatility,
                coefficient=impact_coefficient,
            )
            gross = quote.exit_price / quote.entry_price - 1.0
            # Round trip: cost is paid entering and exiting. The signed return of a
            # short is the negative of the price move, but the cost is always paid.
            round_trip = 2.0 * cost.total
            positions.append(
                Position(
                    symbol=observation.symbol,
                    weight=weight,
                    signal_value=observation.value,
                    entry_date=quote.entry_date,
                    exit_date=quote.exit_date,
                    entry_price=quote.entry_price,
                    exit_price=quote.exit_price,
                    gross_return=gross,
                    cost=round_trip,
                    net_return=gross - sign * round_trip,
                    spread=quote.spread,
                    participation_rate=participation,
                )
            )

    entry_dates = [position.entry_date for position in positions]
    exit_dates = [position.exit_date for position in positions]
    return RebalancePeriod(
        formation_date=formation,
        entry_date=min(entry_dates),
        exit_date=max(exit_dates),
        positions=tuple(positions),
        excluded=tuple(excluded),
        n_ranked=len(tradable),
    )


@dataclass(frozen=True, slots=True)
class _Quote:
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    spread: SpreadEstimate
    daily_volatility: float
    dollar_volume: float


def _quote(
    symbol: str,
    *,
    load_prices: PriceLoader,
    formation: date,
    exit_formation: date,
    execution_lag_days: int,
) -> _Quote | Exclusion:
    """Entry and exit marks, plus the cost inputs, or the reason there are none."""
    window_start = date.fromordinal(formation.toordinal() - SPREAD_WINDOW_DAYS)
    history = list(load_prices(symbol, start=window_start, end=formation))
    forward = list(load_prices(symbol, start=formation, end=exit_formation))
    if not forward:
        return Exclusion.NO_PRICE_HISTORY
    if len(history) < MIN_BARS_FOR_COSTS:
        return Exclusion.INSUFFICIENT_HISTORY

    # Canary 2: the cost window must not contain a bar that had not printed by
    # formation. Estimating a spread from future ranges is a leak that changes
    # nothing about the return but flatters the cost.
    for bar in history:
        if bar.bar_date > formation:
            raise LeakDetected(
                f"cost window for {symbol} contains a bar dated {bar.bar_date}, "
                f"after the formation date {formation}"
            )

    entry_bar = _first_tradable(forward, on_or_after=formation, lag=execution_lag_days)
    if entry_bar is None:
        return Exclusion.NO_ENTRY_BAR
    exit_forward = list(load_prices(symbol, start=exit_formation, end=_far_future(exit_formation)))
    exit_bar = _first_tradable(exit_forward, on_or_after=exit_formation, lag=execution_lag_days)
    if exit_bar is None:
        return Exclusion.NO_EXIT_BAR

    entry_price = _mark(entry_bar)
    exit_price = _mark(exit_bar)
    if entry_price <= 0 or exit_price <= 0:
        return Exclusion.NON_POSITIVE_PRICE

    # Canary 3: the trade itself. The lag is enforced by _first_tradable when the
    # entry bar is chosen; this re-asserts it as a post-condition so a future
    # change to that selection cannot quietly reintroduce same-close execution.
    # The exit must also come after the entry -- a zero-length hold would report a
    # spurious zero return, which is reachable whenever the next formation date
    # falls on the entry bar.
    if (entry_bar.bar_date - formation).days < execution_lag_days:
        raise LeakDetected(
            f"entry bar for {symbol} is dated {entry_bar.bar_date}, less than "
            f"{execution_lag_days} day(s) after formation {formation}"
        )
    if exit_bar.bar_date <= entry_bar.bar_date:
        raise LeakDetected(
            f"exit bar for {symbol} ({exit_bar.bar_date}) does not follow the entry "
            f"bar ({entry_bar.bar_date})"
        )

    return _Quote(
        entry_date=entry_bar.bar_date,
        exit_date=exit_bar.bar_date,
        entry_price=entry_price,
        exit_price=exit_price,
        spread=corwin_schultz_spread(history),
        daily_volatility=_daily_volatility(history),
        dollar_volume=_median_dollar_volume(history),
    )


def _first_tradable(bars: Sequence[PitPrice], *, on_or_after: date, lag: int) -> PitPrice | None:
    threshold = date.fromordinal(on_or_after.toordinal() + lag)
    for bar in bars:
        if bar.bar_date >= threshold:
            return bar
    return None


def _mark(bar: PitPrice) -> float:
    """The price a position is marked at.

    Dividend-adjusted close when the vendor supplies one. Mixing an unadjusted
    entry with an adjusted exit produces a fabricated return on every split, an
    error far larger than the open-versus-close nuance it would buy.
    """
    return bar.adj_close if bar.adj_close is not None else bar.close


def _daily_volatility(bars: Sequence[PitPrice]) -> float:
    marks = [_mark(bar) for bar in bars]
    returns = [
        math.log(later / earlier) for earlier, later in pairwise(marks) if earlier > 0 and later > 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns)


def _median_dollar_volume(bars: Sequence[PitPrice]) -> float:
    volumes = [_mark(bar) * bar.volume for bar in bars if bar.volume > 0]
    return statistics.median(volumes) if volumes else 0.0


def _far_future(anchor: date) -> date:
    """Enough forward window to find the next tradable bar across a long holiday."""
    return date.fromordinal(anchor.toordinal() + 30)


def annualise(returns: Sequence[float], *, periods_per_year: float) -> float:
    """Geometric annual growth rate implied by a series of period returns.

    Geometric, not arithmetic: compounding -50% and +50% is a loss, and an
    arithmetic mean reports it as flat.
    """
    if not returns:
        raise ValueError("no returns to annualise")
    growth = 1.0
    for value in returns:
        growth *= 1.0 + value
    if growth <= 0:
        return -1.0
    return float(growth ** (periods_per_year / len(returns))) - 1.0


def as_callable_loader(fetch: Callable[[str, date, date], Sequence[PitPrice]]) -> PriceLoader:
    """Adapt a positional-argument function to the :class:`PriceLoader` protocol."""

    def loader(symbol: str, *, start: date, end: date) -> Sequence[PitPrice]:
        return fetch(symbol, start, end)

    return loader
