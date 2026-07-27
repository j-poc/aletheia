"""The consecutive-failure circuit breaker on batch ingest.

Written after a real run: the price vendor's daily quota ran out five symbols
into a 226-symbol batch, and the run then spent twenty-two minutes retrying the
remaining 221 against an exhausted quota. It finished with exit code 0 and a long
list of identical errors, which reads like a coverage problem rather than the
entitlement problem it was.

The distinction the breaker has to get right is that a *per-name* entitlement gap
says nothing about the next name -- that is the survivorship measurement, and
tripping on it would destroy the very count the system exists to report.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from aletheia.core.clock import FrozenClock
from aletheia.core.config import load_settings
from aletheia.core.errors import SourceError
from aletheia.core.types import PriceBar
from aletheia.ingest import CONSECUTIVE_FAILURE_LIMIT, Ingestor
from aletheia.sources.base import ParseReport
from aletheia.sources.prices import DelistedCoverageError
from aletheia.store.db import Warehouse
from tests._factories import RETRIEVED_AT, RUN_ID

START = date(2020, 1, 1)
END = date(2020, 1, 31)


class ScriptedPrices:
    """A price source whose behaviour per symbol is dictated by the test."""

    def __init__(self, behaviour: dict[str, str]) -> None:
        self.behaviour = behaviour
        self.asked: list[str] = []

    def daily_bars(
        self, symbol: str, *, start: date, end: date, run_id: str
    ) -> tuple[list[PriceBar], ParseReport]:
        self.asked.append(symbol)
        action = self.behaviour.get(symbol, "ok")
        if action == "quota":
            raise SourceError("HTTP 429: Limit Reach . Please upgrade your plan", source="fmp")
        if action == "delisted":
            raise DelistedCoverageError(f"{symbol} is not served on this plan", source="fmp")
        return (
            [
                PriceBar(
                    symbol=symbol,
                    bar_date=START,
                    open=10.0,
                    high=10.5,
                    low=9.5,
                    close=10.0,
                    adj_close=10.0,
                    volume=1000.0,
                    source="fmp",
                    source_uri="https://example.invalid",
                    retrieved_at=RETRIEVED_AT,
                    content_sha256="0" * 64,
                    ingest_run_id=run_id,
                )
            ],
            ParseReport(),
        )


def _ingestor(warehouse: Warehouse, prices: ScriptedPrices) -> Ingestor:
    return Ingestor(
        settings=load_settings(),
        warehouse=warehouse,
        clock=FrozenClock(datetime(2026, 7, 27, tzinfo=UTC)),
        edgar=None,  # type: ignore[arg-type]
        fred=None,
        prices=prices,  # type: ignore[arg-type]
    )


SYMBOLS = [f"SYM{index:02d}" for index in range(30)]


class TestTheBreakerTrips:
    def test_a_dead_source_stops_the_batch_rather_than_grinding_through_it(
        self, warehouse: Warehouse
    ) -> None:
        prices = ScriptedPrices(dict.fromkeys(SYMBOLS, "quota"))
        outcome = _ingestor(warehouse, prices).ingest_prices(SYMBOLS, start=START, end=END)

        assert outcome.aborted_after is not None
        assert len(prices.asked) == CONSECUTIVE_FAILURE_LIMIT, (
            "the batch must stop, not attempt all 30 symbols"
        )

    def test_the_abort_says_why_and_how_far_it_got(self, warehouse: Warehouse) -> None:
        """A run that stops must be diagnosable from its own output."""
        prices = ScriptedPrices(dict.fromkeys(SYMBOLS, "quota"))
        outcome = _ingestor(warehouse, prices).ingest_prices(SYMBOLS, start=START, end=END)

        assert outcome.aborted_after is not None
        assert "Limit Reach" in outcome.aborted_after
        assert f"{CONSECUTIVE_FAILURE_LIMIT} of {len(SYMBOLS)} attempted" in outcome.aborted_after
        assert "ABORTED" in outcome.summary()

    def test_work_completed_before_the_abort_is_kept(self, warehouse: Warehouse) -> None:
        """Stopping early must not discard the symbols that did succeed."""
        behaviour = dict.fromkeys(SYMBOLS[:3], "ok") | dict.fromkeys(SYMBOLS[3:], "quota")
        outcome = _ingestor(warehouse, ScriptedPrices(behaviour)).ingest_prices(
            SYMBOLS, start=START, end=END
        )
        assert outcome.rows_written == 3
        assert outcome.aborted_after is not None


class TestTheBreakerDoesNotTripWhenItShouldNot:
    def test_a_clean_run_completes_every_symbol(self, warehouse: Warehouse) -> None:
        """The control. Without it, a breaker that always tripped would pass above."""
        prices = ScriptedPrices({})
        outcome = _ingestor(warehouse, prices).ingest_prices(SYMBOLS, start=START, end=END)

        assert outcome.aborted_after is None
        assert len(prices.asked) == len(SYMBOLS)
        assert outcome.rows_written == len(SYMBOLS)

    def test_entitlement_gaps_never_trip_it(self, warehouse: Warehouse) -> None:
        """The measurement the system exists to make must not abort the run.

        Every name unreachable would trip a naive breaker on the first five, and
        the survivorship count -- the whole point of enumerating them -- would be
        replaced by an abort message.
        """
        prices = ScriptedPrices(dict.fromkeys(SYMBOLS, "delisted"))
        outcome = _ingestor(warehouse, prices).ingest_prices(SYMBOLS, start=START, end=END)

        assert outcome.aborted_after is None
        assert len(outcome.unreachable) == len(SYMBOLS)
        assert len(prices.asked) == len(SYMBOLS)

    def test_a_success_resets_the_counter(self, warehouse: Warehouse) -> None:
        """Scattered failures are a data problem, not a dead source."""
        behaviour = {}
        for index, symbol in enumerate(SYMBOLS):
            behaviour[symbol] = "quota" if index % 3 else "ok"
        prices = ScriptedPrices(behaviour)
        outcome = _ingestor(warehouse, prices).ingest_prices(SYMBOLS, start=START, end=END)

        assert outcome.aborted_after is None
        assert len(prices.asked) == len(SYMBOLS)
        assert len(outcome.failed) == sum(1 for value in behaviour.values() if value == "quota")

    def test_a_delisted_name_between_failures_resets_the_counter(
        self, warehouse: Warehouse
    ) -> None:
        """An entitlement gap is evidence the source is alive and answering."""
        behaviour = dict.fromkeys(SYMBOLS, "quota")
        behaviour[SYMBOLS[4]] = "delisted"
        prices = ScriptedPrices(behaviour)
        outcome = _ingestor(warehouse, prices).ingest_prices(SYMBOLS, start=START, end=END)

        assert outcome.aborted_after is not None
        # Four failures, the reset, then five more before tripping.
        assert len(prices.asked) == 4 + 1 + CONSECUTIVE_FAILURE_LIMIT

    def test_a_batch_shorter_than_the_limit_never_aborts(self, warehouse: Warehouse) -> None:
        short = SYMBOLS[: CONSECUTIVE_FAILURE_LIMIT - 1]
        outcome = _ingestor(warehouse, ScriptedPrices(dict.fromkeys(short, "quota"))).ingest_prices(
            short, start=START, end=END
        )
        assert outcome.aborted_after is None
        assert len(outcome.failed) == len(short)


@pytest.fixture(autouse=True)
def _run(warehouse: Warehouse) -> None:
    """Every ingest needs an open run to attribute rows to."""
    if not warehouse.execute("SELECT count(*) FROM ingest_runs").fetchone()[0]:
        warehouse.start_run(source="test", params={}, run_id=RUN_ID)
