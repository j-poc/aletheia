# ALETHEIA

**A point-in-time evidence engine for systematic equity research.**

---

## The problem, in one number

Apple's fiscal 2009 net income was **$5,704,000,000**.

It was also **$8,235,000,000**.

Both are correct. The first is what Apple filed on 2009-10-27 in accession
`0001193125-09-214859`. The second is what Apple filed on 2010-01-25 in
`0001193125-10-012091`, after adopting a revenue-recognition standard
retrospectively. Diluted EPS for fiscal 2008 moved the same way in the same
amendment, from **5.36** to **6.78**.

Download Apple's fundamentals from any commercial vendor today and you get 8,235.
You get it for every date, including every date in November and December 2009,
when the number did not exist and nobody on earth could have known it.

A backtest of late 2009 built on that panel is trading on information published in
2010. Nothing warns you. The Sharpe ratio comes out fine.

Verbatim, against the real warehouse:

```
$ aletheia asof AAPL --concept EarningsPerShareDiluted --period-end 2008-09-27 \
      --date 2009-12-01 --compare-restated
AAPL · EarningsPerShareDiluted · as known on 2009-12-01

 period ending               value   published  rpt  filing
-----------------------------------------------------------
    2008-09-27                5.36  2009-10-27    1  0001193125-09-214859

as it stands today (LOOKAHEAD — what a vendor panel would give you):
  6.78  published 2010-10-27
  difference vs. what was knowable on 2009-12-01: +26.49% — the error a conventional backtest would make

$ aletheia asof AAPL --concept EarningsPerShareDiluted --period-end 2008-09-27 \
      --date 2010-06-01 --compare-restated
AAPL · EarningsPerShareDiluted · as known on 2010-06-01

 period ending               value   published  rpt  filing
-----------------------------------------------------------
    2008-09-27                6.78  2010-01-25    2  0001193125-10-012091  ← restated
```

Same company, same fiscal period, same query. Two dates, two answers, because on
those two dates two different things were true. That is the entire product.

## It is not one company, and it is not rare

Across an 800-filer universe drawn from a 2011 SEC cross-section — **13,447,437
XBRL facts over 1,364,574 filings**:

| | |
|---|---|
| Distinct reported facts, keyed as the warehouse keys them | **7,133,070** |
| …carrying more than one distinct reported value | **357,842** |
| Share | **5.0%** |

One reported number in twenty changed after it was first published. A flat vendor
panel returns only the final value for every one of them, on every date.

Reproduce with `make stats`. The key is
`(cik, taxonomy, concept, unit, period_start, period_end)` — the same one
`v_facts_pit` and `v_fact_revisions` use.

> An earlier version of this README claimed **16.4%**. That query dropped
> `period_start`, which conflates a fiscal year with its fourth quarter — they
> share an end date and report different numbers — and counted 667,015 pairs of
> different periods as one period revised. It survived because no committed code
> produced it. `make stats` now prints both figures side by side, so the mistake
> stays visible rather than becoming folklore. The same omission was a real
> correctness defect in the query layer, not just a documentation error; see
> `AmbiguousPeriod` in `pit/view.py`.

Not all of those are formal restatements — many are prior-year comparatives
re-presented under a changed classification in the next annual report, which the
revision explorer separates by publication lag. That distinction was measured, not
assumed, and it is why the flagship study's hypothesis was swapped (D6 → D7).

---

## What this is

A bitemporal warehouse of SEC filings, XBRL fundamentals, macro vintages and
prices, plus the research layer that sits on top of it. Every stored fact carries
**two** dates: the period it describes, and the date it became knowable. Research
code cannot reach the second one's future.

The guarantee is enforced three independent ways, because one mechanism is a
promise and three is a design:

1. **Filtered at the source.** Every query bounds `knowledge_date <= as_of`.
2. **Checked on the way out.** Every returned row is re-inspected; a value that
   slipped past the filter raises `LookaheadViolation`. This catches the realistic
   failure — a hand-written predicate that is subtly wrong — which the filter alone
   cannot, because the filter is the thing that is wrong.
3. **Unreachable by accident.** `features/`, `research/` and `book/` may not import
   `aletheia.store`. A test walks their import graph and fails the build if they do.

Reading the future is still *possible* — sometimes it is the research question. It
is spelled `unsafe_latest_restated`, so every use of it shows up in a grep and none
of it can be typed by accident.

---

## Why not just use a terminal

A Bloomberg terminal answers *"did a filing drop."* It does that better than
anything here ever will, and real-time filing alerting is not what this is for.

What a terminal licence forbids is bulk systematic extraction into a research
substrate you own. So it cannot answer the question that actually separates a
signal from an expensive coincidence: *has this pattern ever mattered, on the data
that existed at the time, and how many things did I try before I found it?*

That substrate is the asset. This is a build of it.

---

## What it found

Two point-in-time defects surfaced during construction that offline tests could
never have produced, because both required contact with the real feed:

- **EDGAR's daily index is a dissemination feed, not a filing feed.** Filings appear
  on it dated earlier than the day they were disseminated. The knowledge date is
  `max(filed_at, disseminated_at)`, not `filed_at` — a live contract test caught the
  mismatch and it became migration `002`.
- **801 of 3,168 filings had multiple co-registrants.** A row-count reconciliation
  that should have matched did not; the cause was a filing counted once per filer.
  Migration `003`.

Three more surfaced only by running the finished application against the real
warehouse rather than against fixtures:

- **Every filing was invisible to research.** `v_company_filings_pit` inner-joins a
  filer relation that only the daily-index path populated, so all 1.36M filings
  pulled from the submissions endpoint were stored and never linked. The
  surveillance layer would have reported a quiet day, forever.
