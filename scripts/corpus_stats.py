"""Recompute every corpus number the README claims.

The README's headline statistic was wrong once already: it said 16.4% of reported
values change after publication, because the query grouped facts by
``(cik, concept, unit, period_end)`` and omitted ``period_start``. A fiscal year
and its fourth quarter share an end date, so 667,003 pairs of *different periods*
were counted as *the same period revised*. The true figure is 5.0%.

It survived because no committed code produced it -- it came from a query typed
into a shell, pasted into the document, and never run again. So this script
exists, `make stats` runs it, and the README quotes its output. A number that
cannot be regenerated is a number nobody can check, including its author.

The grouping key here is the same one the warehouse itself uses in
``v_facts_pit`` and ``v_fact_revisions``: cik, taxonomy, concept, unit,
period_start, period_end. If those views and this script ever disagree, one of
them is wrong and the difference will show up as a changed number rather than as
silence.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from aletheia.store.db import Warehouse

PERIOD_KEY = "cik, taxonomy, concept, unit, period_start, period_end"
"""The warehouse's own bitemporal key for a reported fact.

``period_end`` alone is not it. Two facts sharing an end date and differing in
start date are a year and a quarter, not a value and its restatement."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("data/warehouse.duckdb"))
    args = parser.parse_args(argv)

    if not args.warehouse.exists():
        print(f"no warehouse at {args.warehouse}. Run `make demo` or `make ingest` first.")
        return 1

    with Warehouse.open(args.warehouse, read_only=True, migrate=False) as store:
        filers = store.count("entities")
        filings = store.count("filings")
        facts = store.count("facts")
        vintage = store.execute("SELECT max(filed_at) FROM filings").fetchone()

        periods = store.execute(
            f"SELECT count(*) FROM (SELECT {PERIOD_KEY} FROM facts GROUP BY ALL)"  # noqa: S608
        ).fetchone()[0]
        revised = store.execute(
            f"SELECT count(*) FROM (SELECT {PERIOD_KEY} FROM facts "  # noqa: S608
            f"GROUP BY ALL HAVING count(DISTINCT value) > 1)"
        ).fetchone()[0]

        # The wrong key, computed alongside so the size of the error stays visible
        # rather than becoming folklore about "that time the number was off".
        loose_periods = store.execute(
            "SELECT count(*) FROM (SELECT cik, concept, unit, period_end FROM facts GROUP BY ALL)"
        ).fetchone()[0]
        loose_revised = store.execute(
            "SELECT count(*) FROM (SELECT cik, concept, unit, period_end FROM facts "
            "GROUP BY ALL HAVING count(DISTINCT value) > 1)"
        ).fetchone()[0]

    print(f"data vintage        {vintage[0] if vintage else 'unknown'}")
    print(f"filers              {filers:>12,}")
    print(f"filings             {filings:>12,}")
    print(f"facts               {facts:>12,}")
    print()
    print("Revision coverage, keyed on " + PERIOD_KEY)
    print(f"  distinct periods  {periods:>12,}")
    print(f"  with >1 value     {revised:>12,}")
    print(f"  share             {revised / periods:>12.1%}" if periods else "  share  n/a")
    print()
    print("Same query without period_start -- the mistake, kept visible:")
    print(f"  distinct periods  {loose_periods:>12,}")
    print(f"  with >1 value     {loose_revised:>12,}")
    if loose_periods and periods:
        print(f"  share             {loose_revised / loose_periods:>12.1%}")
        inflation = (loose_revised - revised) / revised if revised else 0.0
        print(f"  overstates by     {inflation:>12.0%}  ({loose_revised - revised:,} spurious)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
