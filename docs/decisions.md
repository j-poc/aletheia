# Decision record

Decisions are written here at the time they are taken, with the reversal cost, so
they are not re-litigated from memory later. Superseded entries are struck through
and kept — the fact that a decision changed, and why, is part of the record.

---

## D1 — Survivorship: measure and disclose, do not hide

**Taken:** 2026-07-27. **Reversal cost:** low (adapter swap).

Delisted-name prices are not obtainable from this machine. Four sources were
probed live: FMP returns `402 premium` for delisted symbols, Yahoo's chart
endpoint `404`s them, Stooq puts a JavaScript proof-of-work wall in front, and
Finnhub/AlphaVantage candles are premium. The Bloomberg gateway `404`s on
`/healthz`.

Fundamentals and the filer universe *are* survivorship-free — the SEC never
deletes a dead company's filings. Only prices are affected.

Rather than quietly running on the names that happen to have prices, every symbol
the vendor refuses raises `DelistedCoverageError` and is counted as `unreachable`.
Backtests report the exclusion count and reason breakdown as part of the result
(`BacktestResult.exclusions`). The price source sits behind the `PriceSource`
protocol so a survivorship-free vendor drops in without touching research code.

---

## D2 — DuckDB, not Postgres

**Taken:** 2026-07-27. **Reversal cost:** moderate (SQL is portable; one migration layer).

Colima is down, so no container-hosted Postgres. A single-file columnar store is
also the better reproducibility substrate: the warehouse is one file that can be
hashed, and there is no server state to differ between runs.

---

## D3 — `git init` inside `aletheia/`

**Taken:** 2026-07-27. **Reversal cost:** none.

The workspace root is not a repository. Evidence cards need a commit hash, and a
repository also permits worktree isolation, which matters because another live
session shares this directory.

---

## D4 — Universe: ingest broad, select by theory

**Taken:** 2026-07-27. **Reversal cost:** none (configuration).

All filers' metadata is cheap and complete. Fundamentals are pulled in full for
the study universe — every concept, not only the ones a signal needs — because
the revision history across all concepts is what makes the warehouse worth having,
and because narrowing the pull to what today's hypothesis wants is how a dataset
becomes single-purpose.

Study membership (see D7) is decided afterwards by economic reasoning, in the
study code, where it is visible.

---

## D5 — Centre of gravity is the evidence engine

**Taken:** 2026-07-27. **Reversal cost:** none.

Surveillance is a delivery layer, not the claimed source of edge. Real-time filing
alerting is commoditised and Bloomberg wins it. What a terminal licence forbids is
bulk systematic extraction into a research substrate you own — that is the durable
asset, and it is where the build's weight goes.

---

## ~~D6 — Flagship study: restatement magnitude as a return predictor~~ SUPERSEDED

**Taken:** 2026-07-27. **Superseded:** 2026-07-27, before any study code was written.

The original plan named restatement/revision magnitude as the flagship hypothesis,
on the reasoning that it cannot be tested at all on a flat vendor panel and is
therefore a demonstration of the platform rather than a re-run of a known result.

**Why it was dropped.** The revision set is not what the name suggests. Measured on
the ingested AAPL history (449 value changes, `scripts/` diagnostic):

| Population | n | share |
|---|---:|---:|
| In the annual re-presentation window (340–390 days) | 260 | 57.9% |
| Filed on an `/A` amendment | 62 | 13.8% |
| Revised within 75 days of the prior report | 2 | 0.4% |

Nearly three in five "revisions" sit one year after the prior report on a plain
`10-K` or `10-Q`. That is the next periodic report re-presenting the prior year's
comparative column under a changed classification — not the company restating
anything. A study of "revision magnitude predicts returns" over this population is
substantially a study of whether the company filed its next annual report, which is
close to a constant.

The genuine-restatement population is `/A` amendments and 8-K Item 4.02
non-reliance filings: 14% of the set, and concentrated in distressed names. Those
are precisely the names for which prices are unobtainable under D1. So the
survivorship hole is not a footnote for this hypothesis — it removes the
observations that would carry the effect.

---

## D7 — Flagship study: measure the lookahead bias itself

**Taken:** 2026-07-27. **Reversal cost:** none (replaces D6 before implementation).

Run one well-established accounting signal — Sloan (1996) accruals, computed by the
Hribar & Collins (2002) cash-flow method — over an **identical** universe, identical
rebalance dates and an identical price panel, under three data-vintage policies:

| Arm | Values | Dated | Isolates |
|---|---|---|---|
| `first_reported` | as originally published | at the filing date | the control: what a practitioner had |
| `restated_values` | as they stand today | at the *first* filing date | the value channel |
| `naive_vendor` | as they stand today | at period end | value **and** timing channels |

The difference between arms is the bias attributable to data vintage.

**Why this dominates the D6 hypothesis here.**

- **Survivorship cancels in the difference.** All three arms trade the same names on
  the same days; only the signal values differ. The delta is clean even though
  neither level is, which is exactly the repair the D1 stamp cannot make.
- **Every firm-year contributes.** No rare-event sample-size problem, and no need to
  ingest hundreds of names hoping for a handful of Item 4.02 filings.
- **A well-replicated effect is a measuring instrument.** Because the accrual anomaly
  is not in doubt, a difference between arms is attributable to the vintage rather
  than to whether the effect exists at all.
- **The plumbing already exists.** `PitView.unsafe_latest_restated` was built for this
  and is named to be greppable; the vintage policies in `features/vintage.py` are a
  thin layer over it.

The restatement-event study becomes a follow-up, to be run when a price source
covering delisted names is available. That is a spend decision reserved for the maintainer.

---

## D8 — Study universe drawn from a 2011 SEC cross-section

**Taken:** 2026-07-27. **Reversal cost:** none (re-run `scripts/select_universe.py`).

Index-constituent endpoints are premium on the current FMP subscription, and a
modern constituent list would be survivorship-biased by construction.

The universe is instead drawn from the SEC's own `frames` endpoint for
`us-gaap/Assets/USD/CY2011Q4I` — every filer that reported total assets for a period
ending in Q4 2011. Membership is decided by 2011 filings and nothing else, so a
company that failed in 2014 is in the list.

Rule, fixed before any result was seen: filers with total assets ≥ $500M (a
materiality floor, so the sample is not dominated by shells with sparse XBRL), then
a random sample of 800 with seed 20111231 — the as-of date of the cross-section, so
the seed is not a free parameter. Largest-N was rejected: it tilts to
capital-intensive firms and banks.

