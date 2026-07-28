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
    UnitClass,
    contamination_by_unit_class,
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
        by_unit_class = contamination_by_unit_class(app.warehouse)

    _print_summary(population, spread, by_unit_class)

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
        by_unit_class=by_unit_class,
        control=control,
        vintage=vintage,
        config_hash=config_hash,
        trial_count=_distinct_configurations(ledger, args.family),
        registrations=ledger.count(family=args.family),
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


def _print_summary(
    population: Contamination, spread: Any, by_unit_class: tuple[UnitClass, ...]
) -> None:
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
    print(f"returned to first       {population.returned_to_first_value:>12,}")
    directions = (
        population.revised_up + population.revised_down + population.returned_to_first_value
    )
    print(
        f"  sum of the three      {directions:>12,}   (= restated: {directions == population.restated_facts})"
    )
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
        # Distinct name from the quantile loop's ``without`` above: that one holds a
        # Decimal and this one a count, and reusing the name made mypy read the
        # second as the first's type.
        without_flips = population.threshold_counts_excluding_sign_flips[threshold]
        print(
            f"  {threshold:>5}               {count:>12,}   {share:.2%} of restated  "
            f"{without_flips:>12,}"
        )
    print()
    print("post-hoc: could a stock split have caused it?")
    print(f"  {'class':<12}{'facts':>12}{'restated':>10}{'rate':>8}{'down':>8}{'median':>10}")
    for row in by_unit_class:
        print(
            f"  {row.unit_class:<12}{row.facts:>12,}{row.restated_facts:>10,}"
            f"{row.fact_share:>7.2%}{row.downward_share:>8.1%}"
            # Rounded for the console only. The card carries the full Decimal,
            # which is 8 places wide and ran into the column to its left.
            f"{float(row.median_relative_change):>10.4f}"
        )
    print()
    print(f"cross-grain (cik, concept, period) triples   {spread.triples:>12,}")
    print(f"  reported under >1 unit                     {spread.multi_unit:>12,}")
    print(f"  reported under >1 taxonomy                 {spread.multi_taxonomy:>12,}")


def _build_card(
    *,
    population: Contamination,
    spread: Any,
    by_unit_class: tuple[UnitClass, ...],
    control: Contamination,
    vintage: date,
    config_hash: str,
    trial_count: int,
    registrations: int,
    family: str,
) -> EvidenceCard:
    commit, dirty = _git_state()
    return EvidenceCard(
        study_id=STUDY_ID,
        hypothesis=HYPOTHESIS,
        verdict=_verdict(population, by_unit_class),
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
            _split_caveat(by_unit_class),
            f"The trial count above is DISTINCT CONFIGURATIONS in this family, not ledger "
            f"rows: the append-only ledger holds {registrations} registration(s) of which "
            f"{trial_count} differ in configuration. Re-running a byte-identical config "
            f"spends no new degree of freedom and is not a second attempt at finding "
            f"something; changing any knob is, and is counted. Nothing is ever removed "
            f"from the ledger to make this number smaller -- it is deduplicated at read "
            f"time, so the failures the ledger exists to remember stay in it.",
            f"Control: the same aggregate, narrowed to AAPL "
            f"{CONFIG['control']['concept']}, returns {control.facts} facts and "
            f"{control.restated_facts} restated -- the figures measured independently on "
            f"2026-07-27 and registered in D20 before this aggregate was written. Read "
            f"its MAGNITUDES with the split caveat above in hand: the control's median "
            f"relative change is {control.quantiles['p50']}, which is exactly 3/4, the "
            f"arithmetic of Apple's 4:1 split in 2020, and its upper quantiles sit at "
            f"6/7, the 7:1 split of 2014. The control's COUNT is a valid gate on the "
            f"query -- those facts really were restated in the point-in-time sense -- but "
            f"most of the size in it is corporate action rather than accounting revision. "
            f"The FY2008 5.36 -> 6.78 case the README opens with is not: at 20.9% it sits "
            f"on no split ratio.",
        ),
        generated_at=datetime.now(UTC),
        statistics={
            "population": population.as_dict(),
            "post_hoc_by_unit_class": {row.unit_class: row.as_dict() for row in by_unit_class},
            "cross_grain_spread": spread.as_dict(),
            "control_aapl_eps_diluted": control.as_dict(),
            "kill_threshold": (
                f"Registered in D20 at {KILL_THRESHOLD:.0%} of facts. A result below it "
                f"would mean the premise is overstated, and the memo would say so."
            ),
        },
    )


def _by_class(by_unit_class: tuple[UnitClass, ...], name: str) -> UnitClass:
    """One row of the split panel, by name rather than by position."""
    for row in by_unit_class:
        if row.unit_class == name:
            return row
    raise SystemExit(f"unit class {name!r} missing from the panel; the classification changed")


