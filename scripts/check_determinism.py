"""Determinism gate: prove a re-run reproduces byte-identical results.

Determinism is not correctness. It is the property that makes correctness
*provable* -- a result that changes between runs cannot be checked by anyone,
including its author, and a P&L that moves on re-run is a defect rather than a
curiosity.

The gate runs a fixed pipeline in several subprocesses under **different**
``PYTHONHASHSEED`` values and requires the reproducibility hash to be identical.
Varying the hash seed is what exposes the realistic bug: iterating a ``set`` or
relying on ``hash()`` produces a different order in every process, and a build that
only ever ran in one process would never see it.

**Positive control.** A measurement that can only return "pass" is not a
measurement. ``--self-test`` additionally runs a deliberately poisoned pipeline --
signal values derived from ``hash()`` -- and requires the gate to *fail* on it. If
the poisoned run comes back clean, the probe is broken and the clean result means
nothing, so that is reported as an error too.

Usage::

    uv run python scripts/check_determinism.py              # the gate
    uv run python scripts/check_determinism.py --self-test  # gate + positive control
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aletheia.pit import PitPrice
from aletheia.research.evidence import ArmSummary, EvidenceCard, Provenance
from aletheia.research.kernel import SignalObservation, run_quantile_sort

SEEDS = ("0", "1", "12345", "99991")
"""Four distinct hash seeds. One process proves nothing about ordering."""

START = date(2015, 1, 1)
FORMATIONS = [date(2016, 1, 4), date(2016, 7, 4), date(2017, 1, 4)]
CAPITAL = 50_000_000.0

SYMBOLS = tuple(f"SYM{index:02d}" for index in range(20))
N_QUANTILES = 5
BUCKET = len(SYMBOLS) // N_QUANTILES
TIED_LOW = -0.10
TIED_HIGH = 0.09
"""Six firms tie at each extreme, so each traded bucket of four is chosen from six
equal values.

Placing the ties *at the quantile boundaries* is the whole point. A tie in the
middle of the sort never reaches a portfolio, so a fixture with one would report
STABLE while the tie-breaking bug it was meant to catch sat untested. Here, which
four of the six tied names get traded is decided entirely by the sort's tie-break
-- and if that falls back on input order, a set-ordered panel makes it differ in
every process."""


def _signal_values(*, poison: bool) -> dict[str, float]:
    """The clean pipeline's values are fixed; the poisoned ones come from ``hash()``.

    ``hash()`` on a string is randomised per process unless ``PYTHONHASHSEED`` is
    pinned, so the poisoned pipeline is nondeterministic by construction. It is a
    realistic bug: ``hash()`` looks like a convenient stable identifier and is not.
    """
    if poison:
        return {symbol: float(hash(symbol) % 1000) / 1000.0 for symbol in SYMBOLS}
    values = {}
    for index, symbol in enumerate(SYMBOLS):
        if index < BUCKET + 2:
            values[symbol] = TIED_LOW
        elif index >= len(SYMBOLS) - BUCKET - 2:
            values[symbol] = TIED_HIGH
        else:
            values[symbol] = (index - 10) * 0.01
    return values


def _bars(symbol: str, start: date, end: date) -> list[PitPrice]:
    drift = 1.0 + (sum(ord(char) for char in symbol) % 7) / 100.0
    bars: list[PitPrice] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            close = 100.0 * drift ** ((day - START).days / 365.25)
            bars.append(
                PitPrice(
                    symbol=symbol,
                    bar_date=day,
                    open=close,
                    high=close * 1.004,
                    low=close * 0.996,
                    close=close,
                    adj_close=close,
                    volume=750_000.0,
                    tradable_from=day,
                )
            )
        day += timedelta(days=1)
    return bars


def repro_hash(*, poison: bool) -> str:
    """Run the fixed pipeline and return the card's reproducibility hash."""
    values = _signal_values(poison=poison)
    panels: dict[date, list[SignalObservation]] = {}
    for formation in FORMATIONS:
        # Iterating a set on purpose: this is how a real panel builder goes wrong,
        # and the gate should be sensitive to it rather than shielded from it.
        panels[formation] = [
            SignalObservation(
                symbol=symbol, cik=index, value=values[symbol], knowledge_date=formation
            )
            for index, symbol in enumerate(set(SYMBOLS))
        ]

    result = run_quantile_sort(
        label="determinism",
        panels=panels,
        load_prices=lambda symbol, *, start, end: _bars(symbol, start, end),
        n_quantiles=N_QUANTILES,
        execution_lag_days=1,
        capital_usd=CAPITAL,
    )
    card = EvidenceCard(
        study_id="determinism-probe",
        hypothesis="the pipeline reproduces",
        verdict="n/a",
        provenance=Provenance(
            code_commit="fixed",
            code_dirty=False,
            config_hash="fixed",
            data_vintage=date(2017, 1, 4),
            universe_source="synthetic",
        ),
        arms=(ArmSummary.of(result, periods_per_year=2.0),),
        comparisons=(),
        trial_count=1,
        trial_family="determinism",
        caveats=("synthetic",),
        # Varies every run by design; excluded from the hash, which is the point.
        generated_at=datetime.now(UTC),
    )
    return card.repro_hash


def _run_under_seeds(*, poison: bool) -> dict[str, str]:
    """Emit the hash from a fresh process per seed."""
    hashes: dict[str, str] = {}
    for seed in SEEDS:
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--emit",
                *(["--poison"] if poison else []),
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                f"probe process failed under PYTHONHASHSEED={seed}:\n{completed.stderr}"
            )
        hashes[seed] = completed.stdout.strip()
    return hashes


def _report(label: str, hashes: dict[str, str]) -> bool:
    distinct = set(hashes.values())
    print(f"\n{label}")
    for seed, value in hashes.items():
        print(f"    PYTHONHASHSEED={seed:<6} {value}")
    stable = len(distinct) == 1
    print(f"    -> {len(distinct)} distinct hash(es): {'STABLE' if stable else 'DRIFT'}")
    return stable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--poison", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Also prove the gate can fail, by running a deliberately broken pipeline.",
    )
    args = parser.parse_args(argv)

    if args.emit:
        print(repro_hash(poison=args.poison))
        return 0

    clean_stable = _report("clean pipeline (must be stable)", _run_under_seeds(poison=False))

    failures = []
    if not clean_stable:
        failures.append("the pipeline is not reproducible across hash seeds")

    if args.self_test:
        poisoned_stable = _report(
            "poisoned pipeline (must drift -- positive control)", _run_under_seeds(poison=True)
        )
        if poisoned_stable:
            failures.append(
                "the positive control did not drift, so the probe cannot detect drift "
                "and the clean result above is not evidence of anything"
            )

    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        "PASS: identical results across hash seeds"
        + (" (probe verified)" if args.self_test else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
