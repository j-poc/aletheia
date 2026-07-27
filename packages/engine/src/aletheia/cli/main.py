"""Command line interface.

Every command prints what it did, including what it could not do. A run that
partially failed says so on stdout and exits non-zero, because an ingest whose
gaps are invisible is worse than one that fails outright.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from aletheia.app import Application
from aletheia.core.errors import AletheiaError, InsufficientData
from aletheia.core.formatting import abbreviate
from aletheia.core.types import Cik
from aletheia.pit import as_of

# A default macro set chosen for revision behaviour, not for breadth: GDP and
# payrolls are revised heavily (real vintages to study), CPI barely at all, and
# the daily market series are never revised (a control that proves the vintage
# machinery is not inventing revisions).
DEFAULT_MACRO_SERIES = ("GDPC1", "PAYEMS", "CPIAUCSL", "INDPRO", "UNRATE", "DGS10", "DGS2")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        result: int = args.handler(args)
        return result
    except AletheiaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aletheia",
        description="Point-in-time evidence engine for systematic research.",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="override the data directory")
    subparsers = parser.add_subparsers(dest="command")

    status = subparsers.add_parser("status", help="warehouse contents and configuration")
    status.set_defaults(handler=_cmd_status)

    ingest = subparsers.add_parser("ingest", help="pull data from a source")
    ingest_sub = ingest.add_subparsers(dest="what")

    universe = ingest_sub.add_parser("universe", help="ticker↔CIK observations from EDGAR")
    universe.set_defaults(handler=_cmd_ingest_universe)

    company = ingest_sub.add_parser("company", help="filings and XBRL facts for one or more CIKs")
    company.add_argument("ciks", nargs="+", type=int)
    company.add_argument("--no-facts", action="store_true", help="filing index only")
    company.set_defaults(handler=_cmd_ingest_company)

    tickers = ingest_sub.add_parser("tickers", help="same, resolved from ticker symbols")
    tickers.add_argument("tickers", nargs="+")
    tickers.add_argument("--no-facts", action="store_true")
    tickers.set_defaults(handler=_cmd_ingest_tickers)

    macro = ingest_sub.add_parser("macro", help="every vintage of each FRED series")
    macro.add_argument("series", nargs="*", default=list(DEFAULT_MACRO_SERIES))
    macro.set_defaults(handler=_cmd_ingest_macro)

    prices = ingest_sub.add_parser("prices", help="daily bars for the reachable universe")
    prices.add_argument("symbols", nargs="+")
    prices.add_argument("--start", type=date.fromisoformat, default=date(2015, 1, 1))
    prices.add_argument("--end", type=date.fromisoformat, default=None)
    prices.set_defaults(handler=_cmd_ingest_prices)

    delistings = ingest_sub.add_parser("delistings", help="names that left an exchange")
    delistings.set_defaults(handler=_cmd_ingest_delistings)

    daily = ingest_sub.add_parser("daily", help="every filing disseminated on a date")
    daily.add_argument("--date", type=date.fromisoformat, default=None, help="default: yesterday")
    daily.set_defaults(handler=_cmd_ingest_daily)

    asof = subparsers.add_parser("asof", help="what was knowable about a company on a given date")
    asof.add_argument("ticker")
    asof.add_argument("--concept", default="EarningsPerShareDiluted")
    asof.add_argument("--date", type=date.fromisoformat, required=True, help="knowledge date")
    asof.add_argument("--period-end", type=date.fromisoformat, default=None)
    asof.add_argument("--taxonomy", default="us-gaap")
    asof.add_argument(
        "--compare-restated",
        action="store_true",
        help="also show today's restated figure — the number a vendor panel would have given you",
    )
    asof.set_defaults(handler=_cmd_asof)

    revisions = subparsers.add_parser("revisions", help="values that changed after publication")
    revisions.add_argument("ticker")
    revisions.add_argument("--date", type=date.fromisoformat, default=None, help="knowledge date")
    revisions.add_argument("--concept", default=None)
    revisions.add_argument("--min-change", type=float, default=0.05)
    revisions.add_argument("--limit", type=int, default=20)
    revisions.set_defaults(handler=_cmd_revisions)

    return parser


# ------------------------------------------------------------------ commands --


def _cmd_status(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        print("configuration")
        for key, value in app.settings.describe().items():
            print(f"  {key:26s} {value}")
        print("\nwarehouse")
        for table in (
            "entities",
            "entity_identifiers",
            "filings",
            "facts",
            "macro_observations",
            "prices",
            "delistings",
            "raw_payloads",
            "ingest_runs",
        ):
            print(f"  {table:26s} {app.warehouse.count(table):>12,}")

        rows = app.warehouse.execute(
            """
            SELECT source, status, rows_written, started_at
              FROM ingest_runs ORDER BY started_at DESC LIMIT 8
            """
        ).fetchall()
        if rows:
            print("\nrecent runs")
            for source, status, written, started in rows:
                print(f"  {started:%Y-%m-%d %H:%M}  {status:7s} {written:>9,}  {source}")
    return 0


def _cmd_ingest_universe(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        return _report(app.ingestor.ingest_universe())


def _cmd_ingest_company(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        outcome = app.ingestor.ingest_companies(
            [Cik(value) for value in args.ciks], with_facts=not args.no_facts
        )
        return _report(outcome)


def _cmd_ingest_tickers(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        wanted = [ticker.upper() for ticker in args.tickers]
        placeholders = ", ".join("?" for _ in wanted)
        rows = app.warehouse.execute(
            f"""
            SELECT DISTINCT ticker, cik FROM entity_identifiers
             WHERE ticker IN ({placeholders})
            """,  # noqa: S608 - placeholders are generated, values are bound
            wanted,
        ).fetchall()
        found = {str(ticker): int(cik) for ticker, cik in rows}
        missing = [ticker for ticker in wanted if ticker not in found]
        if missing:
            print(f"not in the identifier table: {', '.join(missing)}")
            print("run `aletheia ingest universe` first")
        if not found:
            return 1
        outcome = app.ingestor.ingest_companies(
            [Cik(value) for value in found.values()], with_facts=not args.no_facts
        )
        return _report(outcome)


def _cmd_ingest_macro(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        if app.fred is None:
            print("FRED_API_KEY is not configured", file=sys.stderr)
            return 1
        return _report(app.ingestor.ingest_macro(args.series))


def _cmd_ingest_prices(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        if app.prices is None:
            print("FMP_API_KEY is not configured", file=sys.stderr)
            return 1
        end = args.end or app.clock.today()
        outcome = app.ingestor.ingest_prices(
            [symbol.upper() for symbol in args.symbols], start=args.start, end=end
        )
        if outcome.unreachable:
            print(
                f"\nsurvivorship exposure: {len(outcome.unreachable)} symbol(s) not served by the "
                f"vendor under this entitlement:\n  {', '.join(outcome.unreachable)}"
            )
        return _report(outcome)


def _cmd_ingest_delistings(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        if app.prices is None:
            print("FMP_API_KEY is not configured", file=sys.stderr)
            return 1
        return _report(app.ingestor.ingest_delistings())


def _cmd_ingest_daily(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir) as app:
        day = args.date or (app.clock.today() - timedelta(days=1))
        return _report(app.ingestor.ingest_daily_index(day))


def _cmd_asof(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir, read_only=True) as app:
        cik = _resolve_ticker(app, args.ticker)
        if cik is None:
            return 1
        view = as_of(app.warehouse, args.date)
        try:
            facts = view.facts(
                cik,
                args.concept,
                period_end=args.period_end,
                taxonomy=args.taxonomy,
                limit=None if args.period_end else 8,
            )
        except InsufficientData as exc:
            print(exc)
            return 1
        if not facts:
            print(f"nothing was published for {args.concept} as of {args.date}")
            return 1

        print(f"{args.ticker.upper()} · {args.concept} · as known on {args.date}\n")
        header = f"{'period ending':>14}  {'value':>18}  {'published':>10}  {'rpt':>3}  filing"
        print(header)
        print("-" * len(header))
        for fact in facts:
            # The marker reports the *value*, not the sequence number beside it.
            # Keyed off `is_first_report` this line printed "restated" on every
            # republication -- 6,314,367 rows of the warehouse, 5,798,180 of them
            # (91.8%) figures that had not moved at all. It fired on the two
            # Apple revenue rows in the README's own command, where a later 10-Q
            # simply carried the quarter forward as a comparative. Marking a
            # re-presentation as a restatement on the first command a reader
            # types is the one place this system cannot afford to cry wolf.
            if fact.differs_from_first_report:
                marker = "  ← restated"
            elif fact.is_first_report:
                marker = ""
            else:
                marker = "  ← re-presented"
            print(
                f"{fact.period_end!s:>14}  {_fmt(fact.value):>18}  "
                f"{fact.knowledge_date!s:>10}  {fact.report_seq:>3}  {fact.accn}{marker}"
            )

        if args.compare_restated and args.period_end:
            restated = view.unsafe_latest_restated(
                cik, args.concept, period_end=args.period_end, taxonomy=args.taxonomy
            )
            honest = facts[0]
            print(
                f"\nas it stands today (LOOKAHEAD — what a vendor panel would give you):"
                f"\n  {_fmt(restated.value)}  published {restated.knowledge_date}"
            )
            if honest.value != restated.value and honest.value != 0:
                drift = (restated.value - honest.value) / honest.value
                print(
                    f"  difference vs. what was knowable on {args.date}: "
                    f"{drift:+.2%} — the error a conventional backtest would make"
                )
    return 0


def _cmd_revisions(args: argparse.Namespace) -> int:
    with Application.build(data_dir=args.data_dir, read_only=True) as app:
        cik = _resolve_ticker(app, args.ticker)
        if cik is None:
            return 1
        view = as_of(app.warehouse, args.date or app.clock.today())
        revisions = view.revisions(cik, concept=args.concept, min_relative_change=args.min_change)
        if not revisions:
            print(f"no revisions above {args.min_change:.0%} were public as of {view.as_of}")
            return 0
        print(f"{args.ticker.upper()} · values revised by more than {args.min_change:.0%}")
        header = (
            f"{'period':>12}  {'concept':<38}  {'first':>14}  {'revised':>14}  "
            f"{'change':>8}  {'lag':>5}"
        )
        print(f"\n{header}\n" + "-" * len(header))
        for revision in revisions[: args.limit]:
            try:
                change = f"{revision.relative_change:+.1%}"
            except ZeroDivisionError:
                change = "from 0"
            print(
                f"{revision.period_end!s:>12}  {revision.concept[:38]:<38}  "
                f"{_fmt(revision.prior_value):>14}  {_fmt(revision.new_value):>14}  "
                f"{change:>8}  {revision.days_to_revision:>4}d"
            )
        if len(revisions) > args.limit:
            print(f"\n… {len(revisions) - args.limit:,} more not shown (--limit)")
    return 0


def _resolve_ticker(app: Application, ticker: str) -> Cik | None:
    row = app.warehouse.execute(
        "SELECT cik FROM entity_identifiers WHERE ticker = ? ORDER BY observed_at DESC LIMIT 1",
        [ticker.upper()],
    ).fetchone()
    if row is None:
        print(f"{ticker.upper()} is not in the identifier table; run `aletheia ingest universe`")
        return None
    return Cik(row[0])


def _fmt(value: Decimal) -> str:
    """Readable without lying: large magnitudes abbreviated, small ones exact."""
    return abbreviate(value)


def _report(outcome: object) -> int:
    summary = getattr(outcome, "summary", None)
    print(summary() if callable(summary) else str(outcome))
    failed = getattr(outcome, "failed", [])
    for failure in failed[:10]:
        print(f"  failed: {failure}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
