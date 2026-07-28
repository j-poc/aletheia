"""Flagship study S002: how much of a modern fundamentals panel did not exist at the time.

A population statistic over the whole corpus, not a backtest. It needs no prices,
which is why it is the flagship that ships: S001 (the accrual vintage-bias study)
is built and blocked on a price entitlement that walls off 10,430 of the 10,438
tickers in the universe. See decision D20, which registered this study's method
before any aggregate here had been run.

    uv run python scripts/run_contamination_study.py

Every figure it prints is recomputed from ``data/warehouse.duckdb`` on each run
and written to an evidence card carrying the commit, the data vintage and a
reproducibility hash. Nothing is transcribed by hand.
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from aletheia.app import Application
from aletheia.core.hashing import canonical_hash
from aletheia.corpus.contamination import (
    QUANTILES,
    THRESHOLDS,
    Contamination,
    cross_grain_spread,
    measure_contamination,
)
from aletheia.research.evidence import EvidenceCard, Provenance
from trialkeeper import TrialLedger

STUDY_ID = "S002-restatement-contamination"
TRIAL_FAMILY = "restatement-contamination"
HYPOTHESIS = (
    "A material share of the fundamental facts standing in a modern panel differ "
    "from the values that were actually public at the time, so a backtest reading "
    "current vendor data is reading numbers that did not exist on the dates it trades."
)

CONFIG: dict[str, Any] = {
    "grain": ["cik", "taxonomy", "concept", "unit", "period_start", "period_end"],
    "restated_definition": "more than one distinct value across the report sequence",
    "relative_change_denominator": "greater of |first| and |latest|",
    # Fixed-precision strings, not floats: the config hash has to be stable across
    # machines and platforms, and ``canonical_hash`` rejects floats for that reason.
    "quantiles": [f"{q:.2f}" for q in QUANTILES],
    "thresholds": list(THRESHOLDS),
    "kill_threshold": "0.01",
    "control": {"cik": 320193, "concept": "EarningsPerShareDiluted", "expect": [121, 25]},
}

KILL_THRESHOLD = Decimal(CONFIG["kill_threshold"])
"""Registered in D20: below this, the premise is overstated and the memo says so."""

DATA = Path("data")
CARD_JSON = DATA / "evidence" / f"{STUDY_ID}.json"
CARD_MD = DATA / "evidence" / f"{STUDY_ID}.md"
LEDGER = DATA / "trials.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family",
        default=TRIAL_FAMILY,
        help="Trial-ledger family. Debug runs belong in their own family.",
    )
    args = parser.parse_args(argv)

    with Application.build(read_only=True) as app:
        vintage = _data_vintage(app)

        # The control first, and it gates everything after it. Running the
        # population number and *then* checking the control would mean the number
        # already existed when the check ran, which is the habit this study is
        # about.
        control = measure_contamination(
            app.warehouse,
            cik=CONFIG["control"]["cik"],
            concept=CONFIG["control"]["concept"],
        )
        expected_facts, expected_restated = CONFIG["control"]["expect"]
        print(
            f"control  AAPL {CONFIG['control']['concept']}: "
            f"{control.facts} facts, {control.restated_facts} restated "
            f"(registered: {expected_facts}, {expected_restated})"
        )
        if (control.facts, control.restated_facts) != (expected_facts, expected_restated):
            print("\nCONTROL FAILED -- the population number is wrong, not the control.")
            return 1
        print("control passed\n")

        population = measure_contamination(app.warehouse)
        spread = cross_grain_spread(app.warehouse)

    _print_summary(population, spread)

    ledger = TrialLedger(LEDGER)
    config_hash = canonical_hash(CONFIG)
    trial = ledger.register(
        hypothesis=HYPOTHESIS,
        family=args.family,
        config={**CONFIG, "config_hash": config_hash},
        registered_at=datetime.now(UTC).isoformat(),
    )

    card = _build_card(
        population=population,
        spread=spread,
        control=control,
        vintage=vintage,
        config_hash=config_hash,
        trial_count=ledger.count(family=args.family),
        family=args.family,
    )
    CARD_JSON.parent.mkdir(parents=True, exist_ok=True)
    CARD_JSON.write_bytes(card.to_json())
    CARD_MD.write_text(card.to_markdown(), encoding="utf-8")
    ledger.record_outcome(
        sequence=trial.sequence,
        outcome={"repro_hash": card.repro_hash, "verdict": card.verdict},
    )

    print(f"\ncard written to {CARD_JSON} and {CARD_MD}")
    print(f"repro hash {card.repro_hash}")
    return 0


def _print_summary(population: Contamination, spread: Any) -> None:
    print(f"facts (grain)           {population.facts:>12,}")
    print(f"  restated              {population.restated_facts:>12,}   {population.fact_share:.4%}")
    print(f"  published once        {population.facts_reported_once:>12,}")
    print(
        f"  republished at least  {population.restatable_facts:>12,}   "
        f"restated among these: {population.restatable_share:.4%}"
    )
    print(f"rows (fact-report)      {population.rows:>12,}")
    print(f"  differ from first     {population.restated_rows:>12,}   {population.row_share:.4%}")
    print()
    print(f"revised up              {population.revised_up:>12,}")
    print(f"revised down            {population.revised_down:>12,}")
    print(f"sign flips              {population.sign_flips:>12,}")
    print(f"zero <-> non-zero       {population.restated_from_or_to_zero:>12,}")
    print(f"undefined rel. change   {population.undefined_relative_change:>12,}")
    print()
    print("relative change among restated facts       (post-hoc: excluding sign flips)")
    for key, value in sorted(population.quantiles.items(), key=lambda item: int(item[0][1:])):
        without = population.quantiles_excluding_sign_flips[key]
        print(f"  {key:>4}                {value:>12}       {without:>12}")
    print("restated facts exceeding")
    for threshold, count in sorted(population.threshold_counts.items()):
        share = count / population.restated_facts if population.restated_facts else 0
        without = population.threshold_counts_excluding_sign_flips[threshold]
        print(
            f"  {threshold:>5}               {count:>12,}   {share:.2%} of restated  {without:>12,}"
        )
    print()
    print(f"cross-grain (cik, concept, period) triples   {spread.triples:>12,}")
    print(f"  reported under >1 unit                     {spread.multi_unit:>12,}")
    print(f"  reported under >1 taxonomy                 {spread.multi_taxonomy:>12,}")


def _build_card(
    *,
    population: Contamination,
    spread: Any,
    control: Contamination,
    vintage: date,
    config_hash: str,
    trial_count: int,
    family: str,
) -> EvidenceCard:
    commit, dirty = _git_state()
    return EvidenceCard(
        study_id=STUDY_ID,
        hypothesis=HYPOTHESIS,
        verdict=_verdict(population),
        provenance=Provenance(
            code_commit=commit,
            code_dirty=dirty,
            config_hash=config_hash,
            data_vintage=vintage,
            universe_source="800 filers ingested from EDGAR companyfacts (convenience sample)",
            row_counts={"facts": population.rows, "distinct_facts": population.facts},
        ),
        arms=(),
        comparisons=(),
        trial_count=trial_count,
        trial_family=family,
        caveats=(
            "The 800 filers are a convenience sample, ingested in ad-hoc batches during "
            "development and drawn in 2026 from a CURRENT ticker map -- so they are "
            "alive-today by construction. EDGAR itself is survivorship-free (the SEC "
            "never deletes a dead filer's submissions), but this SELECTION is not. Dead "
            "filers plausibly restate more than survivors, which would bias this figure "
            "DOWN. The warehouse cannot size that: the delistings table holds 100 rows "
            "spanning 2026-07-01 to 2026-07-23, and 2 of the 800 appear in it.",
            "This is a population count, not an inference. There is no sampling "
            "distribution, no p-value and no confidence interval, because nothing is "
            "being estimated from a sample of a larger frame -- every fact in the corpus "
            "is counted. It describes this corpus and generalises only as far as the "
            "selection above allows.",
            "A restatement here means the VALUE changed after first publication. It does "
            "not mean the filer was wrong, and it is not an accounting-fraud measure: "
            "reclassifications, adoption of new standards and ordinary revisions all "
            "count. Sign flips are reported separately because in XBRL they are usually "
            "a presentation convention rather than a change in the underlying figure.",
            "Unit changes and taxonomy migrations cannot appear as restatements because "
            f"unit and taxonomy are inside the grain. They instead split one economic "
            f"fact into two: {spread.multi_unit:,} of {spread.triples:,} "
            f"(cik, concept, period) triples are reported under more than one unit and "
            f"{spread.multi_taxonomy:,} under more than one taxonomy.",
            "XBRL fundamentals begin around 2009-2011, so nothing here says anything "
            "about restatement rates before then.",
            "Relative change is bounded above by 2, because the denominator is the "
            "larger of the two magnitudes and a full sign flip of equal size is "
            "therefore 2x/x. The upper quantiles of the pre-registered distribution "
            "sit at that cap, which means the tail is sign flips. A second panel "
            "repeats the quantiles and threshold counts with sign flips excluded. "
            "That panel was added AFTER the first population run, on seeing the "
            "saturation -- it is post-hoc and is not part of the D20 registration, "
            "which is why the headline is still the pre-registered figure.",
            f"Control: the same aggregate, narrowed to AAPL "
            f"{CONFIG['control']['concept']}, returns {control.facts} facts and "
            f"{control.restated_facts} restated -- the figures measured independently on "
            f"2026-07-27 and registered in D20 before this aggregate was written.",
        ),
        generated_at=datetime.now(UTC),
        statistics={
            "population": population.as_dict(),
            "cross_grain_spread": spread.as_dict(),
            "control_aapl_eps_diluted": control.as_dict(),
            "kill_threshold": (
                f"Registered in D20 at {KILL_THRESHOLD:.0%} of facts. A result below it "
                f"would mean the premise is overstated, and the memo would say so."
            ),
        },
    )


def _verdict(population: Contamination) -> str:
    """State what the numbers show, in whichever direction they fell."""
    share = population.fact_share
    headline = (
        f"{population.restated_facts:,} of {population.facts:,} facts "
        f"({share:.2%}) carry a value that changed after first publication; at "
        f"row grain {population.restated_rows:,} of {population.rows:,} "
        f"({population.row_share:.2%}) differ from the first report of their fact."
    )
    if share < KILL_THRESHOLD:
        return (
            f"{headline} That is below the {KILL_THRESHOLD:.0%} threshold registered in "
            f"D20 before this ran. Restatement contamination in this corpus is a "
            f"rounding error, and the motivating premise -- that a modern panel "
            f"materially misstates what was knowable at the time -- is NOT supported "
            f"by it. The AAPL example remains true and is not representative."
        )
    direction = "down" if population.revised_down > population.revised_up else "up"
    return (
        f"{headline} Among restated facts, "
        f"{population.threshold_counts['0.10']:,} moved by more than 10% of the larger "
        f"of the two values, and revisions run {direction} more often than not "
        f"({population.revised_up:,} up, {population.revised_down:,} down). A backtest "
        f"reading a current vendor panel is reading these values on dates when the "
        f"first-published ones were the only ones available."
    )


def _data_vintage(app: Application) -> date:
    row = app.warehouse.execute("SELECT max(filed_at) FROM filings").fetchone()
    if row is None or row[0] is None:
        raise SystemExit("warehouse has no filings; run the ingest first")
    return date.fromisoformat(str(row[0]))


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
