"""Shared parsing scaffolding.

Parsers here follow one rule: **never invent, never silently drop**. A record
that cannot be parsed is either an error (the upstream contract changed and we
want to know immediately) or a counted, reasoned skip that appears in the run
report. What must not happen is a row quietly vanishing, because a dataset that
is 3% smaller than it should be looks exactly like one that is complete.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aletheia.core.errors import ContractViolation


@dataclass(slots=True)
class ParseReport:
    """What a parse produced, and what it declined to produce.

    ``skipped`` is keyed by reason so an ingest log answers "why is coverage
    lower than expected" without re-running anything.
    """

    parsed: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def skip(self, reason: str, count: int = 1) -> None:
        self.skipped[reason] += count

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped.values())

    def summary(self) -> str:
        if not self.skipped:
            return f"{self.parsed} parsed, 0 skipped"
        detail = ", ".join(f"{reason}={count}" for reason, count in sorted(self.skipped.items()))
        return f"{self.parsed} parsed, {self.total_skipped} skipped ({detail})"

    def merge(self, other: ParseReport) -> None:
        self.parsed += other.parsed
        self.skipped.update(other.skipped)


def require_mapping(payload: Any, *, source: str, uri: str, what: str) -> dict[str, Any]:
    """Assert the payload is a JSON object, or fail with the contract violated."""
    if not isinstance(payload, dict):
        raise ContractViolation(
            f"expected a JSON object for {what}, got {type(payload).__name__}",
            source=source,
            uri=uri,
        )
    return payload


def require_key(payload: dict[str, Any], key: str, *, source: str, uri: str) -> Any:
    if key not in payload:
        raise ContractViolation(
            f"payload is missing required key {key!r}; got {sorted(payload)[:12]}",
            source=source,
            uri=uri,
        )
    return payload[key]


def parse_date(value: str | None) -> date | None:
    """ISO date, or None for the empty strings EDGAR uses to mean 'absent'."""
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_compact_date(value: str) -> date | None:
    """``YYYYMMDD``, as used by the bulk datasets and the daily index."""
    text = value.strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def parse_instant(value: str | None) -> datetime | None:
    """EDGAR's ``2026-04-30T20:30:41.000Z`` acceptance timestamps."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_decimal(value: Any) -> Decimal | None:
    """Exact decimal, or None when the value is absent or unusable.

    Accepts the ``Decimal`` produced by ``json.loads(..., parse_float=Decimal)``
    and the plain ints EDGAR uses for whole-dollar amounts. Floats are converted
    via ``str`` so the shortest round-trip representation is preserved rather
    than the binary expansion.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def split_items(raw: str | None) -> tuple[str, ...]:
    """EDGAR packs 8-K item codes into one comma-separated string."""
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())