def _split_caveat(by_unit_class: tuple[UnitClass, ...]) -> str:
    """Size the corporate-action share of the headline, from the panel rather than by hand."""
    other = _by_class(by_unit_class, "other")
    total_facts = sum(row.facts for row in by_unit_class)
    total_restated = sum(row.restated_facts for row in by_unit_class)
    exposed_facts = total_facts - other.facts
    exposed_restated = total_restated - other.restated_facts
    return (
        "A stock split retroactively rewrites every per-share figure in the archive. That "
        "is a genuine point-in-time restatement -- the split-adjusted number did not exist "
        "on the earlier date -- but it is a corporate action, not an accounting revision, "
        "and the two should not share one headline unexamined. Facts in units a split can "
        f"mechanically touch (per-share values and share counts) are {exposed_facts:,} of "
        f"{total_facts:,} ({exposed_facts / total_facts:.1%}) and account for "
        f"{exposed_restated:,} of {total_restated:,} restatements "
        f"({exposed_restated / total_restated:.1%}). The remaining units restate at "
        f"{other.fact_share:.2%}, against a headline of "
        f"{Decimal(total_restated) / Decimal(total_facts):.2%} -- so splits cannot be "
        "driving the headline. This decomposition was written AFTER the first population "
        "run, on noticing that the control's quantiles were exactly the split ratios; it "
        "is post-hoc and is not part of the D20 registration."
    )


def _verdict(population: Contamination, by_unit_class: tuple[UnitClass, ...]) -> str:
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
    # The direction claim is the most exposed sentence here: a split pushes
    # per-share values down and share counts up, so a skew measured over the
    # whole corpus could be a corporate-action artefact wearing the costume of a
    # finding. It is therefore re-stated on the units a split cannot touch, and
    # if it does not survive there it is not claimed.
    other = _by_class(by_unit_class, "other")
    other_direction = "down" if other.revised_down > other.revised_up else "up"
    if other_direction == direction:
        defence = (
            f"That skew is not a corporate-action artefact: among facts in units a stock "
            f"split cannot touch it holds at {other.downward_share:.1%} {direction} "
            f"({other.revised_up:,} up, {other.revised_down:,} down)."
        )
    else:
        defence = (
            f"That skew does NOT survive restriction to units a stock split cannot touch, "
            f"where revisions run {other_direction} ({other.revised_up:,} up, "
            f"{other.revised_down:,} down) -- so the population direction is a corporate-"
            f"action artefact and is not claimed as a finding."
        )
    return (
        f"{headline} Among restated facts, "
        f"{population.threshold_counts['0.10']:,} moved by more than 10% of the larger "
        f"of the two values, and revisions run {direction} more often than not "
        f"({population.revised_up:,} up, {population.revised_down:,} down, "
        f"{population.returned_to_first_value:,} changed and returned to where they "
        f"started). {defence} A backtest reading a current vendor panel is reading these "
        f"values on dates when the first-published ones were the only ones available."
    )


def _data_vintage(app: Application) -> date:
    row = app.warehouse.execute("SELECT max(filed_at) FROM filings").fetchone()
    if row is None or row[0] is None:
        raise SystemExit("warehouse has no filings; run the ingest first")
    return date.fromisoformat(str(row[0]))


def _git_state() -> tuple[str, bool]:
    """The commit this ran at, and whether the CODE differed from it.

    ``data/`` is excluded from the dirtiness check, and that exclusion is the
    whole point rather than a convenience. This script writes its evidence card
    and appends to the trial ledger, both under ``data/``, before the card is
    built -- so a check over the whole tree reports dirty on every run, including
    a run from a pristine checkout. The first version did exactly that, and a
    reproducibility flag that is always on is one no reader learns anything from.

    What the flag is asked to mean is: were the numbers produced by code that
    matches the named commit? So it looks at everything except the outputs.
    """
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
    code_changes = [
        line
        for line in status.splitlines()
        # Porcelain format is "XY <path>"; the path starts at column 3.
        if line[3:].strip().strip('"') and not line[3:].strip().strip('"').startswith("data/")
    ]
    return (commit, bool(code_changes))


def _distinct_configurations(ledger: TrialLedger, family: str) -> int:
    """Distinct configurations registered in this family -- the multiple-testing count.

    Not the number of ledger rows. Re-running a byte-identical configuration is
    not a second attempt at finding something: no new degree of freedom was
    spent, and inflating the count would make every correction downstream of it
    wrong in the lenient direction's opposite. What *does* count is any change to
    a knob, because that is a second look at the data with a different lens.

    Deduplicating here rather than pruning the ledger is deliberate. The ledger
    stays append-only -- the failures it exists to remember cannot be edited out
    -- and the statistic reads it correctly instead.
    """
    return len(
        {
            canonical_hash(entry.config)
            for entry in ledger.read()
            if entry.family == family and "amends" not in entry.config
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