Observed: 8,166 filers in the frame, 2,998 above the floor, 800 sampled.

Sector exclusions (financials, utilities — for which accruals are not comparable)
are applied at study time, not at ingest, per D4.

---

## D9 — The flagship study is blocked on price entitlement, and is reported blocked

**Taken:** 2026-07-27. **Reversal cost:** none — it is a spend decision, reserved for the maintainer.

Everything the study needs is built and tested. It did not run, and the reason is
data availability rather than code.

**What happened.** The 800-filer fundamentals ingest completed: 800 entities,
1,364,574 filings, 13,447,437 facts. Symbol resolution produced 226 tradable
names (311 financials and utilities screened out per the accruals literature, 261
filers with no listed common stock — bond issuers, trusts and subsidiaries that
file with the SEC but have no equity to trade). The price stage then requested
226 symbols from FMP and got 8 before the account's **daily quota** was exhausted:

```
{"Error Message": "Limit Reach . Please upgrade your plan ..."}
```

Two alternatives were probed live and neither is usable: Yahoo's chart API now
requires a cookie-and-crumb handshake and returns `429` to this address on the
crumb endpoint itself; Stooq puts a JavaScript proof-of-work wall in front of its
CSV. Both were already recorded as unavailable during the original data survey.

**Why the study was not run anyway.** Eight symbols cannot populate five
quantiles. A result from them would be noise dressed as a finding, and publishing
it would be exactly the unlabelled stand-in the standards forbid. The three-arm
comparison is only meaningful over a universe wide enough for a cross-sectional
sort, so a thin run does not "partially" answer the question — it answers a
different one and looks the same.

**What unblocks it**, in order of preference:

1. **Wait for the quota to reset** and re-run `--stage prices`. Free, and the
   circuit breaker now stops the batch after five consecutive failures instead of
   grinding through the remainder, so a second exhaustion is visible in seconds.
2. **A paid price source.** Sharadar, Norgate, QuantRocket or an FMP upgrade,
   roughly $50–200/month. This also closes the D1 survivorship hole, which the
   free tier never can. **Reserved for the maintainer — it is a spend decision.**

**What was fixed because of this.** The run took twenty-two minutes to accomplish
nothing and exited `0`. `429` is in the retryable set, so each of the remaining
221 symbols was retried four times with backoff against a quota that was never
coming back, and the outcome read as poor coverage rather than as an entitlement
failure. `ingest_prices` now trips a circuit breaker after
`CONSECUTIVE_FAILURE_LIMIT` consecutive failures and records `aborted_after` with
the reason and how far it got. A per-name `DelistedCoverageError` deliberately
does **not** count toward the breaker — that is the survivorship measurement, and
tripping on it would replace the count the system exists to report with an abort
message.

---

## D10 — A period start takes three values, not two

`period_start=None` was doing two jobs. As a query argument it meant "do not
narrow by start date". As a stored column it meant "this is a balance-sheet
instant, measured at a moment, and has no start date at all". Both readings are
natural and they contradict each other, so a caller holding an instant had no way
to ask for it: passing its own `period_start` back passed `None`, which widened
the query to every period sharing that end date.

Two meanings need three states. `INSTANT`, a sentinel in `pit/view.py`, is the
third, and `PitFact.pin` hands back whichever of the two a fact needs. A bound
parameter cannot carry it -- `period_start = NULL` is never true in SQL, so
passing `None` as a parameter matches nothing rather than matching the instant --
which is why the predicate is built rather than parameterised.

**Measured, not assumed.** 590 `(cik, taxonomy, concept, unit, period_end)`
groups in the warehouse hold an instant *and* a duration -- 222 filers, 257
distinct concepts -- so the collision is real rather than theoretical. Arconic (CIK 4281) tagged `NumberOfReportingUnits`
for 2017 as a duration in the 2018 10-K and as an instant in the 2019 10-K; both
now sit under the same end date. Separately: `Assets` is an instant in all 73,844
of its facts across 800 filers, and the flow concepts are durations in all of
theirs, which is why the balance-sheet reads in `features/accruals.py` now name
`INSTANT` explicitly.

**What it cost to not have it.** Three defects, all downstream of the same
overload:

1. `features/vintage.py` handed `first.period_start` to its second lookup, so the
   restated arm re-widened a query it had just narrowed and raised on it.
2. The API had no way to express the instant at all, so the ambiguity error told
   the caller to pass a parameter the endpoint did not accept.
3. Worse than either: `unsafe_latest_restated` reads the **full** vintage, so a
   filing made years later could add a second period under an end date and turn a
   question that was answerable on its knowledge date into a `400`. AAR Corp's
   `ProfitLoss` ending 2018-11-30 was one period on 2018-12-19 and two after the
   2019-03-20 10-Q. Future data reaching backwards inside the one component whose
   entire claim is that it cannot.

**Why a sentinel and not a separate method.** `facts()`, `first_reported()` and
`unsafe_latest_restated()` all narrow the same way; a parallel set of
`*_at_instant` methods would have doubled the surface and left the two families
free to drift. One shared predicate builder, one sentinel, four signatures.

*Reversal cost: moderate.* The sentinel is in the public signature of the PIT
layer and in the HTTP contract (`period_start=instant`), so removing it would
break callers. It is additive, though -- `None` and a date behave exactly as
before, which is asserted directly.

---

## D11 — "Restated" is two questions, and answering them as one cries wolf

**Taken:** 2026-07-27. **Reversal cost:** low — one added response field; the two existing ones keep their names.

A later filing *superseding* an earlier one and a later filing *changing the
number* are different events. The API answered only the first and named it
`is_restated`, and the viewer rendered that as a restatement warning.

**Measured over the warehouse.** Of 3,956,913 `(first report, later report)`
pairs — same `(cik, taxonomy, concept, unit, period_start, period_end)` — the
value is unchanged in **3,586,802 of them, 90.6%**. Only 370,111 moved. The
overwhelming majority of refilings are the next periodic report re-presenting a
prior period's comparative column, which is the same finding that killed D6 as a
hypothesis, now applied to the card that describes it. Warning on all of them
puts a restatement flag on nine periods in ten that never had one, and a reader
who learns the flag means nothing stops reading it.

**Three fields, because there are three questions.**

