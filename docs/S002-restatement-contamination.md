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
changes. That claim is defended below against one mechanical process that would
produce it artificially -- stock splits -- and explicitly not against
reclassification, which the data cannot currently separate.

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

**Two in five restated facts moved by more than 10%** -- 128,659 of the 315,685
restatements that are not sign flips, or 40.8%. On the pre-registered basis,
which includes sign flips, it reads 170,816 of 357,842, or 47.7%. The lower,
sign-flip-excluded figure is the one quoted here: every sign flip clears a 10%
cutoff by construction, so the higher number is partly counting the presentation
conventions this memo has just finished setting aside. Both are shown because
choosing the flattering basis silently is the exact move this study is about.

Either way it is the figure that makes this a research problem rather than a
rounding detail.

The p95 and p99 sitting at exactly 2.000 is the cap, not a coincidence: a full
sign flip of equal magnitude is `2x/x`. In XBRL a sign change is usually a
presentation convention (an expense filed positive, then negative) rather than a
change in what happened, so the second column repeats the distribution with the
42,157 sign flips removed. **That column was added after seeing the first result
and is not part of the D20 registration.** The headline stays the pre-registered
figure.

Two further shapes were separated rather than left inside the distribution. Both
were added after the first run, like the panels above, and neither is subtracted
from any pre-registered figure -- these facts stay inside the headline and inside
the quantiles as D20 specified:
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

**The split objection does not survive.** Split-exposed units are 725,520 of
7,133,070 facts (10.2%) and 34,528 of 357,842 restatements (9.6%). Units a split
cannot touch restate at **5.05%**, against a 5.02% headline. Splits are not
driving it.

That closes one mechanical alternative, not the category. A second one is named
in this repo's README and is *not* tested here: prior-year comparatives
re-presented under a changed classification in a later annual report. A
reclassification is not unit-typed, so the decomposition above is blind to it by
construction. It would inflate the restatement count without being an accounting
revision in the sense a reader assumes. Sizing it needs statement-level
structure the corpus does not currently carry, and it is left open rather than
waved away.

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

**Selection, and the direction of its bias.** The 800 filers are drawn from the
SEC's `Assets/USD/CY2011Q4I` frame -- a **2011 point-in-time cross-section** --
filtered to $500M+ total assets (2,998 of the 8,166 filers in the frame
qualified) and sampled with a fixed seed. Membership is decided by 2011 filings
and nothing else, so a company that went dark in 2014 is in the sample because in
2011 it was there. **392 of the 800 filed nothing after 2024-01-01**, measured as
the last date each filer published any fact in this corpus -- the same basis the
cohort split below uses.

That makes the question that actually matters answerable: **how much would a
universe restricted to still-active filers differ from this one?** That is the
pooled headline minus the active-only rate:

| dormant if last filed before | active-only rate | pooled − active-only |
|---|---:|---:|
| 2018-01-01 | 4.97% | +0.05pp |
| 2020-01-01 | 4.86% | +0.15pp |
| 2022-01-01 | 4.85% | +0.17pp |
| 2024-01-01 | 4.85% | +0.16pp |
| 2025-01-01 | 4.86% | +0.15pp |

A universe drawn from today's index would understate this corpus by **0.05 to
0.17 percentage points**. Small either way, and the headline is not materially
deflated by that mechanism.

### What is *not* claimed, and why

Pooled, dormant filers appear to restate **more** — by +0.42pp to +0.88pp,
consistently across all five cutoffs. That looks like a finding. It is not one,
and this memo previously reported it as one.

Hold the accounting period fixed and the sign reverses in **every** band:

| period band | active | dormant | gap |
|---|---:|---:|---:|
| through 2014 | 6.33% | 6.24% | −0.08pp |
| 2015–2018 | 5.95% | 4.96% | −0.99pp |
| 2019–2022 | 3.83% | 1.66% | −2.17pp |
| 2023 onward | 2.38% | 0.02% | −2.36pp |

Both tables are arithmetically correct, which is what makes this Simpson's
paradox rather than a bug. A filer that goes dark stops producing new periods, so
its facts pile up in older bands — and older periods have had more calendar time
in which to be revised, so they restate more for a reason that has nothing to do
with dormancy. The pooled comparison reads that period mix and reports it as a
property of the cohort.

**So no dormancy effect is claimed in either direction.** Cohort and period
vintage are entangled in this corpus and it cannot separate them. The
counterfactual in the first table survives this, because it does not require
attributing the difference to anything — it asks only what an active-only
universe would have measured, which is a fact about composition rather than a
causal claim.

A cautionary note about the method, not just the result: reporting five cutoffs
looked like rigour and was rigour on the wrong axis. Varying the cutoff date
controls for one arbitrary choice while leaving untouched the confound that
decides the answer. Robustness across a parameter is not robustness.

