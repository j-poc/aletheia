"""Pull filings, metadata and XBRL facts for every member of the study universe.

Long-running and resumable. Companies already present in ``entities`` are skipped
unless ``--force`` is given, so an interrupted run continues where it stopped
rather than re-downloading several gigabytes.

Progress is printed per batch so a background run can be monitored, and every
failure is named. A silently shrunk universe is the failure mode this guards
against: partial coverage that looks like a clean result.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from aletheia.app import Application
from aletheia.core.types import Cik


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=Path("data/universe_2011.json"))
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many companies.")
    parser.add_argument("--force", action="store_true", help="Re-ingest companies already present.")
    args = parser.parse_args(argv)

    members = json.loads(args.universe.read_text(encoding="utf-8"))["members"]
    ciks = [Cik(int(member["cik"])) for member in members]

    with Application.build() as app:
        if not args.force:
            known = {
                int(row[0])
                for row in app.warehouse.execute("SELECT DISTINCT cik FROM entities").fetchall()
            }
            skipped = sum(1 for cik in ciks if int(cik) in known)
            ciks = [cik for cik in ciks if int(cik) not in known]
            print(f"already ingested, skipping: {skipped:,}", flush=True)
        if args.limit is not None:
            ciks = ciks[: args.limit]

        print(f"to ingest: {len(ciks):,} companies", flush=True)
        started = time.monotonic()
        written = 0
        failures: list[str] = []

        for offset in range(0, len(ciks), args.batch):
            chunk = ciks[offset : offset + args.batch]
            outcome = app.ingestor.ingest_companies(chunk)
            written += outcome.rows_written
            failures.extend(outcome.failed)
            done = offset + len(chunk)
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed else 0.0
            remaining = (len(ciks) - done) / rate if rate else 0.0
            print(
                f"[{done:>5}/{len(ciks)}] rows={written:>10,} "
                f"failed={len(failures):>3} {rate:.2f} co/s  eta={remaining / 60:.0f}m",
                flush=True,
            )

        print(f"\ndone in {(time.monotonic() - started) / 60:.1f} min; {written:,} rows")
        if failures:
            print(f"{len(failures)} failures:")
            for failure in failures[:40]:
                print(f"  {failure}")
            if len(failures) > 40:
                print(f"  ... and {len(failures) - 40} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