| Field | Asks | Basis |
|---|---|---|
| `is_restated` | does a later document supersede the one this date would have had | accession |
| `value_changed` | did the number move | `Decimal` equality of the two values |
| `already_restated_by_then` | was the value *already* revised by the knowledge date | `Decimal` equality |

`already_restated_by_then` was accession-based and is now value-based, which is
what its name always claimed. Accession-based it fired on any period whose next
annual report had been filed — after a year, nearly all of them — and the left
card then told the reader a restatement had already been published on a figure
that never moved. Observed on AAR Corp's `ProfitLoss` for the six months ending
2018-11-30: $22.1M filed 2018-12-19, the identical figure carried forward under
accession `0001104659-19-074491` on 2019-12-20, and the card claiming a
restatement at any knowledge date after that.

**Why not compare `relative_drift` to zero.** That was the first fix and it is
wrong in two directions, because drift is `None` whenever the first report was 0
— there is no denominator. **123,177** refilings in the warehouse are `0 -> 0`,
and a null-drift check reports every one as a restatement. **2,924** first
reports of 0 later moved to a non-zero figure — Arconic first reported $0 of
discontinued-operations income for FY2018 on 2019-02-21 and restated it to $333M
on 2021-02-16 — and those carry the same null. One comparison, two opposite
cases, indistinguishable. Comparing the values has neither blind spot, and
`Decimal` equality ignores the storage scale, so `22100000` and `22100000.00`
compare equal where their rendered strings might not.

**Server-side, not in the page.** The page could subtract the two figures it
already displays, but they cross the wire as display strings through `plain()`,
so a formatting change would silently flip a claim about whether a company
restated its accounts. The comparison belongs where the `Decimal`s are, and it is
then also right for every other consumer of the endpoint.

**One further defect the same overload caused.** The card's first branch keyed on
`is_restated`, which goes *false* once the restated filing is also the latest one
a reader would have had — the accessions match. On the AAPL FY2008 fixture that
put "this period was never restated" on the exact case the system was built to
show, at any knowledge date after the restatement became the current report. The
branch now keys on `value_changed`, so a period whose value moved reads as
restated regardless of which document is currently on top.

---

## D12 — A revision off a zero base passes every magnitude threshold

**Taken:** 2026-07-27. **Reversal cost:** low — one SQL predicate. Same defect class as D10 and D11.

`PitView.revisions(min_relative_change=x)` required `prior_value <> 0` before
comparing the ratio, which silently dropped every revision whose original figure
was zero. Undefined was being read as *small*, and it is the opposite: a move off
zero is unbounded.

The API applies this filter **by default** (`min_change=0.05`), so the omission
was live on the revision explorer, not latent. Measured: **4,449** of the
warehouse's **394,320** revisions start from zero and were therefore invisible at
every threshold, including Arconic restating equity in affiliates for 2017 from
$0 to $1.02bn (published 2019-02-21) and discontinued-operations income for 2018
from $0 to $333M (published 2021-02-16). A user filtering for large revisions was
being shown strictly fewer of them than a user asking for all revisions, which
inverts what the control means.

A zero base now passes any threshold. The reported `relative_change` stays
`None` — there is still no ratio to report — and the table renders an em dash,
which is the honest rendering of an undefined quantity and is already how the
column handles it. The non-revision case is unaffected: `0 -> 0` is excluded by
the `value <> prior_value` predicate that governs the whole query, and that is
asserted directly rather than assumed.

This is the third instance of the same underlying mistake: a quantity that has no
denominator was being encoded as a value that compares equal to "nothing
happened". D10 was `period_start=None` meaning both "no filter" and "instant";
D11 was `relative_drift=None` standing in for "the value did not change"; this is
the same null standing in for "the change was small".

## D13 — "Already restated" does not say *which* restatement you are looking at

**Taken:** 2026-07-27. **Reversal cost:** low — one API field, one caption, one paragraph tail.

`already_restated_by_then` (`known.value != first.value`, D11) answers whether
the figure a reader held on the knowledge date had moved off its original. The
as-of card then used it to claim something stronger: that the left column showed
the figure standing today. Those coincide only when a period was revised once.

Measured on the 800-filer warehouse: **17,296** of **357,101** revised periods —
**4.8%** — carry three or more distinct values. On those, between two revisions,
the left column shows a figure that is neither the original nor the current one,
under a sentence saying it matched the right column. Observed live before the
fix, Morgan Stanley noninterest expense for Q3 2011 asked at 2012-06-01:

```
first reported   6214000000   2011-11-07
as known         6154000000   2012-02-27      <- left column
as it stands     6115000000   2013-02-26      <- right column
copy rendered: "the restatement was already public — so the left column shows it too"
```

Three different numbers on screen and a claim that two of them were the same.

The card now branches three ways — original / intermediate / current — on a new
`known_is_current` field.

**Why the field compares values and not accession numbers.** The discriminating
case is a re-presentation *after* the last revision: a later filing repeats the
current figure under a new accession, so the accessions differ while the numbers
agree. Compared by accession the card would tell a reader looking at $1.444bn,
beside a column also showing $1.444bn, that they held an intermediate figure —
and since 90.6% of refilings carry the value forward unchanged (D11), that is the
common case, not the corner. This is D11's error a second time and it is pinned
by a test that fails under the accession-based implementation: an earlier version
of that test passed under both and was not evidence of anything.

The fixture is Apple's other current assets at 2009-09-26 — $6.884bn, cut to
$3.140bn by the same 10-K/A of 2010-01-25 that moved FY2008 diluted EPS from 5.36
to 6.78, then cut again to $1.444bn on 2010-07-21, with the FY2010 10-K repeating
$1.444bn unchanged. One real chain covering all three states plus the
re-presentation.

**Also closed here:** `Revision.relative_change` raises rather than returning a
ratio when the base is zero, and D12 made those rows reachable through the
*filtered* path for the first time. The API guards and the web table renders an
em dash; the CLI catches the exception and prints `from 0`, but nothing tested
it, so a later tidy-up could have deleted the `except` and taken
`aletheia revisions` down on any zero-base ticker. `tests/unit/test_cli.py` now
covers that path — the first test of the CLI's rendering layer, which had none.

## D14 — "Restated" is a property of the chain, not of two points on it

D11 established that a refiling is not a restatement. It fixed the HTTP surface.
Two other consumers were never audited, and both were wrong in the same way — and
a third question turned out to be unanswerable from any pair of points at all.

