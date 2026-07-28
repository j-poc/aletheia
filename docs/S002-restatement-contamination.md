# How much of a modern fundamentals panel did not exist at the time

**Study S002. Pre-registered in [D20](decisions.md) before any aggregate was run.**
Evidence card: [`data/evidence/S002-restatement-contamination.md`](../data/evidence/S002-restatement-contamination.md).
Reproduce: `uv run python scripts/run_contamination_study.py`.

---

## Verdict

**One reported number in twenty changed after it was first published.** Across
7,133,070 distinct facts in this corpus, **357,842 (5.02%)** carry a value today
that differs from the one first filed. The premise this platform is built on --
that a research panel assembled from current vendor data silently contains
numbers that did not exist on the dates a backtest trades them -- is
**supported**, by a factor of five against the 1% threshold registered in D20
before the number was known.

The threshold was real, not decorative. Had the figure come back at 0.4%, D20
committed this memo to saying the premise was overstated and the AAPL example
unrepresentative. It came back at 5.02%.

---

## What was measured, in plain terms

Every fact filed with the SEC in XBRL carries two dates: the period it describes
(*Q3 2019 revenue*) and the date it became public. Companies re-file the same
period many times -- in later comparatives, after adopting a new standard, after
finding an error. Each re-filing is stored separately here, so the archive holds
the whole sequence of what a given number was said to be, in order.

The question is simply: **for how many facts does the end of that sequence differ
from its start?** That difference is the gap between what a modern data panel
shows and what was actually knowable at the time.

A *fact* is one `(company, taxonomy, concept, unit, period start, period end)`
tuple. It is *restated* if more than one distinct value appears across its report
sequence.

---

## Results

| | count | share |
|---|---:|---:|
| Distinct facts | 7,133,070 | |
| …restated | **357,842** | **5.02%** |
| …published only once (cannot restate) | 3,294,253 | 46.2% |
| …republished at least once | 3,838,817 | |
| …restated, among those | 357,842 | **9.32%** |
| Fact-report rows | 13,447,437 | |
| …differing from the fact's first report | 516,187 | 3.84% |

Both grains are reported because the choice between them is a place a headline
could be inflated. Counting rows scales the number by how often filers happen to
republish, which is not what the claim is about; fact grain is the headline, and
the row figure is shown next to it rather than omitted.

### Direction

| | count |
|---|---:|
| Revised up | 156,058 |
| Revised down | 191,687 |
| Changed and returned to the first value | 10,097 |
| **Total** | **357,842** |

The three sum to the restated count exactly. The third row is easy to overlook
and worth naming: a fact can change and change back, which leaves the two
endpoints equal while a backtest reading a middle vintage saw something else.

Revisions run **down** more often than up, 55.1% to 44.9% of directional
changes. That claim is defended below rather than asserted, because there is a
mechanical process that would produce it artificially.

### Size of the changes

Relative change is `|latest − first| ÷ max(|first|, |latest|)`. The denominator
is the larger magnitude, which bounds the measure at 2.

| | pre-registered | excluding sign flips |
|---|---:|---:|
| p50 | 0.085 | 0.051 |
| p75 | 0.571 | 0.307 |
| p90 | 1.289 | 0.778 |
| p95 | **2.000** | 0.994 |
| p99 | **2.000** | 1.000 |

| restated facts moving more than | count | share of restated | excl. sign flips |
|---|---:|---:|---:|
| 1% | 260,548 | 72.8% | 218,391 |
| 5% | 200,711 | 56.1% | 158,554 |
| 10% | 170,816 | 47.7% | 128,659 |

**Nearly half of all restated facts moved by more than 10%.** That is the figure
that makes this a research problem rather than a rounding detail.

The p95 and p99 sitting at exactly 2.000 is the cap, not a coincidence: a full
sign flip of equal magnitude is `2x/x`. In XBRL a sign change is usually a
presentation convention (an expense filed positive, then negative) rather than a
change in what happened, so the second column repeats the distribution with the
42,157 sign flips removed. **That column was added after seeing the first result
and is not part of the D20 registration.** The headline stays the pre-registered
figure.

Two further shapes were separated rather than left inside the distribution:
**8,610** facts went from zero to a value or a value to zero -- an appearance or
disappearance rather than a revision, and every one of them sits at exactly 1.0
by arithmetic. **84** have zero at both ends, so the relative change has no
denominator; they are counted rather than dropped.

---

## The obvious objection, and what it costs

**A stock split rewrites every per-share figure in the archive.** When a company
splits 4-for-1, its historical EPS is retroactively divided by four everywhere.
That is a genuine point-in-time restatement -- the split-adjusted number did not
exist on the earlier date, and a backtest using it is using information from the
future -- but it is a corporate action, not an accounting revision, and letting
the two share one headline would overstate what the headline means.

This objection surfaced from the data rather than from a checklist: the AAPL
control's median relative change is exactly 0.75, and its upper quantiles sit at
0.857. Those are 3/4 and 6/7 -- the arithmetic of Apple's 4:1 split in 2020 and
7:1 in 2014.

Splitting the corpus by whether a split could mechanically have caused the
change:

| unit class | facts | restated | rate | revised down | median change |
|---|---:|---:|---:|---:|---:|
| per-share | 301,033 | 18,276 | 6.07% | 61.8% | 0.197 |
| share count | 424,487 | 16,252 | 3.83% | 37.3% | 0.404 |
| **other** (mostly currency) | **6,407,550** | **323,314** | **5.05%** | **55.6%** | **0.076** |

