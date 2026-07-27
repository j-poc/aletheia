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
from aletheia.features.accruals import Accruals, accruals, annual_period_ends
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
    """One firm-year that every arm was able to compute."""

    cik: Cik
    symbol: str
    period_end: date
    prior_period_end: date
    by_vintage: dict[str, Accruals]

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
        periods = annual_period_ends(view, cik)
        if len(periods) < 2:
            report.drop(Drop.NO_ANNUAL_PERIOD)
            continue
        pairs = list(pairwise(periods))

        for formation in formation_dates:
            firm_period = _firm_period_for(
                view,
                cik=cik,
                symbol=symbol,
                pairs=pairs,
                formation=formation,
                vintages=vintages,
                report=report,
            )
            if firm_period is None:
                continue
            report.kept += 1
            report.firms_kept.add(int(cik))
            for vintage in vintages:
                panels[vintage.name][formation].append(firm_period.observation(vintage.name))

    result = Panels(by_vintage=panels, report=report)
    result.assert_universes_match()
    return result


def _firm_period_for(
    view: PitView,
    *,
    cik: Cik | int,
    symbol: str,
    pairs: Sequence[tuple[date, date]],
    formation: date,
    vintages: Sequence[Vintage],
    report: PanelReport,
) -> FirmPeriod | None:
    """The most recent fiscal year this firm had filed by ``formation``.

    Selection uses the *honest* arm's knowledge date for every arm. Letting the
    naive arm pick a fresher period would confound the vintage comparison with a
    difference in which fiscal year each arm is trading -- a second effect on top
    of the one being measured.
    """
    candidates = [
        (prior_end, period_end) for prior_end, period_end in pairs if period_end < formation
    ]
    if not candidates:
        report.drop(Drop.NO_PRIOR_YEAR)
        return None

    for prior_end, period_end in reversed(candidates):
        try:
            computed = {
                vintage.name: accruals(
                    view,
                    cik,
                    period_end=period_end,
                    prior_period_end=prior_end,
                    vintage=vintage,
                )
                for vintage in vintages
            }
        except (InsufficientData, ValueError):
            continue
        honest = next(iter(computed.values()))
        if honest.knowledge_date > formation:
            continue
        return FirmPeriod(
            cik=Cik(int(cik)),
            symbol=symbol,
            period_end=period_end,
            prior_period_end=prior_end,
            by_vintage=computed,
        )

    report.drop(Drop.NOT_YET_FILED)
    return None


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