**The CLI said "restated" on 5,798,180 figures that never moved.** `aletheia asof`
marked a row with `report_seq > 1`, and `report_seq` counts *documents*. Measured
against the 800-filer warehouse:

```
rows the CLI marks '← restated' : 6,314,367
of which the value never moved  : 5,798,180  (91.8%)
```

This is the first command the README tells a reader to run, and it fired on that
very screen — the FY2025 Q2 revenue rows below appear again in the following
year's 10-Q as prior-year comparatives, same $95.359bn, different accession:

```
    2025-03-29            219.659B  2026-05-01    2  0000320193-26-000013  ← restated
    2025-03-29             95.359B  2026-05-01    2  0000320193-26-000013  ← restated
```

`features/accruals.py` had the same defect in `uses_restated_input`
(`any(item.report_seq > 1 ...)`), which under the restated vintage is true of
every input by construction and therefore distinguished nothing.

**And a period can be revised and then revised back.** Every flag on the as-of
endpoint compares two points — first against latest, or known against one of
them. All of them are blind to a chain that returns to where it started, because
its endpoints are equal. AAR Corp's accrued current liabilities at 2021-05-31:

```
 1  174,200,000  2021-07-21  10-K
 2  174,200,000  2021-09-23  10-Q
 3  148,300,000  2021-12-21  10-Q
 4  148,300,000  2022-03-22  10-Q
 5  174,200,000  2022-07-21  10-K
```

`value_changed` is false there, so the page fell through to the re-presentation
branch. Observed live at `knowledge_date=2022-01-01`, verbatim from the rendered
HTML:

```
As known on 2022-01-01   148300000 USD
   "...an intermediate figure — neither the original nor the one standing today."
As it stands today       174200000 USD
   "This period was re-presented, not revised. AAR CORP reported 174200000 on
    2021-07-21, and a later filing on 2022-07-21 carried the same figure forward
    under a different accession. The value never moved; only its source document
    did."
```

Two visibly different numbers, one sentence saying neither had moved — and the
sentence directly above it, added in D13, correctly calling the left column an
intermediate figure. The page contradicted itself in adjacent paragraphs.

```
revised us-gaap periods (>=2 distinct values) : 357,101
first value == last value                     :  10,080  (2.82%)
```

**Decision.** Both questions move into the view (migration 005), because both are
properties of the whole chain and neither is derivable from a pair of rows:

- `differs_from_first_report` — this fact's value is not the one first published
  for its period. Replaces `report_seq`-based tests in the CLI and in accruals.
- `period_distinct_values` — how many values the period has ever carried.
  Surfaces as `PitFact.value_ever_changed` and as `value_ever_changed` on the
  as-of endpoint, and gates the two page branches that assert nothing moved.

The window for the count deliberately has no `ORDER BY`: an ordered window makes
it cumulative, which answers "how many values had appeared by this row" — 1 on
the first row of every revised period. The comparison uses `IS DISTINCT FROM`
rather than `<>` so a null can never produce a null boolean that reads as "not
restated", which is the failure this migration exists to end.

The page gains a fourth branch for the shape none of the other three could
describe. `revisions()` already read the chain correctly — LAG over consecutive
publications — and emits both legs of an A→B→A; it needed no change, and it is
the pattern the rest of the codebase should have followed.

**Fixtures are real and each carries its own discriminator.** Apple's Q2 FY2025
revenue, republished unchanged, for the CLI marker — paired with Morgan Stanley's
FY2011 current long-term debt (0 → $35.082bn) so the fix cannot degenerate into
"never say restated". AAR Corp's accrued liabilities for the A→B→A, paired with
AAR's total equity for the same period, filed four times at $974.4m without
moving, so `value_ever_changed <- True` cannot pass. In accruals, Apple
republished FY2009 operating cash flow in the 10-K/A at the same $10.159bn while
net income and both balance-sheet dates moved — one vintage, both cases.

**Also fixed here:** `features/vintage._replace_dates` rebuilt `PitFact` field by
field, so a new field silently vanished from any fact passing through it rather
than raising. It now uses `dataclasses.replace`.

## D15 — Publication order is defined once, and every view inherits it

`v_fact_revisions` (migration 001) predates `v_facts_pit` (004) and predates the
filed-vs-disseminated distinction (002). It built its own window straight off
`facts` with `ORDER BY filed_at, accn`, so the schema held two independent
answers to "which report came first" — and therefore to "which value did this
revision revise". The two disagree on exactly one input: a filing that became
public later than it was filed.

Nothing in production read it. `PitView.revisions()` had reimplemented the same
LAG inline over `v_facts_pit`, correctly, and the stale view was held alive by a
single test. That is the worst version of this defect, not the mildest: the
correct definition was maintained a few files away while the wrong one sat in
the schema looking authoritative, waiting for the next reader.

**Migration 006 rebuilds the view on `v_facts_pit`** rather than re-fixing its
window, and `revisions()` now reads the view instead of duplicating it. Ordering,
dissemination lag, `report_seq`, `differs_from_first_report` and
`period_distinct_values` are all inherited. The divergence becomes structurally
impossible rather than currently-absent — the same move D14 made one layer up,
applied to the layer that defines the order D14 depends on.

**Measured, both sides.** Of the 3,168 filings captured from the dissemination
feed, 122 became public later than they were filed — one draft registration
statement by 331 days. None of them carry XBRL facts yet, so the two orderings
agree on every row here today: both produce 394,320 revisions across 13.4m facts,
and `EXCEPT` in both directions returns zero. This refactor changes no number on
this warehouse. It changes what happens to the first late-disseminated filing
that revises a period, which forward capture makes a matter of time.

**The fixture is constructed, and says so.** No reordered chain exists in the
record yet, so the test builds the case that has not happened: a filing filed in
January and disseminated in June, beaten into the public record by a March
filing. Under the old ordering every assertion in it inverts. The filer is a
synthetic CIK — inventing a dissemination date for a real accession number would
read as a claim about a filing that was never late.

**Two `NULL` conventions, deliberately opposite.** Migration 005 uses
`IS DISTINCT FROM` because there a null is a missing *value* and `<>` would yield
a null boolean read as "not restated". `revisions()` keeps `<>` because there
`prior_value IS NULL` is the *first-report sentinel*: `IS DISTINCT FROM` would
call every first report a revision if the `prior_value IS NOT NULL` guard above
it ever moved, while `<>` yields null and drops the row. Same operator choice,
reversed, because the null means the opposite thing.

