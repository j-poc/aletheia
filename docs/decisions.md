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
