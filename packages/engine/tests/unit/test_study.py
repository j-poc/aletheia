"""Panel construction across data vintages.

The test that matters here is :class:`TestTheTimingChannelIsWired`. An earlier
version of ``study.py`` pinned every arm to the fiscal year the *honest* arm could
see, on the reasoning that letting the arms diverge would confound the comparison.
That reasoning was wrong: the divergence **is** the timing channel. With it
suppressed, ``restated_values`` and ``naive_vendor`` read identical numbers and the
study reported a timing bias of exactly zero -- a clean-looking null result
produced by a bug rather than by the data.

The fixture is built so a correct implementation cannot avoid disagreeing: at the
formation date, the fiscal year has ended but the 10-K has not been filed.
"""

from __future__ import annotations

from datetime import date

import pytest

from aletheia.core.types import Cik, Fact
from aletheia.features.accruals import (
    NET_INCOME,
    OPERATING_CASH_FLOW,
    TOTAL_ASSETS,
)
from aletheia.features.vintage import FIRST_REPORTED, NAIVE_VENDOR, RESTATED_VALUES
from aletheia.pit import as_of
from aletheia.research.study import Drop, build_panels
from aletheia.store.db import Warehouse
from tests._factories import make_entity, make_fact, make_identifier

FIRM = Cik(320193)
OTHER = Cik(789019)

FY2014_END = date(2014, 12, 31)
FY2015_END = date(2015, 12, 31)
FY2016_END = date(2016, 12, 31)

FY2014_FILED = date(2015, 2, 20)
FY2015_FILED = date(2016, 2, 19)
FY2016_FILED = date(2017, 2, 17)

FORMATION_BEFORE_FILING = date(2016, 1, 31)
"""FY2015 has ended but its 10-K is three weeks away.

A period-indexed vendor panel already has a FY2015 row; a practitioner does not.
This is the exact window the timing channel lives in."""

FORMATION_AFTER_FILING = date(2016, 3, 31)

ALL_VINTAGES = (FIRST_REPORTED, RESTATED_VALUES, NAIVE_VENDOR)


def _year(
    *,
    period_end: date,
    prior_end: date,
    filed_at: date,
    accn: str,
    net_income: str,
    cash_flow: str,
    assets_end: str,
    assets_start: str,
    cik: int = int(FIRM),
) -> list[Fact]:
    """One fiscal year's worth of accrual inputs, all filed together."""
    common = {"filed_at": filed_at, "accn": accn, "unit": "USD", "cik": cik}
    return [
        make_fact(
            value=net_income,
            concept=NET_INCOME,
            period_start=date(prior_end.year, prior_end.month, prior_end.day),
            period_end=period_end,
            **common,
        ),
        make_fact(
            value=cash_flow,
            concept=OPERATING_CASH_FLOW,
            period_start=date(prior_end.year, prior_end.month, prior_end.day),
            period_end=period_end,
            **common,
        ),
        make_fact(
            value=assets_end,
            concept=TOTAL_ASSETS,
            period_start=None,
            period_end=period_end,
            **common,
        ),
        make_fact(
            value=assets_start,
            concept=TOTAL_ASSETS,
            period_start=None,
            period_end=prior_end,
            **common,
        ),
    ]


def _register(warehouse: Warehouse, *, cik: int, ticker: str, sic: str = "3571") -> None:
    warehouse.write_entity(make_entity(cik=cik, name=f"FIRM {cik}", sic=sic))
    warehouse.write_identifiers([make_identifier(cik=cik, ticker=ticker)])


def _load(warehouse: Warehouse, *, cik: int = int(FIRM)) -> Warehouse:
    _register(warehouse, cik=cik, ticker="TEST")
    warehouse.write_facts(
        [
            # Three fiscal years, because the honest arm needs somewhere to fall
            # back to while the naive arm has already moved on. With only two, the
            # honest arm has nothing at the formation date and the firm-period is
            # dropped from every arm -- which is correct behaviour but tests nothing.
            *_year(
                period_end=FY2014_END,
                prior_end=date(2013, 12, 31),
                filed_at=FY2014_FILED,
                accn="0000000000-15-000001",
                net_income="500",
                cash_flow="2000",
                assets_end="90000",
                assets_start="80000",
                cik=cik,
            ),
            *_year(
                period_end=FY2015_END,
                prior_end=FY2014_END,
                filed_at=FY2015_FILED,
                accn="0000000000-16-000001",
                # Accruals strongly negative in FY2015.
                net_income="1000",
                cash_flow="5000",
                assets_end="100000",
                assets_start="90000",
                cik=cik,
            ),
            *_year(
                period_end=FY2016_END,
                prior_end=FY2015_END,
                filed_at=FY2016_FILED,
                accn="0000000000-17-000001",
                # Accruals strongly positive in FY2016 -- opposite sign, so an arm
                # reading the wrong year is unmistakable rather than marginal.
                net_income="9000",
                cash_flow="1000",
                assets_end="110000",
                assets_start="100000",
                cik=cik,
            ),
        ]
    )
    return warehouse