- **Concurrent requests returned each other's rows.** A DuckDB connection carries one
  result set; FastAPI runs sync endpoints in a threadpool. A browser opening two
  pages was enough for a row-count query to come back holding an accession number.
- **The provenance ledger held one row against 2,281 payload files.** Indexing was
  wired per call site and only one of six did it. Row-level provenance was never
  affected — every fact's content hash resolves to a real file — but the index that
  answers "what did this system fetch" was empty and nothing said so.

And one in the cost model, caught by a negative control rather than by review:
Corwin–Schultz produces negative per-pair spread estimates constantly, since it
infers a spread from a range that is mostly volatility. Discarding them truncates
only the low tail. Measured on a simulated path with **no spread imposed at all**,
the discard version reports **128 basis points out of pure noise**. Averaging the
raw estimates and flooring the mean reports approximately zero, which is the truth.

---

## Layout

```
packages/engine/
  core/          Decimal money, injected clock, canonical hashing, typed errors
  provenance/    content-addressed payload store, ingest ledger, run manifests
  sources/       EDGAR (facts, submissions, daily index, bulk) · ALFRED · prices
  store/         DuckDB, forward-only migrations, bitemporal tables, as-of views
  pit/           as_of(date) — the only door between stored data and research
  features/      theory-selected signals; data-vintage policies
  research/      backtest kernel · cost model · panel builder · evidence cards
  surveillance/  daily index poller, forensic scoring
  book/          live paper book, hash-chained daily marks
packages/trialkeeper/    standalone MIT library — deflated Sharpe, PBO, purged CV,
                         Harvey–Liu haircut, pre-registration ledger. Zero
                         aletheia imports; the boundary is enforced by its tests.
```

`trialkeeper` is carved out deliberately. `mlfinlab` went commercial and there is
no well-tested, permissively-licensed implementation of that stack. It is usable
without any of the rest of this.

---

## Discipline, and how it is enforced

| Claim | What makes it true |
|---|---|
| No lookahead | SQL bound + runtime canary + import boundary, each independently tested |
| Restatements preserved | Same period, different accession, different value — kept as separate rows, never deduplicated |
| Money is exact | `Decimal` throughout; floats only for returns and statistics. Scaling that would lose precision raises rather than truncates |
| Results reproduce | `make determinism` runs the pipeline under four `PYTHONHASHSEED` values and requires one hash — **and** runs a deliberately broken pipeline that must fail, so a green result is evidence rather than an assumption |
| Costs are never omitted | Every return is reported gross and net, with turnover and the capital assumed |
| Survivorship is measured | Names the price vendor will not serve are counted with reasons, not skipped |
| Trials are counted | Hypotheses are registered in an append-only hash chain *before* they run |

That determinism gate immediately earned itself: the backtest kernel sorted on
signal value alone, and Python's sort is stable, so tied values inherited whatever
order the caller built the panel in — nondeterministic if that panel came from a
set. Verified two-sided: tie-break removed → four distinct hashes; restored → one.

---

## Running it

```bash
make setup                # uv sync
make verify               # ruff + mypy strict + full suite + determinism gate
make demo                 # ~3 min: builds a 25-filer warehouse and prints the proof
make api                  # read-only HTTP API on :8000
make web                  # the five pages, on localhost:3000
```

**Set a contact address first.** The SEC asks automated clients to identify
themselves and throttles those that do not — a 403 with an HTML page, not a 429:

```bash
export ALETHEIA_SEC_USER_AGENT="Your Name your@email"
```

Nothing else is required. `FRED_API_KEY` and `FMP_API_KEY` unlock macro vintages
and prices; both sources return `None` when unconfigured rather than failing at
startup, because an EDGAR-only run is legitimate and is what `make demo` does.

For the full 800-filer universe and the study:

```bash
make ingest               # ~80 min, resumable
make study                # needs a price entitlement — see "the flagship study" below
```

`make test-live` runs the contract tests against the real APIs. They cost quota and
are excluded from the default suite deliberately — but they are the tests that found
both migrations above, so they are not optional before trusting a change to a source.

---

## What this is not

- **Not a trading system.** Nothing here places an order.
- **Not a data vendor replacement for prices.** Fundamentals and the filer universe
  are survivorship-free — the SEC never deletes a dead company's filings. *Prices*
  for delisted names are not obtainable on the current data plan, and every result
  carries a computed exposure figure saying so rather than quietly running on the
  survivors. The price source sits behind an adapter so a real vendor drops in
  without touching research code.
- **Not a claim that the signals here make money.** The evidence engine is the
  point. A signal that dies once its trial count is honest is published as dying.

### The flagship study has not run

It is built, tested and blocked on data, and that is stated rather than papered
over. The fundamentals landed — 800 filers, 13.4M facts — and symbol resolution
produced 226 tradable names. The price vendor's **daily quota** ran out after 8 of
them, and the two free alternatives are unusable (Yahoo's chart API now demands a
cookie-and-crumb handshake and rate-limits the crumb endpoint itself; Stooq puts a
JavaScript proof-of-work wall in front of its CSV).

Eight symbols cannot populate five quantiles. Running it anyway would produce
noise dressed as a finding, so it was not run. `docs/decisions.md` D9 records what
unblocks it. The failure did earn a fix: batch ingest now trips a circuit breaker
after five consecutive failures instead of spending twenty-two minutes retrying an
exhausted quota and exiting `0`.

---

## Decisions

Architectural decisions, including the ones that were reversed and why, are in
[`docs/decisions.md`](docs/decisions.md). The flagship study's hypothesis was
swapped before implementation once measurement showed the original was mostly
detecting the annual reporting cycle rather than restatements; that reversal is
written up as D6 → D7 rather than quietly deleted.