**What the sample still cannot see.** Firms already dead before 2011Q4 are absent
entirely; firms that first listed after 2011 are absent; and the $500M floor
excludes micro-caps, whose restatement behaviour may well differ. Those are
stated, not estimated -- the corpus cannot measure companies it does not contain.

> **Two corrections, both from adversarial review, both recorded rather than
> quietly deleted** — a project whose subject is unverified claims should show
> its own.
>
> The first version of this section said the universe came from a *current*
> ticker map and was "alive-today by construction", and argued from that to the
> headline being biased down. Both halves were wrong; the selection is a 2011
> cross-section. That sentence appears to have been carried over from a real
> caveat in the *price* study, where a current ticker map genuinely is used to
> resolve symbols.
>
> The fix for it then introduced two errors of its own. It quoted the cohort
> **gap** (0.42–0.88pp) as though it were the bias an active-only universe would
> carry; the bias is that gap weighted by the dormant share of the corpus, which
> is 0.05–0.17pp — overstated three- to tenfold. And it presented the pooled
> contrast as a finding without checking whether cohort was confounded with
> period vintage. It is, and controlling for it reverses the sign. The second
> review caught both.

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

**The claims above are machine-checked against mutants.** The gate carries 29
deliberate defects, of which **19 target this study's code** -- counting rows
instead of facts, dropping `unit` from the grain, taking the smallest value
instead of the first-published one, dividing by the first value instead of the
larger magnitude, keeping sign flips in the panel that exists to remove them,
anchoring the per-share pattern so `USD/shares_unit` escapes it, inverting the
sign of the survivorship gap, quoting the cohort gap in place of the
counterfactual bias (the error corrected above, now a mutant so it cannot recur).
Each is injected into a copy of the tree and must make a named test fail. The
other 10 belong to earlier decisions.

One of them was, for a time, *intermittently* caught. The mutant that reorders
the unit-class panel by size was killed by a test whose three classes each held
one fact -- so the mutated `ORDER BY facts DESC` had a three-way tie and no
defined order, and returned the correct sequence by luck on some runs. The gate
passed four times here and failed three times under review, which is the worst
possible failure mode: a verification whose own verdict is not reproducible. The
test now uses strictly unequal class sizes, so the two orderings always differ.

**Reproducible.** Two runs of identical code at a clean tree produce the same
reproducibility hash. The exact commit and hash are not transcribed here on
purpose: any commit that edits this memo changes the commit the *next* run
reports, so a hash quoted in the memo describes a tree that no longer exists the
moment the memo is committed. The card carries both, written by the run itself --
see the Provenance block of
[`data/evidence/S002-restatement-contamination.md`](../data/evidence/S002-restatement-contamination.md).
Config hash `8196c55f22d1ef28b99423e4e51878e6f6307344862555e94c3e9e3b0d5bb6e1`,
data vintage 2026-07-27, both stable across runs. The card's clean/dirty flag was
verified in both directions: clean at a clean tree, dirty with one source file
edited.

## What was found by writing this up

Every defect below was found by reading output, running a check, or being
reviewed — none by re-reading the code that contained it. They are listed because
a study about unverified claims that hid its own would be worth less than
nothing.

Found while writing, before review:

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
   file edited. **The fix then broke it again** — it read the path from each
   `git status` line by slicing at a fixed column, but the leading space of the
   first line had already been stripped, so that one line was always misread and
   the flag went back to always-dirty. Caught by testing the flag against a tree
   where only outputs had changed. Git now does the exclusion itself.

Found by fresh-context adversarial review, in two rounds:

4. **A false claim about how the universe was selected** — see the note in
   Limitations. Reasoned from a mechanism that did not exist to a bias direction
   that was not measured.
5. **The fix for (4) conflated two different quantities.** It quoted the
   dormant-vs-active *gap* as the bias an active-only universe would carry. The
   bias is that gap weighted by the dormant share of the corpus — three to ten
   times smaller. Both are now computed, named separately, and a mutant enforces
   the distinction.
6. **The fix for (4) also missed a confound that reverses its sign.** Cohort is
   entangled with accounting-period vintage; stratifying flips the result in
   every band. The finding was withdrawn rather than shrunk.
7. **A mutation-gate result that was not reproducible.** One mutant was killed by
   a test whose fixture left a three-way tie, so it died on some runs and
   survived on others — it passed four times locally and failed three times under
   review. A gate whose verdict is not repeatable is not evidence. Fixed with
   unequal fixture sizes.
8. **A weak test caught by the gate itself.** The mutant written for (5) survived,
   because the new test's fixture had an empty active cohort — and with no active
   facts the two quantities coincide, so the mutation was invisible. The fixture
   now populates both cohorts at different rates.

Items 5 through 8 all arrived *after* the study was believed finished and
committed. That is the argument for the fresh-context review being mandatory
rather than discretionary: none of them was visible from inside the work.
