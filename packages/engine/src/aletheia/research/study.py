"""Building signal panels that are comparable across data vintages.

The whole design rests on one requirement: **the arms must differ only in the
data-vintage policy.** Same firms, same rebalance dates, same price panel. If the
restated arm quietly trades a slightly different universe -- because a restatement
happened to make one more firm computable -- then the difference between the arms
measures that, and not the thing it claims to measure.

So a firm-period enters the study only if *every* arm can compute it, and the
whole firm-period is dropped from every arm otherwise. The count of such drops is
reported: a decomposition that silently kept different universes per arm would be
worse than no decomposition at all.

Two known limitations, both of which cancel in the difference and neither of which
cancels in the level:

* **Prices for delisted names are unobtainable** (decision D1). Those firms are
  excluded from every arm identically.
* **The ticker map is a current snapshot**, not a historical one, so a firm that
  changed ticker resolves to its present symbol. Same map in every arm.

Neither excuses a claim about the *level* of the accrual premium measured here.
The claim this module supports is about the *difference between arms*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Final

from aletheia.core.errors import AletheiaError, InsufficientData
from aletheia.core.types import Cik
from aletheia.features.accruals import Accruals, accruals, annual_periods
from aletheia.features.vintage import Vintage
from aletheia.pit import PitView
from aletheia.research.kernel import SignalObservation

MIN_REPORTING_LAG_DAYS: Final = 0
"""No artificial delay is added beyond the filing date itself.

