"""Accruals, and the vintage policies that decide which numbers feed them.

The fixture is Apple's FY2009, and **every figure in it was fetched from EDGAR and
checked** rather than invented. Apple adopted ASU 2009-13/14 retrospectively in the
10-K/A filed 2010-01-25, which restated the year it had reported three months
earlier:

| Input | First reported (2009-10-27) | Restated (2010-01-25) |
|---|---|---|
| `NetIncomeLoss` FY2009 | 5,704,000,000 | 8,235,000,000 |
| `NetCashProvidedByUsedInOperatingActivities` FY2009 | 10,159,000,000 | unchanged |
| `Assets` at 2009-09-26 | 53,851,000,000 | 47,501,000,000 |
| `Assets` at 2008-09-27 | 39,572,000,000 | 36,171,000,000 |

Net income moved +44% and both ends of the balance sheet moved down. Accruals
computed from the two vintages differ by roughly a factor of two, which is what
makes this a fair test of a decomposition that claims to separate them.

The expected values are computed from the definition inside each test rather than
recorded from a previous run, so a change to the formula fails the test instead of
being blessed by it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aletheia.core.errors import InsufficientData
from aletheia.core.types import Cik, Fact
from aletheia.features.accruals import (
    NET_INCOME,
    OPERATING_CASH_FLOW,
    OPERATING_CASH_FLOW_CONTINUING,
    TOTAL_ASSETS,
    Accruals,
    accruals,
    annual_period_ends,
)
from aletheia.features.vintage import (
    FIRST_REPORTED,
    NAIVE_VENDOR,
    RESTATED_VALUES,
    Vintage,
)
from aletheia.pit import as_of
from aletheia.store.db import Warehouse
from tests._factories import make_fact

FIRM = Cik(320193)
FY2009_START = date(2008, 9, 28)
FY2009_END = date(2009, 9, 26)
FY2008_END = date(2008, 9, 27)

TENK_ACCN = "0001193125-09-214859"
TENK_FILED = date(2009, 10, 27)
AMENDMENT_ACCN = "0001193125-10-012091"
AMENDMENT_FILED = date(2010, 1, 25)
EARLY_TENQ_ACCN = "0001193125-09-153165"
EARLY_TENQ_FILED = date(2009, 7, 22)
"""Total assets at FY2008 first appeared in a 10-Q, three months before the 10-K.
The first report of a balance-sheet item is often not the annual report."""

AFTER_FIRST = date(2009, 12, 1)
AFTER_RESTATEMENT = date(2010, 6, 1)

NI_FIRST = "5704000000"
NI_RESTATED = "8235000000"
CFO = "10159000000"
ASSETS_2009_FIRST = "53851000000"
ASSETS_2009_RESTATED = "47501000000"
ASSETS_2008_FIRST = "39572000000"
ASSETS_2008_RESTATED = "36171000000"


def _flow(concept: str, value: str, *, filed_at: date, accn: str, form: str = "10-K") -> Fact:
    return make_fact(
        value=value,
        filed_at=filed_at,
        accn=accn,
        concept=concept,
        unit="USD",
        form=form,
        period_start=FY2009_START,
        period_end=FY2009_END,
    )


def _stock(value: str, *, period_end: date, filed_at: date, accn: str, form: str = "10-K") -> Fact:
    return make_fact(
        value=value,
        filed_at=filed_at,
        accn=accn,
        concept=TOTAL_ASSETS,
        unit="USD",
        form=form,
        period_start=None,
        period_end=period_end,
    )


def _as_first_reported() -> list[Fact]:
    return [
        _flow(NET_INCOME, NI_FIRST, filed_at=TENK_FILED, accn=TENK_ACCN),
        _flow(OPERATING_CASH_FLOW, CFO, filed_at=TENK_FILED, accn=TENK_ACCN),
        _stock(ASSETS_2009_FIRST, period_end=FY2009_END, filed_at=TENK_FILED, accn=TENK_ACCN),
        _stock(
            ASSETS_2008_FIRST,
            period_end=FY2008_END,
            filed_at=EARLY_TENQ_FILED,
            accn=EARLY_TENQ_ACCN,
            form="10-Q",
        ),
    ]


def _as_restated() -> list[Fact]:
    return [
        _flow(
            NET_INCOME, NI_RESTATED, filed_at=AMENDMENT_FILED, accn=AMENDMENT_ACCN, form="10-K/A"
        ),
        _flow(
            OPERATING_CASH_FLOW, CFO, filed_at=AMENDMENT_FILED, accn=AMENDMENT_ACCN, form="10-K/A"
        ),
        _stock(
            ASSETS_2009_RESTATED,
            period_end=FY2009_END,
            filed_at=AMENDMENT_FILED,
            accn=AMENDMENT_ACCN,
            form="10-K/A",
        ),
        _stock(
            ASSETS_2008_RESTATED,
            period_end=FY2008_END,
            filed_at=AMENDMENT_FILED,
            accn=AMENDMENT_ACCN,
            form="10-K/A",
        ),
    ]


@pytest.fixture
def loaded(warehouse: Warehouse) -> Warehouse:
    warehouse.write_facts([*_as_first_reported(), *_as_restated()])
    return warehouse


def _expected(net_income: str, cfo: str, assets_end: str, assets_start: str) -> float:
    average = (Decimal(assets_end) + Decimal(assets_start)) / Decimal(2)
    return float((Decimal(net_income) - Decimal(cfo)) / average)


def _compute(warehouse: Warehouse, view_date: date, vintage: Vintage) -> Accruals:
    return accruals(
        as_of(warehouse, view_date),
        FIRM,
        period_end=FY2009_END,
        prior_period_end=FY2008_END,
        vintage=vintage,
    )


class TestTheDefinition:
    def test_reproduces_a_hand_computed_value(self, loaded: Warehouse) -> None:
        """(NI - CFO) / average total assets, arithmetic written out in the test."""
        result = _compute(loaded, AFTER_FIRST, FIRST_REPORTED)
        expected = _expected(NI_FIRST, CFO, ASSETS_2009_FIRST, ASSETS_2008_FIRST)
        assert result.accruals == pytest.approx(expected)
        # Apple's cash flow exceeded reported earnings, so accruals are negative --
        # the "high quality earnings" end of Sloan's sort.
        assert result.accruals < 0

    def test_money_stays_decimal_and_only_the_ratio_is_float(self, loaded: Warehouse) -> None:
        result = _compute(loaded, AFTER_FIRST, FIRST_REPORTED)
        assert isinstance(result.net_income, Decimal)
        assert isinstance(result.operating_cash_flow, Decimal)
        assert isinstance(result.average_assets, Decimal)
        assert isinstance(result.accruals, float)

    def test_every_input_is_traceable_to_an_accession(self, loaded: Warehouse) -> None:
        result = _compute(loaded, AFTER_FIRST, FIRST_REPORTED)
        inputs = result.inputs
        assert len(inputs) == 4
        assert {item.concept for item in inputs} == {NET_INCOME, OPERATING_CASH_FLOW, TOTAL_ASSETS}
        assert {item.accn.value for item in inputs} == {TENK_ACCN, EARLY_TENQ_ACCN}

    def test_knowledge_date_is_the_slowest_input(self, loaded: Warehouse) -> None:
        """A feature is only as timely as the last number it needed.

        The FY2008 balance sheet was public from the 10-Q on 2009-07-22, but the
        income statement not until 2009-10-27. Dating the feature at the earlier
        of the two would claim knowledge three months before it existed.
        """
        result = _compute(loaded, AFTER_FIRST, FIRST_REPORTED)
        assert result.knowledge_date == TENK_FILED
        assert min(item.knowledge_date for item in result.inputs) == EARLY_TENQ_FILED


class TestVintagePolicies:
    def test_first_reported_ignores_the_restatement(self, loaded: Warehouse) -> None:
        result = _compute(loaded, AFTER_RESTATEMENT, FIRST_REPORTED)
        assert result.net_income == Decimal(NI_FIRST)
        assert not result.uses_restated_input

    def test_restated_values_takes_the_number_but_keeps_the_date(self, loaded: Warehouse) -> None:
        """The value channel in isolation: new numbers, honest publication date."""
        result = _compute(loaded, AFTER_RESTATEMENT, RESTATED_VALUES)
        assert result.net_income == Decimal(NI_RESTATED)
        assert result.knowledge_date == TENK_FILED, (
            "the restated arm must not also inherit the amendment's filing date, "
            "or it would confound the value channel with the timing channel"
        )

    def test_naive_vendor_dates_the_value_at_period_end(self, loaded: Warehouse) -> None:
        """Both channels: today's numbers, attached to the period they describe."""
        result = _compute(loaded, AFTER_RESTATEMENT, NAIVE_VENDOR)
        assert result.net_income == Decimal(NI_RESTATED)
        assert result.knowledge_date == FY2009_END

    def test_the_restatement_roughly_halves_measured_accruals(self, loaded: Warehouse) -> None:
        """The magnitude that makes this fixture worth using.

        Not an arbitrary tolerance: both values are recomputed from the definition
        here, so the assertion is that the implementation agrees with the formula
        on both vintages, and that the two are materially different.
        """
        honest = _compute(loaded, AFTER_RESTATEMENT, FIRST_REPORTED).accruals
        restated = _compute(loaded, AFTER_RESTATEMENT, RESTATED_VALUES).accruals
        assert honest == pytest.approx(
            _expected(NI_FIRST, CFO, ASSETS_2009_FIRST, ASSETS_2008_FIRST)
        )
        assert restated == pytest.approx(
            _expected(NI_RESTATED, CFO, ASSETS_2009_RESTATED, ASSETS_2008_RESTATED)
        )
        assert abs(restated) < abs(honest) / 1.8

    def test_the_naive_arm_can_see_data_that_did_not_exist_yet(self, loaded: Warehouse) -> None:
        """The bias being modelled, demonstrated.

        On 2009-09-26 the FY2009 10-K was a month away and the restatement four
        months away. The honest arm refuses to answer; the naive arm answers with
        a number that would not exist until 2010, which is exactly the error a
        fiscal-period-indexed panel commits.
        """
        naive = _compute(loaded, AFTER_RESTATEMENT, NAIVE_VENDOR)
        assert naive.knowledge_date == FY2009_END < TENK_FILED

        with pytest.raises(InsufficientData):
            _compute(loaded, FY2009_END, FIRST_REPORTED)


