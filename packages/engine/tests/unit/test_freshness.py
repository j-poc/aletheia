"""The freshness contract, and the one failure it exists to prevent.

Every page in this system answers "as it stands today", where *today* means the
newest filing in the warehouse rather than the date on the reader's wall. When
ingest has not run in a month, every page keeps answering in the same colours
with the same confidence about a month-old world, and nothing fails: the run
exits zero, the suite is green, and the surface has quietly converted "I do not
know" into "I checked".

That failure has no natural test, because there is no exception to assert on. So
these are the assertions instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aletheia.api import app as api
from aletheia.core.clock import FrozenClock
from aletheia.core.freshness import FRESH_WITHIN_DAYS, assess
from aletheia.store.db import Warehouse
from tests._factories import (
    FIRST_REPORT_FILED,
    first_report,
    make_entity,
    make_filing,
    make_identifier,
)

VINTAGE = date(2026, 7, 27)


class TestTheContract:
    def test_a_warehouse_ingested_today_is_fresh(self) -> None:
        assert assess(data_vintage=VINTAGE, observed_on=VINTAGE).state == "fresh"

    def test_the_boundary_day_is_still_fresh(self) -> None:
        # Exactly at the contract, not past it. Stated as a test because an
        # off-by-one here cries stale every Monday, and a surface that warns on
        # ordinary days teaches its reader to ignore the warning.
        at_limit = VINTAGE + timedelta(days=FRESH_WITHIN_DAYS)
        assert assess(data_vintage=VINTAGE, observed_on=at_limit).age_days == FRESH_WITHIN_DAYS
        assert assess(data_vintage=VINTAGE, observed_on=at_limit).state == "fresh"

    def test_one_day_past_the_contract_is_stale(self) -> None:
        past = VINTAGE + timedelta(days=FRESH_WITHIN_DAYS + 1)
        verdict = assess(data_vintage=VINTAGE, observed_on=past)
        assert verdict.state == "stale"
        assert verdict.age_days == FRESH_WITHIN_DAYS + 1

    def test_the_stale_reason_tells_the_reader_what_to_do_and_what_they_are_seeing(self) -> None:
        verdict = assess(data_vintage=VINTAGE, observed_on=date(2026, 9, 1))
        assert "make ingest" in verdict.reason
        # The date every page is really answering as of, spelled out. Without it
        # the reader knows the surface is old but not what it is old *at*.
        assert VINTAGE.isoformat() in verdict.reason

    def test_a_filing_dated_after_today_is_broken_not_fresh(self) -> None:
        # Negative age. `age <= contract` would call this the freshest possible
        # warehouse, when in fact either the clock or the data is wrong and there
        # is no way to tell which from here.
        verdict = assess(data_vintage=date(2026, 8, 1), observed_on=VINTAGE)
        assert verdict.state == "broken"
        assert verdict.age_days < 0


class TestPartial:
    def test_a_missing_input_is_partial_even_when_the_data_is_current(self) -> None:
        verdict = assess(data_vintage=VINTAGE, observed_on=VINTAGE, gaps=("facts: 0 rows",))
        assert verdict.state == "partial"
        assert "facts: 0 rows" in verdict.reason

    def test_stale_outranks_partial_but_does_not_hide_it(self) -> None:
        # Severity picks one word for the badge; the gaps still travel, so a
        # surface that is both does not report only the louder half.
        verdict = assess(
            data_vintage=VINTAGE, observed_on=date(2026, 9, 1), gaps=("facts: 0 rows",)
        )
        assert verdict.state == "stale"
        assert verdict.gaps == ("facts: 0 rows",)

    def test_broken_outranks_everything(self) -> None:
        verdict = assess(
            data_vintage=date(2026, 8, 1), observed_on=VINTAGE, gaps=("facts: 0 rows",)
        )
        assert verdict.state == "broken"


class TestOverTheWire:
    """The same judgement, as the browser receives it."""

    @pytest.fixture
    def client(self, warehouse: Warehouse) -> Iterator[TestClient]:
        warehouse.write_entity(make_entity())
        warehouse.write_identifiers([make_identifier()])
        warehouse.write_filings(
            [make_filing(accn="0001193125-09-214859", filed_at=FIRST_REPORT_FILED)]
        )
        warehouse.write_facts([first_report()])
        # Overriding the dependency, not the module handle, and never entering the
        # client as a context manager -- entering runs the lifespan, which opens
        # the real on-disk warehouse from settings.
        api.app.dependency_overrides[api.get_warehouse] = lambda: warehouse
        try:
            yield TestClient(api.app)
        finally:
            api.app.dependency_overrides.clear()

    @pytest.fixture
    def frozen(self, client: TestClient) -> TestClient:
        api.app.dependency_overrides[api.get_clock] = lambda: FrozenClock(
            datetime(2026, 9, 1, tzinfo=UTC)
        )
        return client

    def test_quality_ships_a_state_not_a_date_for_the_reader_to_subtract(
        self, frozen: TestClient
    ) -> None:
        payload = frozen.get("/api/quality").json()["freshness"]
        # The whole point: a renderer handed "stale" cannot present it as fresh.
        # A renderer handed only "2026-07-27" has to do arithmetic, and will get
        # it wrong on the day the contract changes -- silently, in the reader's
        # favour.
        assert payload["state"] in {"fresh", "stale", "partial", "broken"}
        assert payload["observed_on"] == "2026-09-01"
        assert payload["age_days"] == (date(2026, 9, 1) - date(2009, 10, 27)).days

    def test_the_fixture_warehouse_is_stale_at_that_clock(self, frozen: TestClient) -> None:
        # The fixture's newest filing is Apple's FY2008 first report. Seen from
        # 2026 that is seventeen years old, which had better not read as current.
        assert frozen.get("/api/quality").json()["freshness"]["state"] == "stale"

    def test_the_same_warehouse_reads_fresh_when_the_clock_sits_at_its_vintage(
        self, client: TestClient
    ) -> None:
        # The other arm. Without it the test above passes against an endpoint
        # hard-coded to return "stale", which asserts nothing.
        vintage = date.fromisoformat(client.get("/api/quality").json()["data_vintage"])
        api.app.dependency_overrides[api.get_clock] = lambda: FrozenClock(
            datetime(vintage.year, vintage.month, vintage.day, tzinfo=UTC)
        )
        payload = client.get("/api/quality").json()["freshness"]
        assert payload["state"] == "fresh"
        assert payload["age_days"] == 0
