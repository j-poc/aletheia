"""The in-memory price cache.

A cache in front of a leak-checking kernel is dangerous in one specific way: if it
quietly narrows a window, the kernel's canaries stop being able to fire and the
guarantee they enforce silently disappears. So the tests here check equivalence
with the underlying source, not just hit rates -- including for windows that
*should* return bars the kernel will reject.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from aletheia.pit import PitPrice
from aletheia.research.prices import CachedPrices

START = date(2020, 1, 1)


def _history(symbol: str, n_days: int = 400) -> list[PitPrice]:
    bars = []
    day = START
    while len(bars) < n_days:
        if day.weekday() < 5:
            close = 100.0 + len(bars) * 0.1
            bars.append(
                PitPrice(
                    symbol=symbol,
                    bar_date=day,
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    adj_close=close,
                    volume=1000.0,
                    tradable_from=day,
                )
            )
        day += timedelta(days=1)
    return bars


class Source:
    """Counts how many times each symbol is actually fetched."""

    def __init__(self, symbols: dict[str, list[PitPrice]]) -> None:
        self.symbols = symbols
        self.fetches: list[str] = []

    def __call__(self, symbol: str) -> Sequence[PitPrice]:
        self.fetches.append(symbol)
        return self.symbols.get(symbol, [])


@pytest.fixture
def source() -> Source:
    return Source({"AAA": _history("AAA"), "BBB": _history("BBB")})


class TestEquivalenceWithTheSource:
    def test_a_window_returns_exactly_the_bars_in_range(self, source: Source) -> None:
        cache = CachedPrices(source)
        window = cache("AAA", start=date(2020, 3, 1), end=date(2020, 3, 31))
        expected = [
            bar
            for bar in source.symbols["AAA"]
            if date(2020, 3, 1) <= bar.bar_date <= date(2020, 3, 31)
        ]
        assert list(window) == expected

    def test_both_bounds_are_inclusive(self, source: Source) -> None:
        """An off-by-one here would silently change every entry and exit price."""
        cache = CachedPrices(source)
        bars = source.symbols["AAA"]
        first, last = bars[10].bar_date, bars[20].bar_date
        window = cache("AAA", start=first, end=last)
        assert window[0].bar_date == first
        assert window[-1].bar_date == last
        assert len(window) == 11

    def test_the_full_history_is_returned_when_the_window_spans_it(self, source: Source) -> None:
        cache = CachedPrices(source)
        window = cache("AAA", start=date(1990, 1, 1), end=date(2090, 1, 1))
        assert list(window) == source.symbols["AAA"]

    def test_a_window_before_any_data_is_empty_not_an_error(self, source: Source) -> None:
        cache = CachedPrices(source)
        assert cache("AAA", start=date(1990, 1, 1), end=date(1990, 12, 31)) == []

    def test_an_inverted_window_is_empty(self, source: Source) -> None:
        cache = CachedPrices(source)
        assert cache("AAA", start=date(2020, 6, 1), end=date(2020, 1, 1)) == []

    def test_an_unknown_symbol_yields_an_empty_list(self, source: Source) -> None:
        """The kernel treats this as an exclusion, which is the survivorship count.

        Raising here would turn a measured coverage gap into a crash.
        """
        cache = CachedPrices(source)
        assert cache("ZZZ", start=START, end=date(2021, 1, 1)) == []


class TestTheCacheDoesNotHideAnything:
    def test_a_window_extending_past_the_data_still_returns_what_exists(
        self, source: Source
    ) -> None:
        cache = CachedPrices(source)
        bars = source.symbols["AAA"]
        window = cache("AAA", start=bars[-3].bar_date, end=date(2099, 1, 1))
        assert len(window) == 3

    def test_the_cache_will_serve_bars_the_kernel_must_reject(self, source: Source) -> None:
        """The cache must not pre-filter for safety.

        The kernel's cost-window canary fires when it is handed a bar dated after
        the formation date. A cache that clipped windows to look correct would
        disable that check -- so this asserts the cache faithfully returns a
        window the kernel is expected to complain about.
        """
        cache = CachedPrices(source)
        bars = source.symbols["AAA"]
        window = cache("AAA", start=bars[0].bar_date, end=bars[50].bar_date)
        assert window[-1].bar_date == bars[50].bar_date


class TestFetchBehaviour:
    def test_each_symbol_is_fetched_once_however_many_windows_are_asked_for(
        self, source: Source
    ) -> None:
        cache = CachedPrices(source)
        for month in range(1, 12):
            cache("AAA", start=date(2020, month, 1), end=date(2020, month, 28))
        assert source.fetches == ["AAA"]
        assert cache.symbols_loaded == 1

    def test_a_missing_symbol_is_not_refetched_on_every_call(self, source: Source) -> None:
        """Otherwise every delisted name costs a round trip at every formation date."""
        cache = CachedPrices(source)
        for _ in range(5):
            cache("ZZZ", start=START, end=date(2021, 1, 1))
        assert source.fetches == ["ZZZ"]

    def test_unsorted_input_is_sorted_before_slicing(self, source: Source) -> None:
        """Binary search over an unsorted list returns silent nonsense."""
        shuffled = list(reversed(source.symbols["AAA"]))
        cache = CachedPrices(Source({"AAA": shuffled}))
        window = cache("AAA", start=date(2020, 3, 1), end=date(2020, 3, 31))
        assert [bar.bar_date for bar in window] == sorted(bar.bar_date for bar in window)
        assert len(window) == 22

    def test_the_hit_rate_is_reported(self, source: Source) -> None:
        cache = CachedPrices(source)
        cache("AAA", start=START, end=date(2020, 2, 1))
        cache("AAA", start=START, end=date(2020, 3, 1))
        cache("BBB", start=START, end=date(2020, 2, 1))
        assert cache.hits == 1
        assert cache.misses == 2
        assert "2 symbols" in cache.explain()
        assert cache.bars_held == 800
