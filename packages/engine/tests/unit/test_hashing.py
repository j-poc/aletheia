"""Canonical hashing and hash chains.

Every determinism guarantee downstream rests on these functions: if the same
logical value can hash two different ways, then two identical studies get two
different identities and the trial ledger stops meaning anything.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia.core.hashing import (
    GENESIS,
    canonical_hash,
    canonical_json,
    chain_next,
    sha256_bytes,
    sha256_file,
    verify_chain,
)


class TestCanonicalEncoding:
    def test_key_order_does_not_change_the_hash(self) -> None:
        assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})

    def test_nested_key_order_does_not_change_the_hash(self) -> None:
        left = {"outer": {"z": [1, 2], "a": {"q": True}}}
        right = {"outer": {"a": {"q": True}, "z": [1, 2]}}
        assert canonical_hash(left) == canonical_hash(right)

    def test_list_order_does_change_the_hash(self) -> None:
        """Order matters in sequences — [1,2] and [2,1] are different values."""
        assert canonical_hash([1, 2]) != canonical_hash([2, 1])

    def test_decimal_round_trips_losslessly(self) -> None:
        assert canonical_json(Decimal("5.36")) == b'"D:5.36"'
        assert canonical_hash(Decimal("5.36")) != canonical_hash(Decimal("6.78"))

    def test_decimal_is_normalised_so_trailing_zeros_do_not_matter(self) -> None:
        assert canonical_hash(Decimal("5.36")) == canonical_hash(Decimal("5.3600"))

    def test_dates_and_datetimes_encode_distinctly(self) -> None:
        assert canonical_json(date(2009, 10, 27)) == b'"d:2009-10-27"'
        assert canonical_json(datetime(2009, 10, 27, tzinfo=UTC)).startswith(b'"T:')

    def test_float_is_refused(self) -> None:
        """A hash that is only usually stable fails rarely, late, in production."""
        with pytest.raises(TypeError, match="float is not canonically hashable"):
            canonical_json({"sharpe": 1.4})

    def test_naive_datetime_is_refused(self) -> None:
        with pytest.raises(TypeError, match="attach a timezone"):
            canonical_json(datetime(2009, 10, 27))  # noqa: DTZ001 - the point of the test

    def test_unknown_type_is_refused_rather_than_stringified(self) -> None:
        class Opaque:
            pass

        with pytest.raises(TypeError, match="cannot canonically encode"):
            canonical_json(Opaque())


class TestFileHashing:
    def test_matches_in_memory_hash(self, tmp_path: Path) -> None:
        payload = b"0000320193-09-214859"
        target = tmp_path / "f.bin"
        target.write_bytes(payload)
        assert sha256_file(target) == sha256_bytes(payload)

    def test_streams_a_payload_larger_than_the_chunk_size(self, tmp_path: Path) -> None:
        payload = b"x" * (3 * (1 << 20) + 17)  # spans four chunks, last one partial
        target = tmp_path / "big.bin"
        target.write_bytes(payload)
        assert sha256_file(target) == sha256_bytes(payload)


class TestHashChain:
    def test_chain_is_order_dependent(self) -> None:
        a = chain_next(GENESIS, {"n": 1})
        assert chain_next(a, {"n": 2}) != chain_next(GENESIS, {"n": 2})

    def test_rejects_a_malformed_head(self) -> None:
        with pytest.raises(ValueError, match="64-char lowercase hex"):
            chain_next("not-a-digest", {"n": 1})

    def test_intact_chain_verifies(self) -> None:
        entries = _build_chain([{"seq": 1, "mark": "10.00"}, {"seq": 2, "mark": "10.50"}])
        assert verify_chain(entries) == -1

    def test_tampering_with_history_is_detected(self) -> None:
        """Positive control for the paper book: an altered old mark must fail."""
        entries = _build_chain([{"seq": 1, "mark": "10.00"}, {"seq": 2, "mark": "10.50"}])
        assert verify_chain(entries) == -1, "control: chain must verify before tampering"
        entries[0]["mark"] = "99.00"
        assert verify_chain(entries) == 0

    def test_tampering_with_the_last_entry_is_also_detected(self) -> None:
        entries = _build_chain([{"seq": 1, "mark": "10.00"}, {"seq": 2, "mark": "10.50"}])
        entries[1]["mark"] = "99.00"
        assert verify_chain(entries) == 1

    def test_empty_chain_verifies(self) -> None:
        assert verify_chain([]) == -1


def _build_chain(bodies: list[dict[str, object]]) -> list[dict[str, object]]:
    head = GENESIS
    entries: list[dict[str, object]] = []
    for body in bodies:
        head = chain_next(head, body)
        entries.append({**body, "chain_head": head})
    return entries
