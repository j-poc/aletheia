"""Sloan (1996) accruals, computed under an explicit data-vintage policy.

The accrual anomaly is the natural test case for this system. Sloan showed that
the accrual component of earnings -- the part not backed by cash -- reverses:
firms whose profits come mostly from accruals go on to underperform, and firms
whose profits are backed by cash outperform. It is one of the most replicated
results in accounting research, which is exactly what makes it useful here. A
well-established effect is a measuring instrument: point it at two different data
vintages and the difference between the readings is attributable to the vintage,
not to whether the effect exists.

**Which definition, and why.** Sloan's original measure is built from
balance-sheet changes::

    ACC = (dCA - dCash) - (dCL - dSTD - dTP) - Dep

Hribar & Collins (2002) showed this is systematically wrong for firms that
acquired or divested during the year: a balance sheet that grows by acquisition
records the jump as an accrual, when no accrual occurred. They recommend taking
accruals straight from the cash-flow statement::

    ACC = Net income - Cash flow from operations

which cannot suffer that error because both terms come from the same statement.
That is what is implemented here. The balance-sheet method is deliberately not
offered: it needs six concepts instead of three, each of which is optionally
tagged in XBRL, so it would shrink the sample *and* introduce a known bias. If a
robustness check against it is ever wanted, it belongs in a study that states the
sample loss it causes.

**Scaling.** By average total assets over the year, following the literature --
so the measure is a rate, comparable across firms of different size. Average, not
ending, because the numerator is a flow over the year while assets are a stock at
a point.

**Sign.** Reported as computed: positive accruals mean earnings exceeded operating
cash flow. Sloan's prediction is that high positive accruals precede *poor*
subsequent returns, so the profitable portfolio is long low accruals and short
high ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from aletheia.core.errors import InsufficientData
from aletheia.core.types import Accession, Cik
from aletheia.features.vintage import Vintage
from aletheia.pit import PitFact, PitView

NET_INCOME: Final = "NetIncomeLoss"
OPERATING_CASH_FLOW: Final = "NetCashProvidedByUsedInOperatingActivities"
OPERATING_CASH_FLOW_CONTINUING: Final = (
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"
)
TOTAL_ASSETS: Final = "Assets"

ANNUAL_MIN_DAYS: Final = 300
ANNUAL_MAX_DAYS: Final = 430
"""A fiscal year in XBRL is rarely exactly 365 days: 52/53-week calendars, and
transition periods after a year-end change, both land outside a tight window.
Anything outside this range is a quarter, a half-year, or a stub, and is not an
annual period."""

USD: Final = "USD"


@dataclass(frozen=True, slots=True)
class FactInput:
    """One number that went into a feature value, and where it came from.

    Carried so an evidence card can show the arithmetic back to an accession
    number instead of asserting a result.
    """

    concept: str
    value: Decimal
    unit: str
    period_end: date
    accn: Accession
    knowledge_date: date
    report_seq: int

    @classmethod
    def of(cls, fact: PitFact) -> FactInput:
        return cls(
            concept=fact.concept,
            value=fact.value,
            unit=fact.unit,
            period_end=fact.period_end,
            accn=fact.accn,
            knowledge_date=fact.knowledge_date,
            report_seq=fact.report_seq,
        )


@dataclass(frozen=True, slots=True)
class Accruals:
    """Accruals for one firm-year under one vintage policy."""

    cik: Cik
    period_end: date
    vintage: str
    accruals: float
    """Scaled by average total assets. A rate, not a currency amount."""
    net_income: Decimal
    operating_cash_flow: Decimal
    average_assets: Decimal
    knowledge_date: date
    """When every input had become knowable -- the max over the inputs.

    A feature is only as timely as its slowest ingredient. Taking the max rather
    than the filing date of the income statement is what stops a signal being
    dated earlier than the balance sheet it needs.
    """
    inputs: tuple[FactInput, ...]

    @property
    def uses_restated_input(self) -> bool:
        """True when any input came from a restatement rather than a first report."""
        return any(item.report_seq > 1 for item in self.inputs)


def annual_periods(view: PitView, cik: Cik | int) -> list[tuple[date, date]]:
    """``(period_start, period_end)`` for each annual income statement.

    Both dates, not just the end. A fiscal year and its fourth quarter end on the
    same day, so an end date alone does not name a period -- and asking for one by
    end date alone now raises :class:`AmbiguousPeriod` rather than silently
    returning the quarter.

    This enumerates *candidate* periods; whether any of them was knowable on a
    given date is decided by :meth:`Vintage.resolve`, which raises if it was not.
    """
    periods: set[tuple[date, date]] = set()
    for fact in view.facts(cik, NET_INCOME, unit=USD):
        if fact.period_start is None:
            continue
        span = (fact.period_end - fact.period_start).days
        if ANNUAL_MIN_DAYS <= span <= ANNUAL_MAX_DAYS:
            periods.add((fact.period_start, fact.period_end))
    return sorted(periods)


def accruals(
    view: PitView,
    cik: Cik | int,
    *,
    period_end: date,
    prior_period_end: date,
    period_start: date | None = None,
    vintage: Vintage,
) -> Accruals:
    """Cash-flow-statement accruals for the year ending ``period_end``.

    ``prior_period_end`` supplies the opening balance sheet for the average-assets
    denominator. It must be the immediately preceding fiscal year-end; passing a
    quarter-end silently changes what the denominator means, so the gap is checked.

    Raises :class:`InsufficientData` when any input is missing or not yet public,
    rather than substituting a zero or a NaN. A missing accrual is not a zero
    accrual, and the difference reaches the Sharpe ratio.
    """
    gap = (period_end - prior_period_end).days
    if not ANNUAL_MIN_DAYS <= gap <= ANNUAL_MAX_DAYS:
        raise ValueError(
            f"prior_period_end must be the preceding fiscal year-end; "
            f"{prior_period_end} to {period_end} is {gap} days"
        )

    # The flow concepts are keyed on both dates: a year and its Q4 share an end
    # date, and reading the quarter's earnings against the year's cash flow would
    # produce a number that looks exactly like an accruals ratio and is not one.
    income = vintage.resolve(
        view, cik, NET_INCOME, period_end=period_end, period_start=period_start, unit=USD
    )
    cash_flow = _operating_cash_flow(
        view, cik, period_end=period_end, period_start=period_start, vintage=vintage
    )
    assets_end = vintage.resolve(view, cik, TOTAL_ASSETS, period_end=period_end, unit=USD)
    assets_start = vintage.resolve(view, cik, TOTAL_ASSETS, period_end=prior_period_end, unit=USD)

    average_assets = (assets_end.value + assets_start.value) / Decimal(2)
    if average_assets <= 0:
        raise InsufficientData(
            f"average total assets for CIK {int(cik)} year ending {period_end} is "
            f"{average_assets}; accruals scaled by it would be meaningless"
        )

    inputs = (
        FactInput.of(income),
        FactInput.of(cash_flow),
        FactInput.of(assets_end),
        FactInput.of(assets_start),
    )
    accrual_amount = income.value - cash_flow.value
    return Accruals(
        cik=Cik(int(cik)),
        period_end=period_end,
        vintage=vintage.name,
        # Decimal for the money arithmetic above; float only once it has become a
        # dimensionless ratio feeding statistics.
        accruals=float(accrual_amount / average_assets),
        net_income=income.value,
        operating_cash_flow=cash_flow.value,
        average_assets=average_assets,
        knowledge_date=max(item.knowledge_date for item in inputs),
        inputs=inputs,
    )


def _operating_cash_flow(
    view: PitView,
    cik: Cik | int,
    *,
    period_end: date,
    period_start: date | None = None,
    vintage: Vintage,
) -> PitFact:
    """Cash from operations, falling back to the continuing-operations tag.

    Filers use one tag or the other depending on whether they had discontinued
    operations to separate. Both are the operating total for the period; treating
    the absence of the first as missing data would drop a large, non-random slice
    of the sample -- non-random because discontinued operations correlate with
    exactly the distress this signal is about.
    """
    try:
        return vintage.resolve(
            view,
            cik,
            OPERATING_CASH_FLOW,
            period_end=period_end,
            period_start=period_start,
            unit=USD,
        )
    except InsufficientData:
        return vintage.resolve(
            view,
            cik,
            OPERATING_CASH_FLOW_CONTINUING,
            period_end=period_end,
            period_start=period_start,
            unit=USD,
        )
