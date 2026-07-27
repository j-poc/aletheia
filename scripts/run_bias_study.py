"""Flagship study: how much of the accrual anomaly is an artifact of restated data.

One signal -- Sloan accruals, Hribar-Collins cash-flow definition -- run three
times over an identical universe, identical rebalance dates and an identical price
panel. The arms differ only in which vintage of the fundamentals they read:

* ``first_reported``  values as originally filed, dated when the filing was public
* ``restated_values`` values as they stand today, still dated at first filing
* ``naive_vendor``    values as they stand today, dated at fiscal period end

``first_reported`` minus ``restated_values`` is the **value channel** of lookahead
bias. ``restated_values`` minus ``naive_vendor`` is the **timing channel**.

Because all three arms trade the same names on the same days, the survivorship
hole and the current-vintage ticker map affect every arm identically and cancel in
the difference. Neither cancels in the *level*, so this study makes no claim about
the size of the accrual premium -- only about the gap between the arms.

Design fixed before any result was seen:

* Universe: ``data/universe_2011.json`` (see decision D8), non-financial,
  non-utility.
* Formation: month end, from 2012-01 to the data vintage. Monthly calendar-time
  portfolios rather than 13 annual observations, which would have too few periods
  to say anything. The *signal* still updates only when a new annual report is
  filed; the portfolio is simply re-sorted monthly.
* Quintiles, equal weight, long low accruals and short high, dollar neutral.
* One-day execution lag. $100M book, so market impact is a real number.

Stages::

    uv run python scripts/run_bias_study.py --stage symbols   # who is in the study
    uv run python scripts/run_bias_study.py --stage prices    # pull their bars
    uv run python scripts/run_bias_study.py --stage study     # run it
"""

from __future__ import annotations

import argparse
import json
import subprocess
from calendar import monthrange
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from aletheia.app import Application
from aletheia.core.hashing import canonical_hash
from aletheia.core.types import Cik
from aletheia.features.vintage import FIRST_REPORTED, NAIVE_VENDOR, RESTATED_VALUES
from aletheia.pit import as_of
from aletheia.research.evidence import ArmSummary, Comparison, EvidenceCard, Provenance
from aletheia.research.kernel import run_quantile_sort
from aletheia.research.study import build_panels
from trialkeeper import (
    TrialLedger,
    deflated_sharpe_ratio,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
)

STUDY_ID = "S001-accrual-vintage-bias"
TRIAL_FAMILY = "accrual-vintage-bias"
HYPOTHESIS = (
    "The accrual anomaly measured on restated fundamentals differs materially from "
    "the same anomaly measured on the values that were actually public at the time, "
    "and the difference decomposes into a value channel and a timing channel."
)

CONFIG: dict[str, Any] = {
    "signal": "sloan_accruals_hribar_collins",
    "universe_file": "data/universe_2011.json",
    "exclude_financials": True,
    "exclude_utilities": True,
    "formation": "month_end",
    "first_formation": "2012-01-31",
    "n_quantiles": 5,
    "execution_lag_days": 1,
    "capital_usd": 100_000_000.0,
    "long_high": False,
    "periods_per_year": 12,
}

DATA = Path("data")
SYMBOLS_FILE = DATA / "study" / f"{STUDY_ID}-symbols.json"
CARD_JSON = DATA / "evidence" / f"{STUDY_ID}.json"
CARD_MD = DATA / "evidence" / f"{STUDY_ID}.md"
LEDGER = DATA / "trials.jsonl"