**Also fixed here:** `test_shipped_migrations_are_byte_frozen` pinned migrations
1–4 and stayed green when 005 shipped unpinned — it only guarded what somebody
remembered to list. It now asserts the pinned set *equals* the set on disk, so an
unpinned migration fails the suite instead of passing it.

## D16 — Purging is symmetric, because overlap is

Found by adversarial review, not by the test suite, which is the part worth
recording.

`trialkeeper.cv` purged training observations *backwards* from each test index
and left the forward direction to `embargo`. Overlap between label windows is a
symmetric relation — with a label window of `[i, i + h)`, observations `i` and
`p` overlap whenever `abs(i - p) < h`, and which one came first is irrelevant —
so half the relation was unenforced. `embargo` defaults to `0` and was never
tied to `label_horizon`, so the default configuration leaked.

The module docstring promised the symmetric guarantee in the AFML wording
("whose label window overlaps *any* test observation's label window"). The code
delivered half of it. That gap is the defect: `trialkeeper` ships as a standalone
MIT library whose entire value is that an outside user can trust it about leakage.

**Measured.** `purged_kfold(100, n_splits=5, label_horizon=5, embargo=0)`, first
fold, test block `0..19`:

```
purged=0  embargoed=0
train 20 window (20, 25) overlaps test [16, 17, 18, 19]
train 21 window (21, 26) overlaps test [17, 18, 19]
train 22 window (22, 27) overlaps test [18, 19]
train 23 window (23, 28) overlaps test [19]
```

The first fold is the sharpest illustration: its test block starts at index 0, so
there is nothing behind it to purge, the backward pass reported `purged=0`, and
four training rows leaked forward anyway. After the fix the same call reports
`purged=5` on the first fold, `10` on the interior folds (both sides), `5` on the
last — a signature that is wrong in an obvious way if either direction breaks.

**Why the tests missed it.** Every existing assertion probed the backward
boundary only (`first_test - 5 <= index < first_test`). A test that checks one
side of a symmetric relation passes on an implementation that does one side. The
replacement recomputes the overlap relation from its definition across every
fold rather than probing a boundary, so it cannot pass on a one-directional
implementation. Added as mutant #10.

**The purge window is `[p - h, p + h]`**, one wider each side than the strict
`abs(i - p) < h` rule. That conservatism is inherited from the original backward
pass rather than newly introduced, and it is the right reading when the label is
a return measured from the price at `i` to the price at `i + h`: the label
touches both endpoints.

**The margin is now pinned by a test.** Re-verification of the fix found the one
gap the new tests left: a variant narrowing *only* the forward side to
`[p - h, p + h - 1]` still satisfies the overlap invariant, so every test above
passed on it. The window recorded here as deliberate was therefore enforced by
nothing — a documented choice that nothing enforces is a comment, not a choice.
`test_the_conservative_purge_margin_is_pinned` asserts the exact per-fold counts
`[5, 10, 10, 10, 5]` for `purged_kfold(100, n_splits=5, label_horizon=5)`; the
strictly minimal window gives `[4, 8, 8, 8, 4]`. Verified two-sided: narrowing
both sides fails three tests, narrowing the forward side alone fails only this
one. The cost of the conservatism is 1 row per edge fold and 2 per interior fold,
independent of horizon and sample size. Narrowing it later is a legitimate
choice — it just has to come with an edit to this decision.

**Counting changed with it.** `purged` is now every row the purge removed, and
`embargoed` counts only rows dropped by the embargo *alone*. An embargo shorter
than the label horizon therefore reports `0` — honest, because it removed nothing
the purge had not already taken. Previously the overlap was credited to the
embargo, which would have made a symmetric purge look like it did less work.

**Blast radius today: zero.** Nothing in `packages/engine` imports
`purged_kfold` or `combinatorial_purged_splits`; the flagship study (P8) is
blocked on price entitlement and has not run. This was a latent defect in a
published library, not a wrong number in a shipped result — but P8 is exactly
what would have consumed it.

**Related, same review:** the README billed the multiple-testing module as the
"Harvey–Liu haircut". It implements Bonferroni/Holm/BHY against a trial count the
caller supplies, which is *motivated* by Harvey, Liu & Zhu (2016) but is not their
bootstrap over the factor zoo's unpublished tests. Renamed to "BHY multiple-testing
haircut" and the distinction is now stated in the module docstring. The weaker
claim is the true one, and for this library the true one is also the more useful:
it is only as honest as the trial count in your own pre-registration ledger.

---

## D17 — The mutation gate never writes to the working tree

**Taken:** 2026-07-27. **Reversal cost:** none — it is one script, and the
in-place version is in the history.

`scripts/mutation_gate.py` copies the source and test trees into a
temporary directory, mutates the copy, and redirects imports there with
`PYTHONPATH`. The real files are read once at the start and never written.

**What was wrong with mutating in place.** The previous design rewrote tracked
source files and restored them in a `finally`. It was careful about it — the
originals were copied to a backup directory whose path was printed before the
first write, and every file was asserted byte-identical afterward — and that
handled the failure it was designed for: the run dying and leaving mutants on
disk. It did nothing about the other one. For the duration of every pytest
invocation the source of truth on disk was deliberately wrong, and this gate runs
inside `make verify`, which is precisely when something else is most likely to be
reading those files: an editor, a language server, a linter, a reviewer, a
parallel session.

That is not hypothetical. A background security scan read
`005_fact_value_chain.sql` during a gate run and reported a defect whose
suggested fix was byte-identical to the code on disk — it had seen the mutant.
The finding was noise and cost only the time to disprove it. The window that
produced it was real, and the next reader in that window might have been a human
acting on what they saw, or a tool writing back.

**Why the sandbox is safe from the same class of problem.** The mutated bytes now
exist only under `/tmp`, so no concurrent reader of the repository can observe
them. The invariant is checked rather than asserted: sha256 of every target is
taken before the run and compared after, and sampling the tree continuously
*during* a run — 267 samples across a full pass — showed no content drift and no
`git status` output at any point. The sampler was then shown to detect a real
one-line edit, so the clean result is a measurement and not a blind spot.

**The failure mode this introduces, and its control.** `PYTHONPATH` redirection
can fail silently. The editable installs in `.venv` are plain `.pth` path entries
rather than a meta-path finder, so an earlier `sys.path` entry shadows them —
true today, an implementation detail of the installer, and not something to rely
on unverified. If it stopped being true, pytest would import the real unmutated
code, every mutant would survive, and the gate would print an alarming and
completely wrong report: that the test suite catches nothing.

