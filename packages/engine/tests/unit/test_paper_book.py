"""The paper book's tamper-evidence, tested by actually tampering.

Every attack here is paired with an intact-ledger control. A verifier that always
returned "broken" would pass every tampering test and be worthless, so each one
asserts both that the attack is caught and that an untouched book is not
slandered.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia.book.ledger import GENESIS, BookError, Holding, PaperBook

RECORDED = datetime(2026, 7, 27, 21, 0, tzinfo=UTC)


def _holdings(*pairs: tuple[str, str, str]) -> list[Holding]:
    return [
        Holding(symbol=symbol, quantity=Decimal(quantity), price=Decimal(price))
        for symbol, quantity, price in pairs
    ]


@pytest.fixture
def book(tmp_path: Path) -> PaperBook:
    return PaperBook(tmp_path / "book.jsonl")


@pytest.fixture
def three_days(book: PaperBook) -> PaperBook:
    for index, day in enumerate((date(2026, 7, 24), date(2026, 7, 25), date(2026, 7, 26))):
        book.record(
            as_of=day,
            holdings=_holdings(("AAA", "100", "10.50"), ("BBB", "-50", "20.25")),
            cash=Decimal("1000.00"),
            recorded_at=RECORDED,
            note=f"day {index}",
        )
    return book


class TestRecording:
    def test_an_empty_book_has_the_genesis_head(self, book: PaperBook) -> None:
        assert book.head() == GENESIS
        assert book.verify().intact

    def test_marks_chain_to_one_another(self, three_days: PaperBook) -> None:
        marks = list(three_days.read())
        assert [mark.sequence for mark in marks] == [1, 2, 3]
        assert len({mark.chain_head for mark in marks}) == 3

    def test_nav_is_cash_plus_net_exposure(self, three_days: PaperBook) -> None:
        mark = next(three_days.read())
        # 100 x 10.50 = 1050, -50 x 20.25 = -1012.50, plus 1000 cash.
        assert mark.net_exposure == Decimal("37.50")
        assert mark.nav == Decimal("1037.50")
        assert mark.gross_exposure == Decimal("2062.50")

    def test_money_stays_exact(self, three_days: PaperBook) -> None:
        """A track record that drifts by floating-point error is not a track record."""
        mark = next(three_days.read())
        assert isinstance(mark.nav, Decimal)
        assert all(isinstance(holding.price, Decimal) for holding in mark.holdings)

    def test_a_reopened_book_reads_back_identically(self, three_days: PaperBook) -> None:
        reopened = PaperBook(three_days.path)
        assert [mark.chain_head for mark in reopened.read()] == [
            mark.chain_head for mark in three_days.read()
        ]
        assert reopened.verify().intact

    def test_holdings_hash_independently_of_the_order_they_were_passed(
        self, tmp_path: Path
    ) -> None:
        """Two books holding the same positions must agree, however they were built."""
        forward = PaperBook(tmp_path / "a.jsonl")
        backward = PaperBook(tmp_path / "b.jsonl")
        positions = _holdings(("AAA", "100", "10.50"), ("BBB", "-50", "20.25"))
        forward.record(
            as_of=date(2026, 7, 24),
            holdings=positions,
            cash=Decimal("1000.00"),
            recorded_at=RECORDED,
        )
        backward.record(
            as_of=date(2026, 7, 24),
            holdings=list(reversed(positions)),
            cash=Decimal("1000.00"),
            recorded_at=RECORDED,
        )
        assert forward.head() == backward.head()


class TestRefusals:
    def test_a_back_dated_mark_is_refused(self, three_days: PaperBook) -> None:
        with pytest.raises(BookError, match="back-dated"):
            three_days.record(
                as_of=date(2026, 7, 25),
                holdings=[],
                cash=Decimal("0"),
                recorded_at=RECORDED,
            )

    def test_marking_the_same_day_twice_is_refused(self, three_days: PaperBook) -> None:
        """A correction is a new mark with a note, not an overwrite."""
        with pytest.raises(BookError, match="already marked"):
            three_days.record(
                as_of=date(2026, 7, 26),
                holdings=[],
                cash=Decimal("0"),
                recorded_at=RECORDED,
            )

    def test_float_cash_is_refused(self, book: PaperBook) -> None:
        with pytest.raises(BookError, match="must be Decimal"):
            book.record(
                as_of=date(2026, 7, 24),
                holdings=[],
                cash=1000.0,  # type: ignore[arg-type]
                recorded_at=RECORDED,
            )

    def test_the_next_day_is_accepted(self, three_days: PaperBook) -> None:
        """Control: the refusals above must not block ordinary use."""
        mark = three_days.record(
            as_of=date(2026, 7, 27),
            holdings=_holdings(("AAA", "100", "11.00")),
            cash=Decimal("1000.00"),
            recorded_at=RECORDED,
        )
        assert mark.sequence == 4
        assert three_days.verify().intact


def _rewrite(book: PaperBook, mutate: object) -> None:
    lines = book.path.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]
    mutate(payloads)  # type: ignore[operator]
    book.path.write_text(
        "\n".join(json.dumps(payload, sort_keys=True) for payload in payloads) + "\n",
        encoding="utf-8",
    )


class TestTamperEvidence:
    def test_an_untouched_book_verifies(self, three_days: PaperBook) -> None:
        """The control. Without it, every test below could pass on a broken verifier."""
        result = three_days.verify()
        assert result.intact
        assert result.n_marks == 3
        assert "intact" in result.explain()

    def test_editing_a_price_in_the_middle_is_caught(self, three_days: PaperBook) -> None:
        _rewrite(three_days, lambda rows: rows[1]["holdings"][0].update({"price": "99.99"}))
        result = three_days.verify()
        assert not result.intact
        assert result.first_broken_sequence == 2
        assert "altered after it was recorded" in result.explain()

    def test_editing_cash_is_caught(self, three_days: PaperBook) -> None:
        _rewrite(three_days, lambda rows: rows[0].update({"cash": "999999.00"}))
        assert three_days.verify().first_broken_sequence == 1

    def test_deleting_a_mark_is_caught(self, three_days: PaperBook) -> None:
        """Deleting a bad day is the specific fraud this exists to prevent."""
        _rewrite(three_days, lambda rows: rows.pop(1))
        result = three_days.verify()
        assert not result.intact
        assert result.n_marks == 2

    def test_reordering_marks_is_caught(self, three_days: PaperBook) -> None:
        def swap(rows: list[dict[str, object]]) -> None:
            rows[0], rows[1] = rows[1], rows[0]

        _rewrite(three_days, swap)
        assert not three_days.verify().intact

    def test_appending_a_forged_mark_is_caught(self, three_days: PaperBook) -> None:
        """A mark with a plausible but unchained head."""

        def forge(rows: list[dict[str, object]]) -> None:
            forged = dict(rows[-1])
            forged["sequence"] = 4
            forged["as_of"] = "2026-07-27"
            forged["cash"] = "50000.00"
            rows.append(forged)

        _rewrite(three_days, forge)
        result = three_days.verify()
        assert not result.intact
        assert result.first_broken_sequence == 4

    def test_a_rebuilt_chain_matches_a_previously_published_head(
        self, three_days: PaperBook
    ) -> None:
        """What anchoring actually buys.

        The head is what gets committed publicly. Recomputing the chain from the
        file must reproduce it -- otherwise the published value certifies nothing.
        """
        published = three_days.head()
        assert three_days.verify().head == published

    def test_a_tampered_book_cannot_reproduce_the_published_head(
        self, three_days: PaperBook
    ) -> None:
        """The other half: tampering is detectable by anyone holding the old head."""
        published = three_days.head()
        _rewrite(three_days, lambda rows: rows[1].update({"note": "adjusted"}))
        assert three_days.verify().head != published