**The objection does not survive.** Split-exposed units are 725,520 of 7,133,070
facts (10.2%) and 34,528 of 357,842 restatements (9.6%). Units a split cannot
touch restate at **5.05%**, against a 5.02% headline. Splits are not driving it.

The panel also confirms it is finding what it claims to: a split divides
per-share values and multiplies share counts, and the two classes skew in exactly
opposite directions (61.8% down against 37.3% down) while the classification
never sees a price or a corporate-action feed.

This decomposition is likewise **post-hoc** and outside the D20 registration.

### The direction claim, defended

Because a split pushes per-share values down, a downward skew measured over the
whole corpus could be a corporate action wearing the costume of a finding. The
skew is therefore re-measured on the units a split cannot touch: **55.6% down
there, against 55.1% for the population.** It holds. Had it not, the study's code
would have said so and withdrawn the claim -- both branches were written before
the number was known.

---

## What this does not say

- **It is not a return prediction.** Nothing here says restatements are tradable.
  That question is S001, which is built and blocked on a survivorship-free price
  panel; every free source walls off delisted names. This study was chosen as the
  flagship precisely because it needs no prices.
- **It is not a fraud measure.** Reclassifications, new accounting standards and
  ordinary corrections all count as restatements here. A restatement does not
  mean the filer was wrong.
- **It is not an inference.** Every fact in the corpus is counted, so there is no
  sampling distribution, no p-value and no confidence interval. It describes this
  corpus.
- **It says nothing before ~2009.** XBRL fundamentals begin around 2009-2011.

## Limitations that could move the number

**Selection, and the direction of its bias.** The 800 filers were ingested in
ad-hoc batches during development from a *current* ticker map, so they are
alive-today by construction. EDGAR itself is survivorship-free -- the SEC never
deletes a dead filer's submissions -- but this selection is not. Dead companies
plausibly restate more than survivors, so the true figure is more likely above
5.02% than below it. The warehouse cannot size this: the delistings table holds
100 rows spanning three weeks, and 2 of the 800 appear in it. Stated, not
estimated.

**Unit and taxonomy changes are invisible by construction.** Both are inside the
grain, so a fact that migrates between them becomes two facts rather than one
restatement. That is deliberate -- a number is not restated because it moved
taxonomy -- but it is an exclusion, so its size is reported: 5,825 of 7,126,884
`(company, concept, period)` triples appear under more than one unit, and 19
under more than one taxonomy.

---

## Why the result is trustworthy

**The method was fixed before the answer was known.** D20 was committed at
`ae63862`, before any population aggregate ran. It fixed the grain, the
restatement definition, the relative-change denominator, the quantiles, the
thresholds, and the 1% kill threshold.

**A control gates the number, and runs first.** The same code path, narrowed to
AAPL diluted EPS, must return 121 facts and 25 restated -- measured independently
on 2026-07-27 and registered in D20. It runs *before* the population aggregate,
so the population number does not exist when the check is made, and a failure
aborts the study. It has passed on every run.

The control's *count* is a valid gate. Its *magnitudes* are mostly splits, per
the section above, and the card now says so where the control is quoted. The
FY2008 5.36 → 6.78 case the README opens with is not a split: at 20.9% it sits on
no split ratio.

**The measurement code cannot see the future.** `aletheia.corpus` reads every
vintage at once, which is exactly what a feature must never do, so the import
boundary forbids `features/`, `research/` and `book/` from importing it -- the
same treatment `aletheia.store` gets, enforced by a test. That guard is what
moved this module: it was written in `research/` and the guard failed the build.
The boundary was tightened rather than bent.

**Every claim above is machine-checked against a mutant.** 24 deliberate defects
-- counting rows instead of facts, dropping `unit` from the grain, taking the
smallest value instead of the first-published one, dividing by the first value
instead of the larger magnitude, keeping sign flips in the panel that exists to
remove them, anchoring the per-share pattern so `USD/shares_unit` escapes it --
are each injected into a copy of the tree, and each must make a named test fail.
All 24 are caught.

**Reproducible.** Two runs of identical code at a clean tree produce the same
reproducibility hash, `53f5477a0dd5a73a90df27873ac84e494106e2970c641368d240277f78d9014f`,
at commit `60898b55c635b324440db85524a9eb980c64d4a4`, config hash
`8196c55f22d1ef28b99423e4e51878e6f6307344862555e94c3e9e3b0d5bb6e1`, data vintage
2026-07-27.

## What was found by writing this up

Three defects, each surfaced by reading output rather than code, and each fixed
before this memo was finished:

1. **A documented claim that was false.** The undefined-relative-change bucket
   was documented as existing for `0` versus `−0`, "which DuckDB treats as
   distinct decimals". It does not -- DuckDB normalises negative zero in
   `DECIMAL`. Checked against the database rather than reasoned about. The real
   route is a fact reported 0, then 5, then 0.
2. **Direction figures that did not reconcile.** 156,058 + 191,687 came to
   347,745, which is 10,097 short of the restated count. The missing facts
   changed and changed back.
3. **A reproducibility flag that could never read clean.** The study writes its
   own card and ledger before checking `git status`, so every run reported
   "dirty tree", including runs from a pristine checkout. A flag that is always
   on is one no reader learns anything from. Now it ignores the study's own
   outputs, and it was verified two-sided: clean at HEAD, dirty with one source
   file edited.
