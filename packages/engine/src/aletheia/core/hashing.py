"""Content addressing and hash chains.

Three jobs:

1. **Payload identity** — every byte fetched from an external source is hashed
   before parsing, so a re-fetch that returns different bytes is detectable
   rather than silently overwriting history.
2. **Canonical hashing of structured values** — configs, hypotheses, evidence
   cards. Requires a *canonical* encoding: two dicts that differ only in key
   order must hash identically, or the same study appears to be two studies.
3. **Hash chains** — append-only logs (the trial ledger, the paper book) where
   altering an old entry must invalidate everything after it.

Floats are rejected in canonical encoding. ``0.1 + 0.2`` does not round-trip
through JSON identically on every platform, and a hash that is *usually* stable
is worse than no hash: it fails rarely, in production, months later.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

_CHUNK: Final = 1 << 20  # 1 MiB
GENESIS: Final = "0" * 64
"""Chain head before any entry exists. A chain whose head is GENESIS is empty."""


def sha256_bytes(payload: bytes) -> str:
    """Hex digest of raw bytes."""
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex digest of a file, streamed so a 128 MB EDGAR zip does not enter memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON encoding suitable for hashing.

    Sorted keys, no insignificant whitespace, UTF-8. ``Decimal``, ``date`` and
    ``datetime`` are encoded losslessly as tagged strings; ``float`` is refused.

    The structure is normalised *before* encoding rather than via ``default=``,
    because ``json.dumps`` serialises floats natively and would never consult a
    fallback for them — the one type that most needs to be rejected.
    """
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    """Hex digest of the canonical encoding of ``value``."""
    return sha256_bytes(canonical_json(value))


def chain_next(previous_head: str, entry: Any) -> str:
    """Next head of an append-only hash chain.

    ``H(previous_head || canonical(entry))``. Editing any historical entry
    changes every head after it, so a single stored head certifies the whole
    prefix. Used by the trial ledger and the live paper book.
    """
    if len(previous_head) != 64 or not all(c in "0123456789abcdef" for c in previous_head):
        raise ValueError("previous_head must be a 64-char lowercase hex digest")
    digest = hashlib.sha256()
    digest.update(previous_head.encode("ascii"))
    digest.update(canonical_json(entry))
    return digest.hexdigest()


def verify_chain(entries: Sequence[Mapping[str, Any]], *, head_key: str = "chain_head") -> int:
    """Recompute a chain and return the index of the first corrupted entry.

    Returns ``-1`` when the chain is intact. Each entry must carry its own head
    under ``head_key``; that field is excluded from its own hash input.
    """
    head = GENESIS
    for index, entry in enumerate(entries):
        body = {k: v for k, v in entry.items() if k != head_key}
        head = chain_next(head, body)
        if entry.get(head_key) != head:
            return index
    return -1


def _normalize(value: Any) -> Any:
    """Recursively convert to JSON-native types, rejecting anything ambiguous."""
    # bool before int: bool is a subclass of int and must stay true/false.
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, Decimal):
        return f"D:{value.normalize():f}"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetime cannot be canonically encoded; attach a timezone")
        return f"T:{value.isoformat()}"
    if isinstance(value, date):
        return f"d:{value.isoformat()}"
    if isinstance(value, Path):
        return f"p:{value.as_posix()}"
    if isinstance(value, float):
        raise TypeError(
            "float is not canonically hashable — use Decimal (money) "
            "or round to a fixed-precision string (statistics)"
        )
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical mapping keys must be str, got {type(key).__name__}")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, Sequence | set | frozenset):
        if isinstance(value, set | frozenset):
            # Set iteration order is not stable across processes; sort the encoded
            # members so the same set always hashes the same way.
            return sorted(canonical_json(item).decode("utf-8") for item in value)
        return [_normalize(item) for item in value]
    raise TypeError(f"cannot canonically encode {type(value).__name__}")