class TestRefusals:
    def test_a_missing_input_raises_rather_than_defaulting_to_zero(
        self, warehouse: Warehouse
    ) -> None:
        warehouse.write_facts([_flow(NET_INCOME, NI_FIRST, filed_at=TENK_FILED, accn=TENK_ACCN)])
        with pytest.raises(InsufficientData):
            _compute(warehouse, AFTER_FIRST, FIRST_REPORTED)

    def test_a_non_annual_prior_period_is_refused(self, loaded: Warehouse) -> None:
        """A quarter-end denominator silently changes what average assets means."""
        with pytest.raises(ValueError, match="preceding fiscal year-end"):
            accruals(
                as_of(loaded, AFTER_FIRST),
                FIRM,
                period_end=FY2009_END,
                prior_period_end=date(2009, 6, 27),
                vintage=FIRST_REPORTED,
            )

    def test_zero_average_assets_is_refused(self, warehouse: Warehouse) -> None:
        warehouse.write_facts(
            [
                _flow(NET_INCOME, "1000", filed_at=TENK_FILED, accn=TENK_ACCN),
                _flow(OPERATING_CASH_FLOW, "500", filed_at=TENK_FILED, accn=TENK_ACCN),
                _stock("0", period_end=FY2009_END, filed_at=TENK_FILED, accn=TENK_ACCN),
                _stock("0", period_end=FY2008_END, filed_at=TENK_FILED, accn=TENK_ACCN),
            ]
        )
        with pytest.raises(InsufficientData, match="meaningless"):
            _compute(warehouse, AFTER_FIRST, FIRST_REPORTED)


