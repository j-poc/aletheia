"""The live paper book: a hash-chained record of what was held, and when.

Every backtest ever shown to anyone was constructed after the fact. That is not an
accusation, it is arithmetic -- the researcher chose the sample, the universe and
the parameters knowing how the period turned out, and no amount of care fully
removes that. The only record immune to it is one written down *before* the returns
existed.

So this starts the clock. Each day the book records what it holds, at prices as
observed, and chains the record: mark ``n`` hashes the head of mark ``n-1``. Editing
or removing any historical mark changes every head after it, and :meth:`verify`
reports exactly where the break is.

**What the chain does and does not prove.** It proves *internal* consistency: the
history has not been quietly rewritten since it was written. On its own that is
weak, because whoever holds the file could rebuild the whole chain. It becomes
strong when the head is published somewhere with an independent clock -- a dated
commit in a repository, which is how this is used. The head in a commit dated
2026-07-27 certifies every mark before it, and no later rewrite can produce that
same head. Anchoring is what turns a hash chain into evidence; the chain alone is
only tamper-*evident* to someone who already has a copy.

Money is ``Decimal`` throughout. A paper book whose NAV drifts by floating-point
error is not a track record.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from aletheia.core.errors import AletheiaError
from aletheia.core.hashing import chain_next

GENESIS: Final = "0" * 64
CHAIN_KEY: Final = "chain_head"


class BookError(AletheiaError):
    """The book was asked to do something that would corrupt its history."""


@dataclass(frozen=True, slots=True)
class Holding:
    """One position as observed on a mark date."""

    symbol: str
    quantity: Decimal
    """Signed. Negative is short."""
    price: Decimal
    signal_value: float | None = None
    """The signal that put this position on, kept so a later reader can see why."""

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.price

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "price": self.price,
            "market_value": self.market_value,
            "signal_value": None if self.signal_value is None else f"{self.signal_value:.10f}",
        }


@dataclass(frozen=True, slots=True)
class Mark:
    """One day's observation of the whole book."""

    sequence: int
    as_of: date
    holdings: tuple[Holding, ...]
    cash: Decimal
    recorded_at: datetime
    chain_head: str
    note: str = ""

    @property
    def gross_exposure(self) -> Decimal:
        return sum((abs(holding.market_value) for holding in self.holdings), Decimal(0))

    @property
    def net_exposure(self) -> Decimal:
        return sum((holding.market_value for holding in self.holdings), Decimal(0))

    @property
    def nav(self) -> Decimal:
        return self.cash + self.net_exposure

    def body(self) -> dict[str, Any]:
        """The hashed content: everything except the head it produces.

        Holdings are sorted by symbol so two marks with the same positions hash
        identically regardless of the order they were assembled in.
        """
        return {
            "sequence": self.sequence,
            "as_of": self.as_of,
            "holdings": [
                holding.as_dict() for holding in sorted(self.holdings, key=lambda h: h.symbol)
            ],
            "cash": self.cash,
            "recorded_at": self.recorded_at,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Verification:
    """Whether the chain is intact, and where it first is not."""

    intact: bool
    first_broken_sequence: int | None
    n_marks: int
    head: str

    def explain(self) -> str:
        if self.intact:
            return (
                f"chain intact over {self.n_marks} mark(s), head {self.head[:16]}…"
                if self.n_marks
                else "no marks recorded yet"
            )
        return (
            f"chain broken at mark {self.first_broken_sequence} of {self.n_marks}: "
            f"that mark, or one before it, was altered after it was recorded"
        )


class PaperBook:
    """An append-only, hash-chained JSON-lines record of daily marks."""

    def __init__(self, path: Path) -> None:
        self.path = path

    # ------------------------------------------------------------- writing --

    def record(
        self,
        *,
        as_of: date,
        holdings: Sequence[Holding],
        cash: Decimal,
        recorded_at: datetime,
        note: str = "",
    ) -> Mark:
        """Append one day's mark.

        ``recorded_at`` is supplied by the caller rather than read from the clock
        here, so the book stays a pure function of its inputs and a replay
        reproduces byte-identical marks.

        Dates must not go backwards, and a date cannot be marked twice. Both would
        be silent corruption of a track record: the first lets a bad day be
        inserted behind a good one, the second lets a mark be superseded without
        the supersession being visible.
        """
        marks = list(self.read())
        if marks:
            last = marks[-1]
            if as_of < last.as_of:
                raise BookError(
                    f"cannot record {as_of} after {last.as_of}; a track record that "
                    f"accepts back-dated marks is not a track record"
                )
            if as_of == last.as_of:
                raise BookError(
                    f"{as_of} is already marked (sequence {last.sequence}); correcting a "
                    f"mark means appending a new one with a note, not overwriting"
                )
        if not isinstance(cash, Decimal):
            raise BookError("cash must be Decimal; a book carried in float is not exact")

        head = marks[-1].chain_head if marks else GENESIS
        draft = Mark(
            sequence=len(marks) + 1,
            as_of=as_of,
            holdings=tuple(holdings),
            cash=cash,
            recorded_at=recorded_at,
            chain_head=GENESIS,
            note=note,
        )
        mark = Mark(
            sequence=draft.sequence,
            as_of=draft.as_of,
            holdings=draft.holdings,
            cash=draft.cash,
            recorded_at=draft.recorded_at,
            chain_head=chain_next(head, draft.body()),
            note=draft.note,
        )
        self._append(mark)
        return mark

    # ------------------------------------------------------------- reading --

    def read(self) -> Iterator[Mark]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield _from_json(json.loads(line))

    def head(self) -> str:
        """The current chain head. This is the value worth anchoring publicly."""
        marks = list(self.read())
        return marks[-1].chain_head if marks else GENESIS

    def verify(self) -> Verification:
        """Recompute the chain from the genesis head and find the first mismatch."""
        head = GENESIS
        count = 0
        for mark in self.read():
            count += 1
            head = chain_next(head, mark.body())
            if mark.chain_head != head:
                return Verification(
                    intact=False,
                    first_broken_sequence=mark.sequence,
                    n_marks=count,
                    head=head,
                )
        return Verification(intact=True, first_broken_sequence=None, n_marks=count, head=head)

    def _append(self, mark: Mark) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_to_json(mark), sort_keys=True) + "\n")


def _to_json(mark: Mark) -> dict[str, Any]:
    return {
        "sequence": mark.sequence,
        "as_of": mark.as_of.isoformat(),
        "holdings": [
            {
                "symbol": holding.symbol,
                "quantity": str(holding.quantity),
                "price": str(holding.price),
                "signal_value": holding.signal_value,
            }
            for holding in sorted(mark.holdings, key=lambda h: h.symbol)
        ],
        "cash": str(mark.cash),
        "recorded_at": mark.recorded_at.isoformat(),
        "note": mark.note,
        CHAIN_KEY: mark.chain_head,
    }


def _from_json(payload: dict[str, Any]) -> Mark:
    return Mark(
        sequence=int(payload["sequence"]),
        as_of=date.fromisoformat(payload["as_of"]),
        holdings=tuple(
            Holding(
                symbol=str(item["symbol"]),
                quantity=Decimal(str(item["quantity"])),
                price=Decimal(str(item["price"])),
                signal_value=None
                if item.get("signal_value") is None
                else float(item["signal_value"]),
            )
            for item in payload.get("holdings", ())
        ),
        cash=Decimal(str(payload["cash"])),
        recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        chain_head=str(payload[CHAIN_KEY]),
        note=str(payload.get("note", "")),
    )