So `_unredirected_imports` runs before the first mutant and requires
`aletheia.__file__` and `trialkeeper.__file__` to resolve inside the sandbox.

**The check runs under pytest, not beside it.** The first version was a
`python -c` probe, which was easier and measured the wrong thing: pytest inserts
its own `pythonpath` ini entries at the front of `sys.path`, ahead of anything
in `PYTHONPATH`, so a bare interpreter can resolve an import differently from
the process that runs the mutants. It happens not to today — `pythonpath` is
`["packages/engine", "packages/trialkeeper"]` and the packages live one level
further down under `src/`, so those entries match nothing — but a control whose
correctness depends on a layout detail elsewhere in the config is not a control.
The probe is now a generated test file placed inside the copied tree, run
through `_pytest_argv`, the single command line the real mutants use; it records
where each package resolved and the harness reads that back. Same interpreter,
same argv, same `conftest.py` chain, same working directory.

Verified in both directions, under the venv interpreter the gate actually runs
on (an early attempt at this control used the system `python3`, which cannot
import either package at all — the run failed for a reason unrelated to what was
being tested, and a failure in the expected direction for the wrong reason is
not evidence):

| Run | Result |
|---|---|
| Redirection broken, check in place | exit 1, `FAIL imports do not resolve to the sandbox`, naming both real paths |
| Redirection broken, check removed | exit 1 with all 10 mutants reported SURVIVED — the wrong diagnosis the check exists to prevent |
| Unmodified | all 10 caught, exit 0 |

**One consequence worth naming.** Because the tests are collected from `/tmp`,
pytest would otherwise look for its configuration beside them, find none, and
drop `--strict-markers`, `filterwarnings = ["error"]` and the marker
declarations without saying so — the tests would still run, under quietly
different rules. `-c` and `--rootdir` are pinned to the real repository to keep
the sandbox run governed by the same configuration as `make test`.

**What went away.** The backup directory, the byte-identical restore assertion
over the real tree, and the dirty-tree note are all gone, because nothing writes
to the real tree for them to protect. The before/after digest comparison is kept
even though it is now trivially true: it is cheap, and it is the only thing that
would notice if a future edit reintroduced an in-place write.

**The redesign shipped with the same defect it replaced, on a different path.**
`d7738f8` had established the principle that the scratch directory is named
before the first byte is written to it, so a run that dies leaves something
findable. The first sandbox version then put `mkdtemp` *and* the four `copytree`
calls before both the `try/finally` and the `sandbox ->` line. A copy that failed
partway — full disk, permission error, a concurrent writer — orphaned a
half-populated tempdir whose path had never been printed. The directory is now
created and printed first, and everything after it runs inside the `try`.
Verified against the failure itself rather than by reading the code, with the
copy made to raise on the third of four trees:

| Ordering | Result of the same simulated mid-copy failure |
|---|---|
| Fixed (create, print, then `try`) | exit 1, path printed, exception surfaced, **0** tempdirs left |
| As shipped in `77d0c03` | exit 1, no path printed, **1** orphan left holding `packages/engine` |

Finding the second row also turned up a real orphan from the *previous*
mechanism still sitting in `/tmp` — six flattened backup files, confirmed
byte-identical to their tracked originals, and removed. The litter this class of
bug produces is not hypothetical here.

**The wrong interpreter now fails on the first line, not the tenth mutant.**
Every subprocess uses `sys.executable`, so running the script under a system
`python3` rather than `uv run python` means no editable install, no importable
packages, and ten mutants all failing on `ModuleNotFoundError` before testing
anything. That is loud rather than silent, which makes it much less dangerous
than the shadowing case — but it is a confusing traceback from inside a pytest
subprocess when it could be one sentence. `_wrong_interpreter` checks
`importlib.util.find_spec` in-process before a tempdir exists, and names the
command to use instead. Under `/opt/homebrew/bin/python3`: exit 1, the message,
and zero tempdirs created. Under `uv run python`: silent, and the gate proceeds.

**What this harness structurally cannot test.** The sandbox is a plain directory
copy, so it is not a git repository, so `code_version()` correctly returns
`"unknown"` inside it. Provenance stamping therefore cannot be mutation-tested
here, and the test that covered it — `assert row[3]`, "a code version was
stamped" — passed on the string `"unknown"` just as happily as on a real sha,
which meant the stamp could have degraded everywhere with nothing noticing.
`test_the_stamp_is_the_real_commit_when_the_source_is_in_a_repository` now
compares the stamp against a sha obtained by shelling out to git directly
(not against `code_version`, which would be checking a function against itself)
and skips, with the reason stated, when there is no repository. Two-sided:
with the stamp forced to `"unknown"` at runtime the old assertion still passes
and the new one fails.

## D18 — A skip is a test result, and it has to be earned

**Taken:** 2026-07-27. **Reversal cost:** none.

The review of D17 found that the new provenance test derived the repository root
with `Path(version.__file__).resolve().parents[4]` — the same expression
`code_version` uses. Sharing it looked harmless, and it was not: a regression in
that arithmetic would break `code_version` *and* make the test that checks it
skip. The one thing that would catch the defect would quietly decline to run.

The arithmetic was also wrong. `parents[4]` is `<root>/packages`, not `<root>` —
`git rev-parse --show-toplevel` says so. It worked anyway, because `git -C`
searches upward from wherever it is pointed, so any directory inside the working
tree answers correctly. That is the worst kind of correct: harmless one level
down, and fatal one level up, where the path leaves the repository entirely and
every stamp silently becomes `"unknown"`.

Both are fixed. `source_tree_root()` is `parents[5]`, pinned against git's own
answer by `test_the_derived_root_is_the_actual_repository_root`. The skip
decision moved to `_discoverable_repository()`, which walks upward looking for
`.git` and shares no logic with what it checks — so a repository that exists but
cannot be read is now a failure, and only a genuine absence is a skip.

Measured by mutating the depth in `version.py` and restoring it:

| Depth | Root pin | Stamp test |
|---|---|---|
| `parents[5]` (correct) | PASSED | PASSED |
| `parents[4]` (what shipped) | **FAILED** | PASSED — git's upward search still resolves it |
| `parents[6]` (one too far) | **FAILED** | **FAILED** — and this is the case that used to skip |

The bottom-right cell is the whole point. Under the previous test that run was a
silent skip, reported as success by every summary line in the build.