PRICE_START = date(2011, 1, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("symbols", "prices", "study"), required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    with Application.build() as app:
        vintage = _data_vintage(app)
        view = as_of(app.warehouse, vintage)

        if args.stage == "symbols":
            return _stage_symbols(app, view, vintage, limit=args.limit)
        if args.stage == "prices":
            return _stage_prices(app)
        return _stage_study(app, view, vintage)


# ------------------------------------------------------------------ stages --


def _stage_symbols(app: Application, view: Any, vintage: date, *, limit: int | None) -> int:
    """Resolve the universe to tradable symbols and report what the screen removed."""
    ciks = _universe_ciks(limit=limit)
    panels = build_panels(
        view,
        ciks=ciks,
        formation_dates=_formation_dates(vintage)[-1:],
        vintages=(FIRST_REPORTED,),
        exclude_financials=CONFIG["exclude_financials"],
        exclude_utilities=CONFIG["exclude_utilities"],
    )
    symbols = sorted(
        {
            observation.symbol
            for observations in panels.by_vintage[FIRST_REPORTED.name].values()
            for observation in observations
        }
    )
    SYMBOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYMBOLS_FILE.write_text(
        json.dumps(
            {
                "study_id": STUDY_ID,
                "data_vintage": vintage.isoformat(),
                "n_symbols": len(symbols),
                "symbols": symbols,
                "panel_report": {
                    "kept": panels.report.kept,
                    "firms_seen": len(panels.report.firms_seen),
                    "firms_kept": len(panels.report.firms_kept),
                    "drops": {reason.value: n for reason, n in panels.report.drops.items()},
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(panels.report.explain())
    print(f"\n{len(symbols):,} symbols written to {SYMBOLS_FILE}")
    _ = app
    return 0


def _stage_prices(app: Application) -> int:
    """Pull daily bars, recording every symbol the vendor would not serve."""
    payload = json.loads(SYMBOLS_FILE.read_text(encoding="utf-8"))
    symbols: list[str] = payload["symbols"]
    print(f"requesting bars for {len(symbols):,} symbols since {PRICE_START}", flush=True)

    outcome = app.ingestor.ingest_prices(symbols, start=PRICE_START, end=app.clock.today())
    print(outcome.summary())
    # The unreachable list IS the survivorship exposure -- recorded, not swallowed.
    unreachable = sorted(outcome.unreachable)
    (DATA / "study" / f"{STUDY_ID}-unreachable.json").write_text(
        json.dumps(
            {
                "requested": len(symbols),
                "unreachable": len(unreachable),
                "share": round(len(unreachable) / len(symbols), 4) if symbols else 0.0,
                "symbols": unreachable,
                "note": (
                    "Delisted names the price vendor will not serve on this plan. "
                    "Excluded identically from every arm, so the comparison between "
                    "arms is unaffected; the level of any single arm is not."
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"unreachable: {len(unreachable):,} of {len(symbols):,}")
    if outcome.failed:
        print(f"failed: {len(outcome.failed)}")
        for failure in outcome.failed[:20]:
            print(f"  {failure}")
    return 0


def _stage_study(app: Application, view: Any, vintage: date) -> int:
    formations = _formation_dates(vintage)
    ciks = _universe_ciks(limit=None)
    vintages = (FIRST_REPORTED, RESTATED_VALUES, NAIVE_VENDOR)

    ledger = TrialLedger(LEDGER)
    config_hash = canonical_hash(CONFIG)
    ledger.register(
        hypothesis=HYPOTHESIS,
        family=TRIAL_FAMILY,
        config={**CONFIG, "config_hash": config_hash},
        registered_at=datetime.now(UTC).isoformat(),
    )

    print(f"building panels over {len(formations)} formation dates...", flush=True)
    panels = build_panels(
        view,
        ciks=ciks,
        formation_dates=formations,
        vintages=vintages,
        exclude_financials=CONFIG["exclude_financials"],
        exclude_utilities=CONFIG["exclude_utilities"],
    )
    print(panels.report.explain(), flush=True)

    def load_prices(symbol: str, *, start: date, end: date) -> Any:
        # Bound at the data vintage, not at the formation date: prices are never
        # restated, so retrieval is not the point-in-time question. *Which* bars a
        # simulation may act on is, and the kernel enforces that itself.
        return view.prices(symbol, start=start, end=end, execution_lag_days=0)

    results = {}
    for policy in vintages:
        print(f"running arm {policy.name}...", flush=True)
        results[policy.name] = run_quantile_sort(
            label=policy.name,
            panels=panels.by_vintage[policy.name],
            load_prices=load_prices,
            n_quantiles=CONFIG["n_quantiles"],
            execution_lag_days=CONFIG["execution_lag_days"],
            capital_usd=CONFIG["capital_usd"],
            long_high=CONFIG["long_high"],
        )
        print("   " + results[policy.name].explain(), flush=True)

    card = _build_card(
        app=app,
        vintage=vintage,
        panels_report=panels.report.explain(),
        results=results,
        config_hash=config_hash,
        trial_count=ledger.count(family=TRIAL_FAMILY),
    )
    CARD_JSON.parent.mkdir(parents=True, exist_ok=True)
    CARD_JSON.write_bytes(card.to_json())
    CARD_MD.write_text(card.to_markdown(), encoding="utf-8")
    ledger.record_outcome(
        sequence=ledger.count(include_amendments=True),
        outcome={"repro_hash": card.repro_hash, "verdict": card.verdict},
    )

    print("\n" + card.to_markdown())
    print(f"card written to {CARD_JSON} and {CARD_MD}")
    return 0


# ------------------------------------------------------------------ helpers --


def _build_card(
    *,
    app: Application,
    vintage: date,
    panels_report: str,
    results: dict[str, Any],
    config_hash: str,
    trial_count: int,
) -> EvidenceCard:
    periods_per_year = float(CONFIG["periods_per_year"])
    arms = tuple(
        ArmSummary.of(result, periods_per_year=periods_per_year) for result in results.values()
    )
    by_label = {arm.label: arm for arm in arms}

    comparisons = (
        Comparison(
            name="Value channel",
            baseline=FIRST_REPORTED.name,
            variant=RESTATED_VALUES.name,
            metric="net annualised return",
            baseline_value=by_label[FIRST_REPORTED.name].net_annualised,
            variant_value=by_label[RESTATED_VALUES.name].net_annualised,
            interpretation=(
                "Same firms, same days, same prices, same publication dates -- only the "
                "values were replaced by their restated versions. Any gap is what using "
                "a modern fundamentals panel adds or removes purely through revision."
            ),
        ),
        Comparison(
            name="Timing channel",
            baseline=RESTATED_VALUES.name,
            variant=NAIVE_VENDOR.name,
            metric="net annualised return",
            baseline_value=by_label[RESTATED_VALUES.name].net_annualised,
            variant_value=by_label[NAIVE_VENDOR.name].net_annualised,
            interpretation=(
                "Both arms use restated values; the naive arm additionally dates them at "
                "fiscal period end rather than at the filing. The gap is the cost of "
                "joining a period-indexed panel to returns without a filing-date guard."
            ),
        ),
        Comparison(
            name="Combined",
            baseline=FIRST_REPORTED.name,
            variant=NAIVE_VENDOR.name,
            metric="net annualised return",
            baseline_value=by_label[FIRST_REPORTED.name].net_annualised,
            variant_value=by_label[NAIVE_VENDOR.name].net_annualised,
            interpretation=(
                "The honest result against the one an unguarded replication would report."
            ),
        ),
    )

    stats: dict[str, Any] = {}
    for arm, result in zip(arms, results.values(), strict=True):
        returns = np.asarray(result.net_returns, dtype=np.float64)
        entry: dict[str, Any] = {
            "n_periods": arm.n_periods,
            "probabilistic_sharpe": float(probabilistic_sharpe_ratio(returns)),
            "minimum_track_record_periods": float(minimum_track_record_length(returns)),
        }
        if trial_count >= 2:
            deflated = deflated_sharpe_ratio(
                returns, n_trials=trial_count, trial_sharpe_variance=_trial_variance(results)
            )
            entry["deflated_sharpe"] = float(deflated.deflated_sharpe)
            entry["expected_max_sharpe_from_luck"] = float(deflated.expected_max_sharpe)
        stats[arm.label] = entry
    stats["pbo"] = (
        "Not computed. PBO asks whether selecting the in-sample winner from a set of "
        "candidate strategies is informative. This study does not select a winner -- it "
        "reports three arms that are all shown. Running CSCV over them would produce a "
        "number with no interpretation."
    )

    verdict = _verdict(comparisons, by_label)
    commit, dirty = _git_state()
    return EvidenceCard(
        study_id=STUDY_ID,
        hypothesis=HYPOTHESIS,
        verdict=verdict,
        provenance=Provenance(
            code_commit=commit,
            code_dirty=dirty,
            config_hash=config_hash,
            data_vintage=vintage,
            universe_source=CONFIG["universe_file"],
            row_counts=_row_counts(app),
        ),
        arms=arms,
        comparisons=comparisons,
        trial_count=trial_count,
        trial_family=TRIAL_FAMILY,
        caveats=(
            "Prices for names delisted during the window are unobtainable on this data "
            "plan (decision D1). They are excluded identically from every arm, so the "
            "comparison holds; the LEVEL of any single arm is biased upward by their "
            "absence and should not be quoted as an estimate of the accrual premium.",
            "The ticker-to-CIK map is a current SEC snapshot, not a historical one, so a "
            "firm that changed symbol resolves to its present one. Same map in every arm.",
            "XBRL fundamentals begin around 2009-2011, so the sample starts in 2012 and "
            "cannot reach the period Sloan (1996) originally studied.",
            "The universe is drawn from filers with at least $500M of total assets in "
            "2011. The accrual anomaly is documented as stronger in small caps, so this "
            "sample is a conservative place to look for it.",
            "Spread is estimated from daily high-low ranges (Corwin-Schultz), not measured "
            "from quotes. Impact uses a square-root model with a literature coefficient. "
            "Both are estimates and are labelled as such.",
            panels_report.replace("\n", " "),
        ),
        generated_at=datetime.now(UTC),
        statistics=stats,
    )


def _verdict(comparisons: tuple[Comparison, ...], by_label: dict[str, ArmSummary]) -> str:
    """State what the numbers show, in whichever direction they fell."""
    value, timing, combined = comparisons
    honest = by_label[FIRST_REPORTED.name].net_annualised
    parts = [
        f"On point-in-time data the sort returned {honest:+.2%} a year net of modelled "
        f"costs over {by_label[FIRST_REPORTED.name].n_periods} monthly periods."
    ]
    parts.append(
        f"Replacing first-reported values with restated ones moved that by "
        f"{value.difference:+.2%} a year (value channel); additionally dating them at "
        f"fiscal period end moved it a further {timing.difference:+.2%} (timing channel)."
    )
    total = combined.difference
    direction = "overstates" if total > 0 else "understates"
    parts.append(
        f"An unguarded replication would therefore have reported a figure "
        f"{abs(total):.2%} a year higher than the truth, i.e. it {direction} the effect."
        if total != 0
        else "The two channels offset, leaving the naive figure close to the honest one."
    )
    return " ".join(parts)


def _trial_variance(results: dict[str, Any]) -> float:
    """Dispersion of Sharpe across the arms actually run.

    A measured input rather than an assumed one. With three arms it is a weak
    estimate, and the deflated Sharpe inherits that weakness -- which is stated
    rather than hidden behind a plausible constant.
    """
    sharpes = []
    for result in results.values():
        returns = np.asarray(result.net_returns, dtype=np.float64)
        std = float(np.std(returns, ddof=1))
        sharpes.append(float(np.mean(returns)) / std if std > 0 else 0.0)
    return float(np.var(np.asarray(sharpes), ddof=1)) if len(sharpes) > 1 else 0.0


def _formation_dates(vintage: date) -> list[date]:
    """Month ends from the configured first formation to the data vintage."""
    first = date.fromisoformat(CONFIG["first_formation"])
    dates: list[date] = []
    year, month = first.year, first.month
    while True:
        day = date(year, month, monthrange(year, month)[1])
        if day > vintage:
            break
        dates.append(day)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return dates


def _universe_ciks(*, limit: int | None) -> list[Cik]:
    members = json.loads(Path(CONFIG["universe_file"]).read_text(encoding="utf-8"))["members"]
    ciks = [Cik(int(member["cik"])) for member in members]
    return ciks[:limit] if limit else ciks


def _data_vintage(app: Application) -> date:
    row = app.warehouse.execute("SELECT max(filed_at) FROM filings").fetchone()
    if row is None or row[0] is None:
        raise SystemExit("warehouse has no filings; run the ingest first")
    return date.fromisoformat(str(row[0]))


def _row_counts(app: Application) -> dict[str, int]:
    counts = {}
    for table in ("entities", "filings", "facts", "prices", "entity_identifiers"):
        row = app.warehouse.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        counts[table] = int(row[0]) if row else 0
    return counts


def _git_state() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ("unknown", True)
    return (commit, bool(status))


if __name__ == "__main__":
    raise SystemExit(main())