@pytest.fixture
def loaded(warehouse: Warehouse) -> Warehouse:
    return _load(warehouse)


class TestTheTimingChannelIsWired:
    def test_the_naive_arm_reads_a_year_the_honest_arm_cannot_yet_see(
        self, loaded: Warehouse
    ) -> None:
        """The regression test for the suppressed-channel bug.

        On 2016-01-31 the honest arm's freshest filed year is FY2015 (filed
        2016-02-19? no -- not yet). It must fall back to nothing here, so the
        firm-period is dropped. The meaningful case is the one below, where the
        honest arm has FY2015 and the naive arm has already moved to FY2016.
        """
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=[date(2017, 1, 31)],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        honest = panels.by_vintage[FIRST_REPORTED.name][date(2017, 1, 31)]
        naive = panels.by_vintage[NAIVE_VENDOR.name][date(2017, 1, 31)]
        assert honest and naive
        # FY2016 ended 2016-12-31 and was filed 2017-02-17. On 2017-01-31 the naive
        # arm has it and the honest arm does not.
        assert honest[0].value != naive[0].value
        assert honest[0].value < 0 < naive[0].value

    def test_the_restated_and_naive_arms_are_not_the_same_series(self, loaded: Warehouse) -> None:
        """The exact equality the study script warns about at runtime."""
        formations = [date(2017, 1, 31), date(2017, 3, 31)]
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=formations,
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        restated = [
            observation.value
            for formation in formations
            for observation in panels.by_vintage[RESTATED_VALUES.name][formation]
        ]
        naive = [
            observation.value
            for formation in formations
            for observation in panels.by_vintage[NAIVE_VENDOR.name][formation]
        ]
        assert restated != naive

    def test_after_the_filing_the_arms_agree_again(self, loaded: Warehouse) -> None:
        """Control: the gap is a timing gap, not a permanent divergence.

        Once the 10-K is public both arms are on the same fiscal year, and since
        nothing here was restated they read the same value. A difference that
        persisted after filing would be measuring something else.
        """
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=[FORMATION_AFTER_FILING],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        values = {
            name: dates[FORMATION_AFTER_FILING][0].value
            for name, dates in panels.by_vintage.items()
        }
        assert values[FIRST_REPORTED.name] == values[NAIVE_VENDOR.name]

    def test_each_arm_carries_its_own_knowledge_date(self, loaded: Warehouse) -> None:
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=[date(2017, 1, 31)],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        honest = panels.by_vintage[FIRST_REPORTED.name][date(2017, 1, 31)][0]
        naive = panels.by_vintage[NAIVE_VENDOR.name][date(2017, 1, 31)][0]
        assert honest.knowledge_date == FY2015_FILED
        assert naive.knowledge_date == FY2016_END


class TestTheUniverseStaysIdentical:
    def test_every_arm_trades_the_same_names_on_the_same_dates(self, loaded: Warehouse) -> None:
        """The invariant the whole comparison rests on, asserted not assumed."""
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=[date(2017, 1, 31), date(2017, 6, 30)],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        panels.assert_universes_match()  # must not raise

    def test_a_firm_missing_from_one_arm_is_dropped_from_all(self, warehouse: Warehouse) -> None:
        """Only one fiscal year, so no arm has an opening balance sheet for it."""
        _register(warehouse, cik=int(FIRM), ticker="TEST")
        warehouse.write_facts(
            _year(
                period_end=FY2015_END,
                prior_end=FY2014_END,
                filed_at=FY2015_FILED,
                accn="0000000000-16-000001",
                net_income="1000",
                cash_flow="5000",
                assets_end="100000",
                assets_start="90000",
            )
        )
        panels = build_panels(
            as_of(warehouse, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=[date(2017, 1, 31)],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        for by_formation in panels.by_vintage.values():
            assert all(not observations for observations in by_formation.values())
        assert panels.report.kept == 0
        assert panels.report.drops.get(Drop.NO_ANNUAL_PERIOD) == 1


class TestDropAccounting:
    def test_a_firm_with_no_ticker_is_recorded_not_silently_skipped(
        self, loaded: Warehouse
    ) -> None:
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM, OTHER],
            formation_dates=[date(2017, 1, 31)],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        assert panels.report.drops.get(Drop.NO_TICKER, 0) >= 1

    def test_the_report_states_what_entered_and_what_did_not(self, loaded: Warehouse) -> None:
        panels = build_panels(
            as_of(loaded, date(2026, 7, 27)),
            ciks=[FIRM],
            formation_dates=[date(2017, 1, 31)],
            vintages=ALL_VINTAGES,
            exclude_financials=False,
            exclude_utilities=False,
        )
        text = panels.report.explain()
        assert "firm-periods kept" in text
        assert "dropped" in text
