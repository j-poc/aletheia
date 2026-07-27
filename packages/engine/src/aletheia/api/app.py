"""HTTP surface over the point-in-time warehouse.

The API exists so the guarantee can be *seen* rather than read about. Its
centrepiece is ``/api/asof``, which answers the same question at two dates and
returns two different numbers -- the whole product in one request.

**Read-only by construction.** The warehouse is opened read-only, so no route can
mutate stored data even by accident. Ingest is a command-line operation with a
human behind it, not something a web request can trigger.

**The lookahead guarantee is not relaxed at the boundary.** Routes go through
:mod:`aletheia.pit` exactly as research code does. A request for a knowledge date
returns what was knowable then, and a route that wants today's restated figure has
to ask for it by the name that says so.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from aletheia.core.config import load_settings
from aletheia.core.errors import InsufficientData
from aletheia.core.formatting import plain
from aletheia.core.types import Cik
from aletheia.pit import PitFiling, PitView, as_of
from aletheia.store.db import Warehouse
from aletheia.surveillance.forensics import PERIODIC_FORMS, assess, rank

DEFAULT_CONCEPT = "EarningsPerShareDiluted"
LAG_ELIGIBLE_FORMS = PERIODIC_FORMS
"""Only these consult filer history, so only these need it fetched."""


class _State:
    """Holds the single read-only warehouse handle for the process.

    DuckDB permits one writer; readers are cheap. The handle is opened once at
    startup rather than per request so a page load does not pay to reopen a
    multi-gigabyte file.
    """

    warehouse: Warehouse | None = None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings = load_settings()
    state.warehouse = Warehouse.open(settings.warehouse_path, read_only=True, migrate=False)
    try:
        yield
    finally:
        if state.warehouse is not None:
            state.warehouse.close()
            state.warehouse = None


def get_warehouse() -> Iterator[Warehouse]:
    if state.warehouse is None:
        raise HTTPException(status_code=503, detail="warehouse is not open")
    yield state.warehouse


app = FastAPI(
    title="ALETHEIA",
    summary="Point-in-time evidence engine for systematic equity research.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    # The dev frontend only. Widening this is a deployment decision, not a default.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ helpers --


def _resolve(warehouse: Warehouse, ticker: str) -> tuple[Cik, str]:
    row = warehouse.execute(
        """
        SELECT i.cik, e.name
          FROM entity_identifiers i JOIN entities e ON e.cik = i.cik
         WHERE upper(i.ticker) = upper(?)
         ORDER BY i.observed_at DESC LIMIT 1
        """,
        [ticker],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no registrant maps to ticker {ticker!r}")
    return Cik(int(row[0])), str(row[1])


def _number(value: Decimal) -> str:
    """Money crosses the wire as a string.

    JSON numbers are IEEE doubles in every browser, so a Decimal serialised as a
    number stops being exact the moment it is parsed. The string keeps what was
    filed.
    """
    return plain(value)


def _data_vintage(warehouse: Warehouse) -> date:
    row = warehouse.execute("SELECT max(filed_at) FROM filings").fetchone()
    if row is None or row[0] is None:
        raise HTTPException(status_code=503, detail="warehouse holds no filings")
    return date.fromisoformat(str(row[0]))


def _view(warehouse: Warehouse, knowledge_date: date | None) -> PitView:
    return as_of(warehouse, knowledge_date or _data_vintage(warehouse))


# ------------------------------------------------------------------- routes --


@app.get("/api/health")
def health(warehouse: Warehouse = Depends(get_warehouse)) -> dict[str, Any]:
    return {"status": "ok", "data_vintage": _data_vintage(warehouse).isoformat()}


@app.get("/api/search")
def search(
    q: str = Query(min_length=1, max_length=32),
    limit: int = Query(default=15, ge=1, le=50),
    warehouse: Warehouse = Depends(get_warehouse),
) -> dict[str, Any]:
    """Ticker or company-name lookup, for the picker."""
    rows = warehouse.execute(
        """
        SELECT DISTINCT i.ticker, e.name, e.cik, e.sic_description
          FROM entity_identifiers i JOIN entities e ON e.cik = i.cik
         WHERE upper(i.ticker) LIKE upper(?) OR upper(e.name) LIKE upper(?)
         ORDER BY length(i.ticker), i.ticker
         LIMIT ?
        """,
        [f"{q}%", f"%{q}%", limit],
    ).fetchall()
    return {
        "results": [
            {
                "ticker": row[0],
                "name": row[1],
                "cik": int(row[2]),
                "industry": row[3],
            }
            for row in rows
        ]
    }


@app.get("/api/asof/{ticker}")
def asof(
    ticker: str,
    knowledge_date: date = Query(description="see the world as it was on this date"),
    concept: str = Query(default=DEFAULT_CONCEPT),
    period_end: date | None = Query(default=None),
    warehouse: Warehouse = Depends(get_warehouse),
) -> dict[str, Any]:
    """What was knowable, and what a vendor panel would have told you instead.

    Both figures are returned together because the gap between them is the point.
    The restated value is fetched through ``unsafe_latest_restated``, which is
    named that way so its use is visible here as it is everywhere else.
    """
    cik, name = _resolve(warehouse, ticker)
    view = _view(warehouse, knowledge_date)
    full = as_of(warehouse, _data_vintage(warehouse))

    try:
        # The latest report public on or before the knowledge date -- NOT the first
        # report. "What was knowable on D" changes as restatements arrive, and that
        # change is the entire demonstration: ask on 2009-12-01 and Apple's FY2008
        # diluted EPS is 5.36, ask on 2010-06-01 and it is 6.78, because the
        # restatement was published in between. Pinning this to first_reported would
        # return 5.36 on both dates and quietly remove the thing being shown.
        known = view.fact(cik, concept, period_end=period_end)
        first = view.first_reported(cik, concept, period_end=known.period_end)
    except InsufficientData as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    restated = full.unsafe_latest_restated(cik, concept, period_end=known.period_end)
    # Drift is measured from the FIRST report, not from whatever was current on the
    # knowledge date. Measuring from `known` reports +0.00% for any date after the
    # restatement landed, while the accompanying text still describes the move from
    # the original figure -- a number and a sentence disagreeing on the same card.
    drift = None
    if first.value != 0:
        drift = float((restated.value - first.value) / abs(first.value))

    return {
        "ticker": ticker.upper(),
        "company": name,
        "cik": int(cik),
        "concept": concept,
        "knowledge_date": knowledge_date.isoformat(),
        "as_known": _fact(known),
        "as_first_reported": _fact(first),
        "as_it_stands_today": _fact(restated),
        "relative_drift": drift,
        "is_restated": restated.accn.value != known.accn.value,
        "already_restated_by_then": known.accn.value != first.accn.value,
    }


@app.get("/api/revisions/{ticker}")
def revisions(
    ticker: str,
    concept: str | None = Query(default=None),
    min_change: float = Query(default=0.05, ge=0.0),
    limit: int = Query(default=50, ge=1, le=500),
    warehouse: Warehouse = Depends(get_warehouse),
) -> dict[str, Any]:
    """Values that changed after publication."""
    cik, name = _resolve(warehouse, ticker)
    view = _view(warehouse, None)
    found = view.revisions(cik, concept=concept, min_relative_change=min_change)
    return {
        "ticker": ticker.upper(),
        "company": name,
        "n_revisions": len(found),
        "revisions": [
            {
                "concept": revision.concept,
                "unit": revision.unit,
                "period_end": revision.period_end.isoformat(),
                "prior_value": _number(revision.prior_value),
                "new_value": _number(revision.new_value),
                "prior_knowledge_date": revision.prior_knowledge_date.isoformat(),
                "new_knowledge_date": revision.new_knowledge_date.isoformat(),
                "days_to_revision": revision.days_to_revision,
                "relative_change": float(revision.relative_change)
                if revision.prior_value != 0
                else None,
                "new_accn": revision.new_accn.value,
                "new_form": revision.new_form,
            }
            for revision in found[:limit]
        ],
    }


@app.get("/api/feed")
def feed(
    day: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    warehouse: Warehouse = Depends(get_warehouse),
) -> dict[str, Any]:
    """Filings disseminated on a date, ranked by forensic concern.

    An empty result is a legitimate answer -- weekends and holidays exist -- and is
    returned as an empty list with the date echoed, not as an error.
    """
    target = day or _data_vintage(warehouse)
    # A view bounded at `target` combined with `since=target` isolates exactly the
    # filings that became knowable that day: the view caps the upper end and
    # `since` the lower.
    view = as_of(warehouse, target)
    filings = view.filings(since=target)

    # Filer history is only consulted by the filing-lag flag, which applies to
    # periodic reports alone. Fetching it for every filing meant ~3,900 extra
    # queries on a normal day -- one per filing -- to inform a check that most of
    # them are not eligible for. Fetched once per distinct periodic filer instead.
    history_by_cik: dict[int, list[PitFiling]] = {}
    periodic_ciks = {int(filing.cik) for filing in filings if filing.form in LAG_ELIGIBLE_FORMS}
    for cik_value in sorted(periodic_ciks):
        history_by_cik[cik_value] = view.filings(cik=cik_value, forms=LAG_ELIGIBLE_FORMS)

    assessments = []
    for filing in filings:
        history = [
            past
            for past in history_by_cik.get(int(filing.cik), ())
            if past.knowledge_date < filing.knowledge_date
        ]
        assessments.append(assess(filing, filer_history=history))

    ranked = rank(assessments)
    return {
        "date": target.isoformat(),
        "n_filings": len(filings),
        "n_flagged": len(ranked),
        "items": [
            {
                "accn": item.accn,
                "cik": item.cik,
                "form": item.form,
                "knowledge_date": item.knowledge_date.isoformat(),
                "score": item.score,
                "confidence": item.confidence.name,
                "confidence_meaning": item.confidence.value,
                "findings": [
                    {"flag": finding.flag.name, "evidence": finding.evidence}
                    for finding in item.findings
                ],
            }
            for item in ranked[:limit]
        ],
    }


@app.get("/api/quality")
def quality(warehouse: Warehouse = Depends(get_warehouse)) -> dict[str, Any]:
    """Coverage and lineage. What is actually in here, and where it came from."""
    counts = {}
    for table in (
        "entities",
        "entity_identifiers",
        "filings",
        "facts",
        "prices",
        "macro_observations",
        "delistings",
        "ingest_runs",
        "raw_payloads",
    ):
        row = warehouse.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        counts[table] = int(row[0]) if row else 0

    # period_start is part of the key, not decoration. A fiscal year and its fourth
    # quarter share a period_end and are different periods reporting different
    # numbers; grouping without the start date reads that as a revision. On this
    # warehouse that mistake manufactured 667,003 of them -- it turned a true 5.0%
    # into a false 16.4%.
    revised = warehouse.execute(
        """
        SELECT count(*) FROM (
            SELECT cik, concept, unit, period_start, period_end
              FROM facts GROUP BY ALL HAVING count(DISTINCT value) > 1
        )
        """
    ).fetchone()
    periods = warehouse.execute(
        "SELECT count(*) FROM ("
        "SELECT cik, concept, unit, period_start, period_end FROM facts GROUP BY ALL)"
    ).fetchone()

    runs = warehouse.execute(
        """
        SELECT source, count(*), max(started_at)
          FROM ingest_runs GROUP BY source ORDER BY source
        """
    ).fetchall()
    indexed, on_disk = warehouse.payload_ledger_coverage(load_settings().raw_dir)

    return {
        "data_vintage": _data_vintage(warehouse).isoformat(),
        "row_counts": counts,
        "revision_coverage": {
            "distinct_periods": int(periods[0]) if periods else 0,
            "periods_with_a_changed_value": int(revised[0]) if revised else 0,
        },
        "ingest_runs": [
            {"source": row[0], "runs": int(row[1]), "last_started": str(row[2])} for row in runs
        ],
        "payload_ledger": {
            "indexed": indexed,
            "on_disk": on_disk,
            # Reported, not reconciled. The store keeps bytes keyed by hash and no
            # metadata, so a backfill would have to invent source_uri and
            # retrieved_at -- and fabricated provenance cannot be told apart from
            # the real thing. Every fact still carries its own content hash, which
            # resolves to a file on disk; this counts the index, not the evidence.
            "note": (
                "payloads fetched before indexing moved into the fetcher are on "
                "disk and addressable by hash, but not enumerated here"
            )
            if on_disk > indexed
            else "complete",
        },
    }


@app.get("/api/evidence")
def evidence_index() -> dict[str, Any]:
    """Every evidence card written to disk, newest first.

    An empty list means no study has been run yet, which is reported as such
    rather than as an error -- a fresh checkout legitimately has no results.
    """
    directory = load_settings().data_dir / "evidence"
    if not directory.exists() or not any(directory.glob("*.json")):
        # An existing-but-empty directory is the common case -- the study writes it
        # before it writes a card -- and must read the same as no directory at all.
        return {"cards": [], "note": "no study has been run in this warehouse yet"}
    cards = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cards.append(
            {
                "study_id": payload.get("study_id"),
                "hypothesis": payload.get("hypothesis"),
                "verdict": payload.get("verdict"),
                "trial_count": payload.get("trial_count"),
                "trial_family": payload.get("trial_family"),
                "repro_hash": payload.get("repro_hash"),
                "generated_at": payload.get("generated_at"),
                "provenance": payload.get("provenance"),
                "arms": payload.get("arms", []),
                "comparisons": payload.get("comparisons", []),
                "caveats": payload.get("caveats", []),
                "statistics": payload.get("statistics", {}),
            }
        )
    cards.sort(key=lambda card: str(card.get("generated_at")), reverse=True)
    return {"cards": cards}


def _fact(fact: Any) -> dict[str, Any]:
    return {
        "value": _number(fact.value),
        "unit": fact.unit,
        "period_end": fact.period_end.isoformat(),
        "filed_at": fact.filed_at.isoformat(),
        "knowledge_date": fact.knowledge_date.isoformat(),
        "accn": fact.accn.value,
        "form": fact.form,
        "report_seq": fact.report_seq,
        "source_uri": fact.source_uri,
    }
