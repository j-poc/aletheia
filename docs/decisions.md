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
Verified in both directions:

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