**And these two tests skip inside the mutation gate.** The sandbox is a copy
under `/tmp` with no `.git` anywhere above it, so `_discoverable_repository()`
correctly returns `None` and both tests decline to run there — measured, not
assumed:

```
sandbox        /var/folders/.../T/aletheia-skipcheck-yftq5rst
.git above it? False

in sandbox   test_the_stamp_is_the_real_commit_when_the_source_is_in_a_repository
             SKIPPED [1] ...: source tree is not in a git repository, so there is no sha to expect
in sandbox   test_the_derived_root_is_the_actual_repository_root
             SKIPPED [1] ...: source tree is not in a git repository, so there is no root to compare

--- same two nodes against the real tree, for contrast ---
real tree    test_the_stamp_is_the_real_commit_when_the_source_is_in_a_repository   .
real tree    test_the_derived_root_is_the_actual_repository_root                    .
```

So the harness that proves tests-would-fail structurally cannot cover the two
tests written to close an unearned skip. The fix is still proven — the depth
mutation above ran against the real working tree, where they do run — but the
proof comes from a one-off control rather than from the standing gate, and
nobody should read `all 10 mutants caught` as covering these. Naming that here
is the same discipline the decision is about: a gate's blind spot reported as
coverage is an unearned skip one level up.

## D19 — Positive controls run against a copy of the tree, never the live checkout

**Taken:** 2026-07-28. **Reversal cost:** none.

Most of the evidence in D14–D18 comes from the same move: break something on
purpose, observe the test fail, restore, observe it pass. Every one of those
controls was run by editing a tracked file in the working tree and writing it
back — including the depth mutation in D18, and including the reviewer's
independent reproduction of it.

That is a real hazard, and it nearly bit. Two sessions ran mutations against
this one checkout within minutes of each other. Had a restore landed while the
other's `make verify` was mid-run, the result would have been a confident wrong
answer in *either* direction — a green run that silently covered a live mutant,
or a red run someone would have chased as a regression that did not exist — and
neither party could have told which from the output. Nothing was corrupted, but
only because the windows did not overlap. That is luck, not isolation.

The failure mode deserves naming because it is this project's own thesis
pointed inward. ALETHEIA exists to make a number's provenance checkable; a
control run on a shared mutable checkout produces a measurement whose
provenance is exactly what cannot be checked afterward. `git status` came back
clean, so the file was restored — but a clean tree at the end says nothing
about what the tree was *during* the run.

The convention, from here:

- A control that writes to tracked files works on a copy — `git worktree add`,
  or the same `shutil.copytree`-into-a-tempdir the mutation gate already does
  for exactly this reason. `scripts/mutation_gate.py` is the model, not the
  exception.
- The one-off controls in `scratchpad/` predate this and mutate in place. They
  are kept as the record of what was measured, not as a pattern to copy.
- Two agents never hold this checkout at once. Where that cannot be arranged,
  the second one takes a worktree.

Not mechanically enforced here: this repo has no hook that can see another
session's writes. It is a convention, and it is written down because the last
time it held, it held by accident.

## D20 — The flagship study drops prices and measures restatement contamination instead

