"""Build a small warehouse from nothing and land on the proof.

The full universe is 800 filers, 13.4M facts and about eighty minutes. That is the
right size for research and the wrong size for someone deciding in five minutes
whether any of this works. This builds a ~25-filer warehouse in roughly three
minutes and finishes by printing the one result that matters: the same query, at
two dates, returning two different numbers.

Everything here is real. There is no fixture, no seeded database and no recorded
response — it fetches from EDGAR live, and if the SEC is down it says so rather
than falling back to something that looks like data.

Idempotent: re-running skips filers that already have facts, so an interrupted
run continues rather than starting over.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

from aletheia.app import Application
from aletheia.core.formatting import plain
from aletheia.core.types import Cik
from aletheia.pit import as_of

# Large, long-listed, non-financial filers with XBRL back to the 2009-2011 mandate.
# Apple is not optional -- it carries the restatement the whole demonstration rests
# on. The rest are chosen to be recognisable, so a reader can sanity-check a number
# against their own knowledge rather than taking it on trust.
DEMO_TICKERS = (
    "AAPL",
    "MSFT",
    "JNJ",
    "XOM",
    "WMT",
    "KO",
    "PEP",
    "INTC",
    "CSCO",
    "ORCL",
    "IBM",
    "MRK",
    "PFE",
    "HD",
    "MCD",
    "BA",
    "CAT",
    "MMM",
    "NKE",
    "T",
    "VZ",
    "CVX",
    "UNH",
    "TGT",
    "LOW",
)

AAPL_CIK = Cik(320193)
FY2008_END = date(2008, 9, 27)
CONCEPT = "EarningsPerShareDiluted"
BEFORE = date(2009, 12, 1)
AFTER = date(2010, 6, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--skip-daily",
        action="store_true",
        help="Skip the dissemination pull that populates the filing feed page.",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    with Application.build(data_dir=args.data_dir) as app:
        if "set ALETHEIA_SEC_USER_AGENT" in app.settings.sec_user_agent:
            print("NOTE: ALETHEIA_SEC_USER_AGENT is not set.")
            print("      The SEC asks automated clients to identify themselves with a real")
            print("      contact address, and throttles those that do not. Set it to")
            print("      something like 'Your Name your@email' before a long run.\n")

        print("1/4  ticker to CIK map from EDGAR", flush=True)
        outcome = app.ingestor.ingest_universe()
        if outcome.failed:
            reason = outcome.failed[0]
            print(f"     FAILED: {reason[:160]}")
            if "rate limit" in reason.lower() or "threshold" in reason.lower():
                print("\n     EDGAR is throttling this address. It clears on its own, usually")
                print("     within about ten minutes. Re-run then -- the script is idempotent")
                print("     and will pick up where it stopped.")
            else:
                print("\n     EDGAR could not be reached. Nothing here reads from a fixture,")
                print("     so there is no offline path and nothing to fall back to.")
            return 1
        print(f"     {outcome.rows_written:,} identifier rows", flush=True)

        placeholders = ", ".join("?" for _ in DEMO_TICKERS)
        rows = app.warehouse.execute(
            # Placeholders are generated from a module-level tuple of literals;
            # every value is bound, none is interpolated.
            f"SELECT DISTINCT ticker, cik FROM entity_identifiers WHERE ticker IN ({placeholders})",  # noqa: S608
            list(DEMO_TICKERS),
        ).fetchall()
        resolved = {str(ticker): int(cik) for ticker, cik in rows}
        missing = [ticker for ticker in DEMO_TICKERS if ticker not in resolved]
        if missing:
            # Named, not silently dropped: a demo that quietly shrinks is how a
            # reader ends up trusting a smaller sample than they think they have.
            print(f"     not resolvable today: {', '.join(missing)}", flush=True)

        already = {
            int(row[0])
            for row in app.warehouse.execute("SELECT DISTINCT cik FROM facts").fetchall()
        }
        todo = [Cik(value) for value in sorted(resolved.values()) if value not in already]
        print(
            f"\n2/4  filings and XBRL facts for {len(todo)} filers "
            f"({len(resolved) - len(todo)} already present)",
            flush=True,
        )
        if todo:
            outcome = app.ingestor.ingest_companies(todo)
            print(f"     {outcome.rows_written:,} rows, {len(outcome.failed)} failed", flush=True)
            for failure in outcome.failed[:5]:
                print(f"       {failure}")

        if not args.skip_daily:
            day = _last_weekday()
            print(f"\n3/4  filings disseminated on {day} (populates the feed page)", flush=True)
            outcome = app.ingestor.ingest_daily_index(day)
            print(f"     {outcome.rows_written:,} filings", flush=True)
        else:
            print("\n3/4  skipped", flush=True)

        app.warehouse.backfill_filing_filers()

        print("\n4/4  the proof\n", flush=True)
        ok = _print_proof(app)

    minutes = (time.monotonic() - started) / 60
    print(f"\nbuilt in {minutes:.1f} min")
    if ok:
        print("\nNext:  make api     (terminal 1)")
        print("       make web     (terminal 2)  ->  http://localhost:3000")
    return 0 if ok else 1


def _print_proof(app: Application) -> bool:
    """Ask one question at two dates. The answers must differ."""
    try:
        before = as_of(app.warehouse, BEFORE).fact(AAPL_CIK, CONCEPT, period_end=FY2008_END)
        after = as_of(app.warehouse, AFTER).fact(AAPL_CIK, CONCEPT, period_end=FY2008_END)
    except Exception as exc:  # noqa: BLE001 - a demo must explain itself, not traceback
        print(f"     could not reach the FY2008 figure: {exc}")
        return False

    print("     AAPL diluted EPS for the year ending 2008-09-27\n")
    for label, fact in ((f"as known on {BEFORE}", before), (f"as known on {AFTER}", after)):
        marker = "  <- restated" if fact.report_seq > 1 else ""
        print(
            f"       {label}   {plain(fact.value):>6}   "
            f"filed {fact.knowledge_date}   {fact.accn.value}{marker}"
        )

    if before.value == after.value:
        print("\n     BOTH DATES RETURNED THE SAME VALUE.")
        print("     The point-in-time filter is not being applied. Do not trust anything")
        print("     downstream until this is fixed.")
        return False

    change = (after.value - before.value) / abs(before.value)
    print(
        f"\n     Two dates, two answers: {change:+.2%}. On 2009-12-01 nobody could have"
        f"\n     known {plain(after.value)} — it was published on {after.knowledge_date}. Every vendor"
        "\n     panel returns it for both dates anyway."
    )
    return True


def _last_weekday() -> date:
    """Most recent weekday. The SEC publishes no index on weekends."""
    day = date.today() - timedelta(days=1)  # noqa: DTZ011 - a calendar day is what is wanted
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


if __name__ == "__main__":
    raise SystemExit(main())