class TestConceptFallback:
    def test_the_continuing_operations_tag_is_accepted(self, warehouse: Warehouse) -> None:
        """Filers with discontinued operations tag the other concept.

        Treating that as missing would drop a non-random slice of the sample --
        non-random because discontinued operations correlate with the distress
        this signal is about.
        """
        warehouse.write_facts(
            [
                _flow(NET_INCOME, NI_FIRST, filed_at=TENK_FILED, accn=TENK_ACCN),
                _flow(OPERATING_CASH_FLOW_CONTINUING, CFO, filed_at=TENK_FILED, accn=TENK_ACCN),
                _stock(
                    ASSETS_2009_FIRST, period_end=FY2009_END, filed_at=TENK_FILED, accn=TENK_ACCN
                ),
                _stock(
                    ASSETS_2008_FIRST, period_end=FY2008_END, filed_at=TENK_FILED, accn=TENK_ACCN
                ),
            ]
        )
        result = _compute(warehouse, AFTER_FIRST, FIRST_REPORTED)
        assert result.operating_cash_flow == Decimal(CFO)


class TestPeriodDiscovery:
    def test_only_annual_durations_are_returned(self, warehouse: Warehouse) -> None:
        warehouse.write_facts(
            [
                _flow(NET_INCOME, NI_FIRST, filed_at=TENK_FILED, accn=TENK_ACCN),
                # A quarter: same concept, ~91 days, must not be read as a year.
                make_fact(
                    value="1000000000",
                    filed_at=date(2009, 7, 22),
                    accn=EARLY_TENQ_ACCN,
                    concept=NET_INCOME,
                    unit="USD",
                    form="10-Q",
                    period_start=date(2009, 3, 29),
                    period_end=date(2009, 6, 27),
                ),
            ]
        )
        assert annual_period_ends(as_of(warehouse, AFTER_FIRST), FIRM) == [FY2009_END]

    def test_instant_facts_are_not_mistaken_for_periods(self, warehouse: Warehouse) -> None:
        """Balance-sheet facts have no start date and no duration to classify."""
        warehouse.write_facts(
            [_stock(ASSETS_2009_FIRST, period_end=FY2009_END, filed_at=TENK_FILED, accn=TENK_ACCN)]
        )
        assert annual_period_ends(as_of(warehouse, AFTER_FIRST), FIRM) == []
