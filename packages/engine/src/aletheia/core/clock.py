"""Injected clock.

Wall-clock reads inside business logic are a determinism killer: the same inputs
produce different outputs on a different day, which makes a result impossible to
reproduce and therefore impossible to audit. Every component that needs "now"
takes a :class:`Clock`; only the composition root (CLI / API startup) constructs
a :class:`SystemClock`.

Tests use :class:`FrozenClock`, so a test suite run in December behaves exactly
as it did in July.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current instant. Always timezone-aware UTC."""

    def now(self) -> datetime:  # pragma: no cover - protocol
        ...

    def today(self) -> date:  # pragma: no cover - protocol
        ...


class SystemClock:
    """Real wall clock. Constructed only at the composition root."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return datetime.now(UTC).date()

    def __repr__(self) -> str:
        return "SystemClock()"


class FrozenClock:
    """Deterministic clock pinned to a fixed instant.

    ``advance`` exists so a test can simulate the passage of time explicitly,
    which is the only kind of time passage a reproducible test should contain.
    """

    __slots__ = ("_now",)

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, *, seconds: float = 0.0, days: int = 0) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds, days=days)

    def __repr__(self) -> str:
        return f"FrozenClock({self._now.isoformat()})"