Some studies impose a fixed lag (three or six months after fiscal year end) to
approximate when data was available. That approximation is unnecessary here: the
actual filing date is known, so it is used. The naive arm's period-end dating is
the *deliberate* exception, and it is the thing being measured."""


class Drop(StrEnum):
    """Why a firm-period never reached any arm."""

    NO_ANNUAL_PERIOD = "no annual period available"
    NO_PRIOR_YEAR = "no preceding fiscal year for the opening balance sheet"
    NOT_YET_FILED = "not filed by the formation date"
    MISSING_INPUT = "an accrual input was never tagged"
    NO_TICKER = "no ticker symbol maps to this registrant"
    SECTOR_EXCLUDED = "financial or utility"


@dataclass(frozen=True, slots=True)
class FirmPeriod:
    """One firm at one formation date, with each arm's chosen fiscal year.

    The arms may sit on **different** fiscal years, and that is the point. A
    period-indexed vendor panel hands you FY2015 on 2015-12-31, seven weeks before
    the 10-K exists; over those seven weeks the honest arm is still reading FY2014.
    That gap is the timing channel. Forcing every arm onto the same fiscal year --
    which an earlier version of this module did -- makes the naive arm identical to
    the restated arm and the timing channel measure exactly zero.
    """

    cik: Cik
    symbol: str
    by_vintage: dict[str, Accruals]

    def period_end(self, vintage: str) -> date:
        return self.by_vintage[vintage].period_end

    def observation(self, vintage: str) -> SignalObservation:
        result = self.by_vintage[vintage]
        return SignalObservation(
            symbol=self.symbol,
            cik=int(self.cik),
            value=result.accruals,
            knowledge_date=result.knowledge_date,
        )


@dataclass
class PanelReport:
    """What entered the study and what did not."""

    formation_dates: list[date] = field(default_factory=list)
    kept: int = 0
    drops: dict[Drop, int] = field(default_factory=dict)
    firms_seen: set[int] = field(default_factory=set)
    firms_kept: set[int] = field(default_factory=set)

    def drop(self, reason: Drop) -> None:
        self.drops[reason] = self.drops.get(reason, 0) + 1

    @property
    def total_dropped(self) -> int:
        return sum(self.drops.values())

    def explain(self) -> str:
        lines = [
            f"{self.kept:,} firm-periods kept across {len(self.formation_dates)} formation dates "
            f"({len(self.firms_kept):,} of {len(self.firms_seen):,} firms contributed)",
            f"{self.total_dropped:,} firm-periods dropped:",
        ]
        lines.extend(
            f"    {count:>7,}  {reason.value}"
            for reason, count in sorted(self.drops.items(), key=lambda item: -item[1])
        )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Panels:
    """One signal panel per vintage, over an identical universe."""

    by_vintage: dict[str, dict[date, list[SignalObservation]]]
    report: PanelReport

    def assert_universes_match(self) -> None:
        """The invariant the whole comparison rests on.

        Checked rather than assumed. A silent divergence here would look like a
        finding about data vintages when it was a finding about coverage.
        """
        names = list(self.by_vintage)
        if len(names) < 2:
            return
        reference = {
            formation: sorted(observation.symbol for observation in observations)
            for formation, observations in self.by_vintage[names[0]].items()
        }
        for name in names[1:]:
            other = {
                formation: sorted(observation.symbol for observation in observations)
                for formation, observations in self.by_vintage[name].items()
            }
            if other != reference:
                raise AletheiaError(
                    f"vintage arms '{names[0]}' and '{name}' cover different universes; "
                    f"any difference between them would be uninterpretable"
                )


def build_panels(
    view: PitView,
    *,
    ciks: Sequence[Cik | int],
    formation_dates: Sequence[date],
    vintages: Sequence[Vintage],
    exclude_financials: bool = True,
    exclude_utilities: bool = True,
) -> Panels:
    """Compute every arm's signal over one shared universe.

    ``view`` must be opened at the data vintage -- the latest date the warehouse
    knows about -- because the naive arm needs to reach values that were not public
    at formation. Point-in-time discipline for the honest arms is enforced inside
    :meth:`Vintage.resolve`, which calls the guarded ``first_reported``, and again
    by the kernel, which re-checks every observation's knowledge date against the
    formation date it is used on.
    """
    report = PanelReport(formation_dates=list(formation_dates))
    panels: dict[str, dict[date, list[SignalObservation]]] = {
        vintage.name: {formation: [] for formation in formation_dates} for vintage in vintages
    }

    resolved = _resolve_symbols(
        view,
        ciks,
        report=report,
        exclude_financials=exclude_financials,
        exclude_utilities=exclude_utilities,
    )

    for cik, symbol in resolved:
        report.firms_seen.add(int(cik))
        # Computed once per firm across every fiscal year and every arm, then
        # *selected* per formation date. Recomputing at each of ~174 monthly
        # formation dates would repeat the same arithmetic twelve times per fiscal
        # year for no change in the answer.
        series = _firm_series(view, cik, vintages=vintages, report=report)
        if series is None:
            continue

        for formation in formation_dates:
            picked = _pick_per_arm(series, formation=formation, vintages=vintages)
            if picked is None:
                report.drop(Drop.NOT_YET_FILED)
                continue
            firm_period = FirmPeriod(cik=Cik(int(cik)), symbol=symbol, by_vintage=picked)
            report.kept += 1
            report.firms_kept.add(int(cik))
            for vintage in vintages:
                panels[vintage.name][formation].append(firm_period.observation(vintage.name))

    result = Panels(by_vintage=panels, report=report)
    result.assert_universes_match()
    return result


def _firm_series(
    view: PitView,
    cik: Cik | int,
    *,
    vintages: Sequence[Vintage],
    report: PanelReport,
) -> dict[str, list[Accruals]] | None:
    """Every fiscal year this firm reported, under every arm.

    Computed at the data vintage, so nothing here is filtered by a formation date.
    Point-in-time discipline is applied afterwards by :func:`_pick_per_arm`, which
    selects on each arm's own knowledge date, and again by the kernel, which
    re-checks every observation it is handed.
    """
    periods = annual_periods(view, cik)
    if len(periods) < 2:
        report.drop(Drop.NO_ANNUAL_PERIOD)
        return None

    series: dict[str, list[Accruals]] = {vintage.name: [] for vintage in vintages}
    for (_, prior_end), (period_start, period_end) in pairwise(periods):
        try:
            computed = {
                vintage.name: accruals(
                    view,
                    cik,
                    period_end=period_end,
                    prior_period_end=prior_end,
                    # Both dates: an end date alone matches the fiscal year and its
                    # fourth quarter, and the PIT layer now refuses rather than guess.
                    period_start=period_start,
                    vintage=vintage,
                )
                for vintage in vintages
            }
        except (InsufficientData, ValueError):
            # One arm short is the whole firm-year short: an arm computed on a
            # different set of years would not be comparable with the others.
            report.drop(Drop.MISSING_INPUT)
            continue
        for name, value in computed.items():
            series[name].append(value)

    if any(not values for values in series.values()):
        report.drop(Drop.NO_PRIOR_YEAR)
        return None
    return series


def _pick_per_arm(
    series: dict[str, list[Accruals]],
    *,
    formation: date,
    vintages: Sequence[Vintage],
) -> dict[str, Accruals] | None:
    """The freshest fiscal year each arm could see on ``formation``.

    Each arm selects on its **own** knowledge date. For the honest arms that is
    the filing date of the slowest input; for the naive arm it is the fiscal period
    end, which is what lets it reach a year the others cannot yet see.

    Returns ``None`` unless every arm has something, so the traded universe stays
    identical across arms even though the fiscal years differ.
    """
    picked: dict[str, Accruals] = {}
    for vintage in vintages:
        candidates = [value for value in series[vintage.name] if value.knowledge_date <= formation]
        if not candidates:
            return None
        picked[vintage.name] = max(
            candidates, key=lambda value: (value.knowledge_date, value.period_end)
        )
    return picked


def _resolve_symbols(
    view: PitView,
    ciks: Iterable[Cik | int],
    *,
    report: PanelReport,
    exclude_financials: bool,
    exclude_utilities: bool,
) -> list[tuple[Cik | int, str]]:
    """Apply the sector screen and map each survivor to a ticker."""
    resolved: list[tuple[Cik | int, str]] = []
    for cik in ciks:
        try:
            entity = view.entity(cik)
        except InsufficientData:
            report.drop(Drop.NO_TICKER)
            continue
        if (exclude_financials and entity.is_financial) or (
            exclude_utilities and entity.is_utility
        ):
            report.drop(Drop.SECTOR_EXCLUDED)
            continue
        symbols = view.tickers(cik)
        if not symbols:
            report.drop(Drop.NO_TICKER)
            continue
        # The shortest symbol is the common-stock line; longer ones on the same CIK
        # are preferred shares, warrants and class variants.
        resolved.append((cik, min(symbols, key=lambda symbol: (len(symbol), symbol))))
    return resolved
