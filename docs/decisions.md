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
