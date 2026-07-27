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
from pathlib import Path

from aletheia.app import Application
from aletheia.core.errors import AletheiaError
from aletheia.core.types import Cik

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


def _report(outcome: object) -> int:
    summary = getattr(outcome, "summary", None)
    print(summary() if callable(summary) else str(outcome))
    failed = getattr(outcome, "failed", [])
    for failure in failed[:10]:
        print(f"  failed: {failure}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