**Taken:** 2026-07-28. **Reversal cost:** none — D6 anticipated this swap ("reversal
cost: none — swap before P8 begins"), and P8 has not begun. **Status when written:
pre-registration. No aggregate had been run and no result was known.**

D6 named restatement magnitude *as a return predictor* as the flagship. That
needs a price panel. The panel is 8 symbols, and it is 8 by entitlement, not by
effort: a live probe of six universe tickers on 2026-07-27 returned HTTP 402 on
all six —

    probing: ['A', 'AA', 'AAAU', 'AAC', 'AAC-UN', 'AACB']
    402  Premium Query Parameter: 'Special Endpoint : This value set for
         'symbol' is not available under your current subscription'

against 10,430 tickers in the universe. That is a whitelist wall, not a quota
that patience clears. An eight-name cross-section cannot support a
cross-sectional anomaly claim, and no survivorship disclosure repairs n=8. A
study run anyway would be a study whose headline is its own caveat.

So the flagship changes rather than waits. The new question needs no prices and
is the one the corpus is uniquely built to answer:

> **How much of a modern fundamentals panel did not exist at the time?**

This is a better flagship than the one it replaces, not a consolation. It is
measurable today at no spend, it is a population statistic rather than a
backtest (so it cannot be p-hacked by construction), and it states the
platform's premise as a number instead of an anecdote. Today that premise rests
on one company: AAPL FY2008 diluted EPS, 5.36 → 6.78.

### Registered before running anything

**Grain.** A *fact* is `(cik, taxonomy, concept, unit, period_start,
period_end)`. The primary statistic is computed at that grain, because
`differs_from_first_report` is a per-row flag and a fact restated five times
would otherwise count five times. Row-grain is reported too, as a secondary —
the gap between the two is itself informative and hiding it would be a choice.

**Primary statistic.** Share of facts whose value changed after first
publication (≥2 distinct values across the report sequence).

**Falsifiable form, with the kill threshold set now.** If the primary statistic
comes in **below 1%**, restatement contamination is a rounding error, the
platform's motivating premise is overstated, and the memo says exactly that.
The AAPL example would then be an outlier presented as an illustration — which
is the error this whole project exists to make hard. Registered in advance so
that it cannot be renegotiated afterward.

**Exclusions, declared now, each reported with its count.** Unit changes,
taxonomy migrations, and sign-convention flips are not economic restatements. A
number is not "restated" because it moved from one taxonomy to another. What is
excluded gets counted and shown; an unfiltered number is easy to attack and a
filtered one with the filter visible is not.

**No single materiality threshold is the headline.** The distribution of
relative change is reported, plus the share above 1% / 5% / 10%. Picking one
cutoff after seeing the distribution is the degree of freedom this repo is
about; it is closed here.

**Positive control, and it gates the number.** The general aggregate is run
against AAPL `EarningsPerShareDiluted` and must return **25 restated of 121
periods** — the figure measured independently on 2026-07-27 and quoted in the
plan and the README. It is a known answer the query is fully capable of
getting wrong. If it does not reproduce, the population number is wrong, not
the AAPL one, and nothing ships until it does.

**Selection is a convenience sample and the memo will say so.** The 800 filers
were ingested across 37 ad-hoc batches during development, drawn in 2026 from a
*current* ticker map — so they are alive-today by construction. The corpus is
survivorship-free where EDGAR is (the SEC never deletes a dead filer), but the
*selection* is not, and dead companies plausibly restate more than survivors,
which would bias the headline **down**. The warehouse cannot currently size this:
`delistings` holds 100 rows spanning 2026-07-01 to 2026-07-23, three weeks of one
vendor page, and only 2 of the 800 appear in it. So the direction of the bias is
arguable and its magnitude is not measured — stated, not estimated.

**Scope.** No new ingest. The 8-symbol price panel is not touched. The study is
fundamentals-only by design, and the memo says that rather than letting it read
as a limitation worked around.

The price-vendor spend stays where D1 and the plan put it: reserved for Jurgis.
This decision does not route around that question, it removes the flagship's
dependency on it.

### D20 — correction, 2026-07-28 (appended, original text left intact above)

Two statements in D20's prose were wrong. They are corrected here rather than
edited in place: a pre-registration whose text can be revised after the result is
known is not a pre-registration, so the record keeps what it actually said and
the correction is dated and appended.

**1. The universe was described as a current-ticker-map convenience sample.** D20
says the 800 filers were "ingested across 37 ad-hoc batches during development,
drawn in 2026 from a *current* ticker map", and reasons from that to the headline
being biased **down**. That is false in both halves. `scripts/select_universe.py`
(committed at `fd14a47`, before D20) draws the 800 from the SEC's
`Assets/USD/CY2011Q4I` frame — a 2011 point-in-time cross-section, $500M asset
floor, 2,998 eligible of 8,166 in the frame, fixed seed. Membership is decided by
2011 filings alone, so filers that later went dark are in the sample by
construction: **392 of the 800 published no fact after 2024-01-01**, measured on
the same basis the shipped code uses (each filer's last fact in this corpus).

The false sentence appears to have been carried over from `run_bias_study.py`,
where "the ticker-to-CIK map is a current SEC snapshot" is a genuine caveat about
resolving *price* symbols in S001. It does not apply to company selection here.

Consequence: the survivorship bias D20 said could not be sized **can** be sized,
and now is. `contamination_by_survival` reports restatement rates for dormant
against still-active filers at five cutoffs. The gap runs +0.42pp to +0.88pp,
same sign throughout — dormant filers restate somewhat more, and this universe
already contains them. Reporting all five cutoffs is deliberate: the cutoff is a
free parameter, and quoting one value would be a choice made after seeing the
others.

**2. The control's provenance sentence overstates.** D20 says the AAPL 121/25
figure was "quoted in the plan and the README". It is in the plan; it is not in
the README, and no plan file is committed to this repo. The claim that matters —
that the figure was registered before the code that computes it existed — is
independently verifiable and holds: `ae63862` (D20) precedes `499f38b`, the first
commit of `contamination.py`. The same overstatement is copied into the module
docstring of `tests/unit/test_contamination.py` and is corrected there.

Also minor: "37 ad-hoc batches" does not reconcile with `ingest_universe.py
--batch 25` over 800 names (≈32). The batch count was never load-bearing.

**How both were found.** A fresh-context adversarial review of the finished
study, run before the memo shipped. Neither was reachable by reading the code:
the code was right, the prose describing it was wrong. That is the failure mode
this project exists to catch, and it caught it in its own flagship.

### D20 — second correction, 2026-07-28 (appended; both texts above left intact)

**The correction above needs a correction, and it is the interesting one.** Point
1 concluded that "the gap runs +0.42pp to +0.88pp, same sign throughout — dormant
filers restate somewhat more". **That is withdrawn.** It is Simpson's paradox: a
filer that goes dark stops producing new accounting periods, so its facts
concentrate in older ones, and older periods have had more calendar time in which
to be revised. Hold the period fixed and the sign reverses in every band
(−0.08pp, −0.99pp, −2.17pp, −2.36pp). `survival_by_period_band` computes it.

Two further things, both of which change what should be concluded rather than
just the number:

**a. The gap is not the bias.** The reasoning above treated the cohort gap as the
error an active-only universe would carry. It is not. That bias is the gap
weighted by the dormant cohort's share of the corpus: **+0.05pp to +0.17pp**,
three to ten times smaller. `SurvivalSplit.active_only_bias` computes it and a
mutant enforces the distinction. The sizeable claim in point 1 — that the bias
"can be sized, and now is" — stands; the figure it quoted did not.

**b. The banded table is not the fix either.** Stratifying by period removes the
vintage confound and maximises a second one running the other way: a dormant
filer stopped filing at all, so inside a band its facts were mostly published
once, and a fact published once cannot be restated. Republication rates are
1.47% dormant against 44.77% active in the 2023-onward band; conditioning on a
second report moves the sign a third time. Cohort is entangled with both period
vintage and republication opportunity, and none of the three views computed here
-- pooled, period-banded, conditioned on a second report -- breaks both at once. A
matched or regression-adjusted design was not attempted and is not ruled out; the
claim is about these numbers, not about what is measurable in principle.

**Conclusion: no dormancy effect is asserted in either direction.** What survives
is the counterfactual, which needs no causal attribution — an active-only
universe would have measured 0.05–0.17pp less contamination than this one. The
headline (5.02%) is untouched throughout; every correction here is to the
survivorship caveat, which was always a caveat and never the result.

**c. The fix for (b) put its guard in the wrong layer.** The thin-cell
suppression was written into the console formatter only, so the evidence card —
the artifact meant to be read without surrounding prose — kept the unreportable
rate. And the same round persisted a **pooled** conditional gap of +2.47pp to
+3.76pp, positive at all five cutoffs and four to eight times the figure
retracted in this very appendix, discussed nowhere. The prose disclaimed it; the
shipped JSON did not, which is the worse way round, because the JSON is what the
next run reads. The guard now lives on `SurvivalSplit`, suppressed cells
serialise as `null` with a stated reason, reportable ones carry their disclaimer
in the card, and the pooled panel is printed and argued with rather than hidden.

**How it was found.** (a) and the paradox came from a second fresh-context
review, run after the first round's fix had been committed and believed final.
(b) came from reading the study's own printed output afterwards: a cell reporting
0.02% on 4,573 facts is one restated fact, which is censoring rather than
measurement. (c) came from a fourth fresh-context review of the commit that fixed
(b). Each defect was introduced by the fix to the one before it — the pattern
worth carrying forward is that corrections are written with more confidence and
less scrutiny than the code they replace, and that a claim can be correct in the
prose and wrong in the artifact.
