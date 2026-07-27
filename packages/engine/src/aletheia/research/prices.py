"""An in-memory price cache shaped like the kernel's loader protocol.

The kernel asks for three windows per (symbol, formation date): the cost lookback,
the forward window, and the exit window. Over ~170 monthly formations that is
~500 queries per symbol against the same few thousand rows, and the 60-day cost
window is re-read almost in full every month.

Loading each symbol's history once and slicing it in memory removes the repetition
without changing a single returned bar. The slicing is a binary search over a
date-sorted list, so a window costs O(log n + window) rather than a database scan.

**This is a performance change and nothing else.** It deliberately does not filter,
adjust, or bound anything the kernel would otherwise see: the kernel's leak
canaries must remain able to fire, so a cache that quietly clipped windows to
"safe" ranges would disable the very checks that make the result trustworthy.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from datetime import date

from aletheia.pit import PitPrice


class CachedPrices:
    """Loads each symbol's bars once, then answers windows from memory.

    ``fetch`` is called at most once per symbol and must return the symbol's full
    history, ascending by ``bar_date``. A symbol the source cannot serve yields an
    empty list, which the kernel already treats as an exclusion rather than an
    error -- that path is the survivorship accounting and must stay intact.
    """

    def __init__(self, fetch: Callable[[str], Sequence[PitPrice]]) -> None:
        self._fetch = fetch
        self._bars: dict[str, list[PitPrice]] = {}
        self._dates: dict[str, list[date]] = {}
        self.misses = 0
        self.hits = 0

    def __call__(self, symbol: str, *, start: date, end: date) -> Sequence[PitPrice]:
        if symbol not in self._bars:
            self.misses += 1
            bars = sorted(self._fetch(symbol), key=lambda bar: bar.bar_date)
            self._bars[symbol] = bars
            self._dates[symbol] = [bar.bar_date for bar in bars]
        else:
            self.hits += 1
        if start > end:
            return []
        days = self._dates[symbol]
        lower = bisect_left(days, start)
        upper = bisect_right(days, end)
        return self._bars[symbol][lower:upper]

    @property
    def symbols_loaded(self) -> int:
        return len(self._bars)

    @property
    def bars_held(self) -> int:
        return sum(len(bars) for bars in self._bars.values())

    def explain(self) -> str:
        total = self.hits + self.misses
        rate = self.hits / total if total else 0.0
        return (
            f"price cache: {self.symbols_loaded:,} symbols, {self.bars_held:,} bars, "
            f"{total:,} windows served, {rate:.1%} from memory"
        )
