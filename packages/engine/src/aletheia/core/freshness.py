"""How old the warehouse is allowed to be before a viewer must stop trusting it.

A point-in-time system has a specific way of lying to the person looking at it.
Every page here answers "as it stands today", and *today* is the newest filing
date in the warehouse -- not the date on the reader's wall. When ingest has not
run for a month, every page keeps answering, in the same colours, with the same
confidence, about a world a month out of date. Both runs exit zero and no test
notices, because nothing is broken; the surface has simply converted "I do not
know" into "I checked".

So the freshness judgement is computed here, server-side, from an injected clock,
and shipped to the browser as a *state* rather than as a date for the reader to
subtract from today in their head. A renderer that receives ``"stale"`` cannot
accidentally present it as fresh; a renderer that receives ``"2026-07-27"`` and
is left to work it out will, on the day the contract changes, silently stop.

The four states are the ones a viewer actually needs to tell apart:

``fresh``
    Ran, and the data is inside its contract.
``stale``
    Ran fine. The data is older than the contract allows. Nothing here is wrong,
    and all of it may be out of date.
``partial``
    Ran, and some inputs are missing. What is present is real; what is absent is
    named, never rendered as zero.
``broken``
    The run failed, or the warehouse is internally inconsistent. Nothing on the
    surface is trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

FRESH_WITHIN_DAYS = 4
"""Calendar days the newest filing may lag the observation date.

Four, not one. EDGAR publishes a filing index on business days only, so a
warehouse ingested on a Monday morning legitimately holds nothing newer than the
previous Friday -- three calendar days -- and a long weekend adds a fourth. A
tighter bound would cry stale every Monday, and a surface that warns on ordinary
days trains its reader to ignore the warning, which is worse than not having one.

This is a declared contract, not a law of the system: ingest is a deliberate
command with a human behind it, so what this really measures is how long since
someone last ran it.
"""

State = Literal["fresh", "stale", "partial", "broken"]

_SEVERITY: dict[State, int] = {"fresh": 0, "partial": 1, "stale": 2, "broken": 3}


@dataclass(frozen=True, slots=True)
class Freshness:
    """The freshness verdict, and everything needed to disagree with it."""

    data_vintage: date
    observed_on: date
    age_days: int
    fresh_within_days: int
    state: State
    reason: str
    gaps: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "data_vintage": self.data_vintage.isoformat(),
            "observed_on": self.observed_on.isoformat(),
            "age_days": self.age_days,
            "fresh_within_days": self.fresh_within_days,
            "state": self.state,
            "reason": self.reason,
            "gaps": list(self.gaps),
        }


def assess(
    *,
    data_vintage: date,
    observed_on: date,
    gaps: tuple[str, ...] = (),
    fresh_within_days: int = FRESH_WITHIN_DAYS,
) -> Freshness:
    """Classify the warehouse against its freshness contract.

    ``gaps`` are missing inputs described in the words a reader needs, e.g.
    ``"prices: 0 rows"``. They are reported whatever the state, so a surface that
    is stale *and* incomplete does not hide the second fact behind the first.
    """
    age = (observed_on - data_vintage).days

    if age < 0:
        # A filing dated after the observation date. Either the clock is wrong or
        # the warehouse is, and there is no way to tell which from here -- so the
        # honest answer is that nothing on this surface can be relied on.
        return Freshness(
            data_vintage=data_vintage,
            observed_on=observed_on,
            age_days=age,
            fresh_within_days=fresh_within_days,
            state="broken",
            reason=(
                f"the newest filing is dated {data_vintage.isoformat()}, which is after "
                f"{observed_on.isoformat()}. Either the warehouse or the clock is wrong."
            ),
            gaps=gaps,
        )

    candidates: list[tuple[State, str]] = [
        (
            "fresh",
            f"the newest filing is {age} day(s) old, inside the {fresh_within_days}-day contract.",
        )
    ]
    if age > fresh_within_days:
        candidates.append(
            (
                "stale",
                f"the newest filing is {age} day(s) old and the contract allows "
                f"{fresh_within_days}. Run `make ingest`; until then every page on this "
                f"site answers as of {data_vintage.isoformat()}.",
            )
        )
    if gaps:
        candidates.append(
            ("partial", f"{len(gaps)} expected input(s) are missing: {'; '.join(gaps)}.")
        )

    state, reason = max(candidates, key=lambda pair: _SEVERITY[pair[0]])
    return Freshness(
        data_vintage=data_vintage,
        observed_on=observed_on,
        age_days=age,
        fresh_within_days=fresh_within_days,
        state=state,
        reason=reason,
        gaps=gaps,
    )
