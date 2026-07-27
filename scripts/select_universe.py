"""Choose the study universe from a point-in-time SEC cross-section.

The temptation is to take today's S&P 500 and run history over it. That is
survivorship bias in its purest form: every member is a company that survived to
today, and the ones that failed -- exactly the ones a fundamental signal should
have flagged -- are absent by construction.

Instead the universe is drawn from the SEC's own XBRL ``frames`` endpoint for
``Assets`` at ``CY2011Q4I``: every filer that reported total assets for a period
ending in the fourth quarter of 2011. Membership is decided by 2011 filings and
nothing else. A company that went bankrupt in 2014 is in this list, because in
2011 it was there.

The rule, fixed before any result was seen:

1. All filers in ``us-gaap/Assets/USD/CY2011Q4I``.
2. Keep those with total assets >= $500M -- a materiality floor, so the sample is
   not dominated by shells and micro-caps whose XBRL is sparse and whose prices
   are unobtainable.
3. Take a random sample of ``--sample`` names with a fixed seed, rather than the
   largest N. Largest-N would tilt the sample to capital-intensive firms and
   banks; a random draw from the eligible set is representative of it.

Sector exclusions (financials, utilities) are NOT applied here. Ingest is broad;
selection by economic reasoning happens at study time, where it is visible.

Usage::

    uv run python scripts/select_universe.py --sample 800 --out data/universe_2011.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import httpx

from aletheia.core.config import load_settings

FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2011Q4I.json"
DEFAULT_FLOOR_USD = 500_000_000
DEFAULT_SEED = 20111231
"""The as-of date of the cross-section, used as the seed so it is not a free knob."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=800)
    parser.add_argument("--floor", type=int, default=DEFAULT_FLOOR_USD)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("data/universe_2011.json"))
    parser.add_argument(
        "--user-agent",
        default=None,
        help=(
            "Contact address for the SEC User-Agent. Defaults to "
            "ALETHEIA_SEC_USER_AGENT. The SEC asks automated clients to identify "
            "themselves and throttles those that do not."
        ),
    )
    args = parser.parse_args(argv)
    user_agent = args.user_agent or load_settings().sec_user_agent

    with httpx.Client(timeout=120, headers={"User-Agent": user_agent}) as client:
        response = client.get(FRAME_URL)
        response.raise_for_status()
        frame: dict[str, Any] = response.json()

    rows = frame["data"]
    eligible = [row for row in rows if row["val"] >= args.floor]
    # Sort before sampling: dict iteration order from JSON is stable, but sorting
    # makes the draw depend only on the seed and the eligible set, not on the
    # order the SEC happened to serialise.
    eligible.sort(key=lambda row: int(row["cik"]))

    rng = random.Random(args.seed)  # noqa: S311 - sampling a universe, not cryptography
    chosen = sorted(
        rng.sample(eligible, min(args.sample, len(eligible))), key=lambda r: int(r["cik"])
    )

    payload = {
        "frame": frame["ccp"],
        "tag": frame["tag"],
        "source_uri": FRAME_URL,
        "rule": {
            "assets_floor_usd": args.floor,
            "sample_size": args.sample,
            "seed": args.seed,
        },
        "counts": {
            "filers_in_frame": len(rows),
            "eligible_at_floor": len(eligible),
            "selected": len(chosen),
        },
        "members": [
            {"cik": int(row["cik"]), "name": row["entityName"], "assets_usd": int(row["val"])}
            for row in chosen
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"filers in CY2011Q4I Assets frame : {len(rows):,}")
    print(f"with assets >= ${args.floor / 1e6:,.0f}M   : {len(eligible):,}")
    print(f"sampled (seed {args.seed})          : {len(chosen):,}")
    print(f"written to                        : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
