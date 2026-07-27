# Runbook

Operating instructions. Written for the person who has to run this at 7am when
something has gone wrong, so it says what to check and what the failure means, not
only what to type.

---

## Bootstrap

```bash
make setup                 # uv sync, creates .venv from uv.lock
make verify                # ruff + mypy strict + full suite + determinism gate
```

`make verify` must pass on a clean checkout before anything else is trusted. It
takes a couple of minutes and includes the determinism gate's positive control,
so a pass means the gate itself was proven able to fail.

Credentials live in `~/.claude/.env` and are read through `aletheia.core.config`.
Nothing is required for an EDGAR-only run — the SEC needs only a contact address
in the `User-Agent`, set via `SEC_USER_AGENT`. `FRED_API_KEY` and `FMP_API_KEY`
unlock macro vintages and prices respectively; both sources return `None` from the
composition root when unconfigured rather than failing at startup, because an
EDGAR-only run is legitimate.

---

## Building a warehouse from nothing

Order matters: the universe and company metadata must exist before prices, because
the price stage reads the symbols the fundamentals stage resolved.

```bash
uv run aletheia ingest universe                       # 10,432 ticker↔CIK rows
uv run python scripts/select_universe.py --sample 800 # the study universe (D8)
uv run python scripts/ingest_universe.py --batch 25   # ~80 min, resumable
uv run aletheia ingest macro --series GDPC1 DGS10 ...  # ALFRED vintages
uv run python scripts/run_bias_study.py --stage symbols
uv run python scripts/run_bias_study.py --stage prices
```

**The company stage is the long pole** — roughly six seconds per filer, dominated
by parsing 20–30k XBRL facts each, not by the network. It is safe to interrupt.
Re-running skips any company that already has facts in the warehouse.

> Resume is keyed on **facts**, not on the `entities` row. `ingest_company` writes
> the entity first, so a company that died mid-write would otherwise be skipped
> forever and be silently missing from the universe.

### If the company stage dies

Look at the traceback rather than restarting blindly. One failure mode has
already been seen in production: a filer reported `EntityPublicFloat` as
2.56 × 10¹⁸ USD, which needed 29 significant digits and overflowed Decimal's
default 28-digit context. That is fixed, but the shape recurs — one filer's bad
tag can stop a run.

A per-company failure that is *caught* (anything deriving from `AletheiaError` or
`OSError`) is recorded in the outcome's `failed` list and the batch continues.
Anything else propagates and stops the run, which is deliberate: an unrecognised
error is not something to swallow across 800 companies.

---

## Health checks

```bash
uv run aletheia status                      # row counts and configuration
uv run python scripts/check_determinism.py --self-test
```

Then the acceptance check. **These two commands returning different numbers is the
single sharpest evidence the system works:**

```bash
uv run aletheia asof AAPL --concept EarningsPerShareDiluted \
    --period-end 2008-09-27 --date 2009-12-01 --compare-restated   # expect 5.36
uv run aletheia asof AAPL --concept EarningsPerShareDiluted \
    --period-end 2008-09-27 --date 2010-06-01 --compare-restated   # expect 6.78
```

If both return 6.78, the point-in-time filter is not being applied and **nothing
downstream should be trusted** until it is fixed.

### Filings look empty

If `PitView.filings()` returns nothing for a company that plainly has filings,
check the filer relation:

```sql
SELECT count(*) FROM filings;
SELECT count(*) FROM filing_filers;
```

`v_company_filings_pit` inner-joins `filing_filers`. A filing with no row there is
present in the table and invisible to every query that asks what a company
published. Repair:

```python
warehouse.backfill_filing_filers()  # idempotent; returns rows inserted
```

A return of `0` on a warehouse you suspect means the problem is elsewhere.

---

## Running the app

Two processes, two shells:

```bash
make api    # uvicorn on 127.0.0.1:8000
make web    # next dev on localhost:3000
```

If either port is taken, override and point the frontend at the API:

```bash
uv run uvicorn aletheia.api.app:app --host 127.0.0.1 --port 8040
cd apps/web && ALETHEIA_API=http://127.0.0.1:8040 npx next dev -p 3040
```

The API opens the warehouse **read-only**. DuckDB permits a single writer, so the
API will refuse to start while an ingest is running — that is the lock working,
not a bug. Stop the ingest or wait for it.

CORS allows `localhost:3000` only. Widening it is a deployment decision and should
be made deliberately.

---

## Running the flagship study

```bash
make study     # symbols → prices → study, writing an evidence card
```

Or one stage at a time, which is what you want when iterating:

```bash
uv run python scripts/run_bias_study.py --stage study --family accrual-vintage-bias-debug
```

**Use a separate `--family` for debug runs.** Every run registers a trial in the
hash-chained ledger, and the trial count for a family sets the deflated-Sharpe bar
for every result in it. A dozen exploratory runs under the real family name
silently raise the bar the real result has to clear — which is the correction
working as designed, and not what you meant.

The run prints a warning if the `restated_values` and `naive_vendor` arms produce
identical return series. That means period selection has collapsed onto a single
fiscal year and the timing channel is measuring nothing; the study output is not
usable until it is fixed.

---

## Things that are supposed to fail

Knowing which failures are correct saves an hour of debugging the wrong thing.

| Symptom | Meaning |
|---|---|
| `LookaheadViolation` | A row escaped the knowledge-date filter. A genuine bug — the canary caught what the filter missed. Do not suppress it. |
| `LeakDetected` | The *simulation* was handed data it could not have had. Usually a panel assembled with a wrong date filter. |
| `InsufficientData` | Nothing was published in time. Correct behaviour; a `None` here would become a NaN and then a Sharpe ratio. |
| `IntegrityViolation: too large for DECIMAL` | A value genuinely cannot be stored. Check whether the filer mis-tagged it. |
| `DelistedCoverageError` | The price vendor will not serve a delisted name. Counted as `unreachable` — this *is* the survivorship measurement. |
| `StoreError: Could not set lock` | Another process holds the warehouse. DuckDB allows one writer. |
| Migration checksum mismatch | A shipped migration file changed after being applied. Migrations are append-only; add a new one. |

---

## Data vintage, and why a re-run is a new study

The vintage is `max(filed_at)` across all filings. It appears on every evidence
card and on the data-quality page.

Re-running a study after the vintage moves is **not** a reproduction. Restatements
filed in between did not exist when the first run happened, so the `restated` and
`naive` arms legitimately see different numbers. The reproducibility hash will
differ, and that is correct.

To genuinely reproduce a result, you need the same commit *and* a warehouse at the
same vintage. The raw payload store is content-addressed, so the inputs to any
past run are still on disk under their sha256 even after the warehouse moves on.

---

## The paper book

```python
book.record(as_of=..., holdings=[...], cash=Decimal("..."), recorded_at=...)
book.verify()  # recomputes the chain; reports the first broken mark
book.head()  # the value worth committing publicly
```

The chain proves the file has not been rewritten since it was written. It becomes
*evidence* only when the head is anchored somewhere with an independent clock — a
dated commit. Commit the book file regularly; the git timestamp is the anchor.

Never edit a past mark. A correction is a new mark carrying a note. Editing breaks
the chain, which is the mechanism working, and the break is permanent.

---

## Scheduled operation

Not currently scheduled, and deliberately so. Before anything here runs
unattended it needs a stated trigger, allowed actions, verification evidence,
escalation path and stop condition. An ingest that fails quietly at 3am and leaves
a half-written universe is worse than one that never ran, because the next study
will run on it and report a clean result.
