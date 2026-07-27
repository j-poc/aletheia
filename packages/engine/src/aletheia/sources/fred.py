"""FRED / ALFRED client — macro data with real vintages.

Macro series are revised, often heavily and for years. US real GDP for 2020Q2
has **eight** distinct published values (verified live 2026-07-27); a backtest
using today's figure for a 2020 decision is using a number that did not exist
until 2023.

ALFRED — the vintage archive behind FRED — solves this properly. Requesting
``realtime_start=1776-07-04&realtime_end=9999-12-31`` returns one row per
(observation date, vintage), each with the window over which that value was the
published one. ``realtime_start`` is therefore the knowledge date, supplied by
the publisher.

Contract verified live 2026-07-27:
``https://api.stlouisfed.org/fred/series/observations`` — response carries
``count``/``limit``/``observations``; each observation has ``realtime_start``,
``realtime_end``, ``date``, ``value``; a missing value is the string ``"."``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any, Final

from aletheia.core.config import Secret
from aletheia.core.errors import ContractViolation
from aletheia.core.types import MacroObservation
from aletheia.sources.base import ParseReport, parse_date, require_key, require_mapping
from aletheia.sources.http import Fetcher, redact

SOURCE: Final = "fred"
BASE: Final = "https://api.stlouisfed.org/fred"

# ALFRED's sentinels for "from the beginning of the archive" and "to the end of
# time". Together they ask for every vintage that has ever existed.
ALL_VINTAGES_START: Final = "1776-07-04"
ALL_VINTAGES_END: Final = "9999-12-31"
MAX_ROWS_PER_REQUEST: Final = 100_000

# ALFRED refuses a request spanning more than 2,000 vintage dates (verified live:
# DGS10 has 5,073 and returns HTTP 400 naming the limit). Daily series cross it
# easily — every business day is a vintage date even when nothing is revised — so
# the archive is walked in windows and stitched back together.
MAX_VINTAGE_DATES_PER_REQUEST: Final = 2_000
VINTAGE_CHUNK_SIZE: Final = 1_500  # margin below the ceiling


class FredClient:
    """Vintage-aware reads of FRED series."""

    def __init__(self, fetcher: Fetcher, *, api_key: Secret) -> None:
        self._fetch = fetcher
        self._api_key = api_key

    def vintage_dates(self, series_id: str) -> list[date]:
        """Every date on which this series was published or revised."""
        uri = (
            f"{BASE}/series/vintagedates?series_id={series_id}"
            f"&api_key={self._api_key.reveal()}&file_type=json&limit=10000"
        )
        safe_uri = redact(uri)
        result = self._fetch.get(uri, source=SOURCE)
        payload = require_mapping(
            json.loads(result.body), source=SOURCE, uri=safe_uri, what="vintagedates"
        )
        raw = require_key(payload, "vintage_dates", source=SOURCE, uri=safe_uri)
        if not isinstance(raw, list):
            raise ContractViolation("vintage_dates is not a list", source=SOURCE, uri=safe_uri)
        total = payload.get("count")
        if isinstance(total, int) and total > len(raw):
            raise ContractViolation(
                f"{series_id} has {total} vintage dates but only {len(raw)} were returned; "
                f"paginate before trusting this series",
                source=SOURCE,
                uri=safe_uri,
            )
        dates = [parse_date(str(value)) for value in raw]
        return [value for value in dates if value is not None]

    def all_vintages(
        self,
        series_id: str,
        *,
        run_id: str,
        observation_start: date | None = None,
        observation_end: date | None = None,
    ) -> tuple[list[MacroObservation], ParseReport]:
        """Every published value of ``series_id``, across every vintage.

        Returns one record per (observation date, vintage span). A series that has
        never been revised yields one row per observation; a heavily revised one
        yields many, which is the entire point.

        Series with more vintage dates than ALFRED will serve in one request are
        fetched in windows and stitched back together by
        :func:`_merge_unrevised_runs` — see that function for why a naive
        concatenation of windows would manufacture revisions that never happened.
        """
        vintages = self.vintage_dates(series_id)
        if len(vintages) > MAX_VINTAGE_DATES_PER_REQUEST:
            return self._chunked_vintages(
                series_id,
                vintages,
                run_id=run_id,
                observation_start=observation_start,
                observation_end=observation_end,
            )
        return self._window(
            series_id,
            realtime_start=ALL_VINTAGES_START,
            realtime_end=ALL_VINTAGES_END,
            run_id=run_id,
            observation_start=observation_start,
            observation_end=observation_end,
        )

    def _chunked_vintages(
        self,
        series_id: str,
        vintages: list[date],
        *,
        run_id: str,
        observation_start: date | None,
        observation_end: date | None,
    ) -> tuple[list[MacroObservation], ParseReport]:
        report = ParseReport()
        collected: list[MacroObservation] = []
        for index in range(0, len(vintages), VINTAGE_CHUNK_SIZE):
            window = vintages[index : index + VINTAGE_CHUNK_SIZE]
            # The first window must reach back to the start of the archive, or
            # every observation older than the window would be stamped with the
            # window's start date instead of its true first-publication date.
            start = ALL_VINTAGES_START if index == 0 else window[0].isoformat()
            records, window_report = self._window(
                series_id,
                realtime_start=start,
                realtime_end=window[-1].isoformat(),
                run_id=run_id,
                observation_start=observation_start,
                observation_end=observation_end,
            )
            collected.extend(records)
            report.merge(window_report)
        merged = _merge_unrevised_runs(collected)
        report.skip("window overlap collapsed", len(collected) - len(merged))
        return merged, report

    def _window(
        self,
        series_id: str,
        *,
        realtime_start: str,
        realtime_end: str,
        run_id: str,
        observation_start: date | None = None,
        observation_end: date | None = None,
    ) -> tuple[list[MacroObservation], ParseReport]:
        params = [
            ("series_id", series_id),
            ("api_key", self._api_key.reveal()),
            ("file_type", "json"),
            ("realtime_start", realtime_start),
            ("realtime_end", realtime_end),
            ("limit", str(MAX_ROWS_PER_REQUEST)),
        ]
        if observation_start:
            params.append(("observation_start", observation_start.isoformat()))
        if observation_end:
            params.append(("observation_end", observation_end.isoformat()))
        query = "&".join(f"{key}={value}" for key, value in params)
        uri = f"{BASE}/series/observations?{query}"
        safe_uri = redact(uri)

        result = self._fetch.get(uri, source=SOURCE)
        payload = require_mapping(
            json.loads(result.body), source=SOURCE, uri=safe_uri, what="observations"
        )
        observations = require_key(payload, "observations", source=SOURCE, uri=safe_uri)
        if not isinstance(observations, list):
            raise ContractViolation("observations is not a list", source=SOURCE, uri=safe_uri)

        total = payload.get("count")
        if isinstance(total, int) and total > MAX_ROWS_PER_REQUEST:
            # Silently receiving a truncated series would understate the revision
            # history of exactly the series that revise most.
            raise ContractViolation(
                f"{series_id} has {total} vintage rows, above the {MAX_ROWS_PER_REQUEST} "
                f"request limit; paginate before trusting this series",
                source=SOURCE,
                uri=safe_uri,
            )

        report = ParseReport()
        records: list[MacroObservation] = []
        for entry in observations:
            record = _parse_observation(
                entry,
                series_id=series_id,
                uri=safe_uri,
                sha=result.content_sha256,
                retrieved_at=result.stored.retrieved_at,
                run_id=run_id,
                report=report,
            )
            if record is not None:
                records.append(record)
        return records, report


def _merge_unrevised_runs(records: list[MacroObservation]) -> list[MacroObservation]:
    """Collapse consecutive vintages that carry the same value.

    Necessary because windowing clips vintage spans. Ask ALFRED for the archive
    in two windows and an observation that was never revised comes back twice —
    once ending at the first window's edge, once starting at the second's. Written
    as-is, that reads downstream as a revision on the window boundary: a revision
    that never happened, at a date chosen by our chunking rather than by the
    publisher.

    Merging runs of equal value into one span, keeping the earliest
    ``realtime_start`` and the latest ``realtime_end``, restores exactly what a
    single unchunked request would have returned.
    """
    by_observation: dict[tuple[str, date], list[MacroObservation]] = {}
    for record in records:
        by_observation.setdefault((record.series_id, record.obs_date), []).append(record)

    merged: list[MacroObservation] = []
    for group in by_observation.values():
        ordered = sorted(group, key=lambda r: r.realtime_start)
        current = ordered[0]
        for candidate in ordered[1:]:
            if candidate.value == current.value:
                # Same published value, later window: extend the span rather than
                # recording a second "publication".
                current = replace(
                    current, realtime_end=max(current.realtime_end, candidate.realtime_end)
                )
                continue
            merged.append(current)
            current = candidate
        merged.append(current)

    # Sorted output keeps the write order deterministic, which keeps re-runs
    # byte-identical.
    merged.sort(key=lambda r: (r.series_id, r.obs_date, r.realtime_start))
    return merged


def _parse_observation(
    entry: Any,
    *,
    series_id: str,
    uri: str,
    sha: str,
    retrieved_at: Any,
    run_id: str,
    report: ParseReport,
) -> MacroObservation | None:
    if not isinstance(entry, dict):
        report.skip("observation is not an object")
        return None
    obs_date = parse_date(entry.get("date"))
    realtime_start = parse_date(entry.get("realtime_start"))
    realtime_end = parse_date(entry.get("realtime_end"))
    if obs_date is None or realtime_start is None or realtime_end is None:
        report.skip("observation missing a date or a vintage window")
        return None

    raw_value = entry.get("value")
    # FRED encodes a genuinely missing observation as ".". It is kept as NULL
    # rather than dropped: "this period had no published value" is information,
    # and dropping it would silently shorten the series.
    value = None if raw_value in (".", "", None) else _to_float(raw_value, report)

    report.parsed += 1
    return MacroObservation(
        series_id=series_id,
        obs_date=obs_date,
        value=value,
        realtime_start=realtime_start,
        realtime_end=realtime_end,
        source_uri=uri,
        retrieved_at=retrieved_at,
        content_sha256=sha,
        ingest_run_id=run_id,
    )


def _to_float(raw: Any, report: ParseReport) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        report.skip("non-numeric observation value")
        return None
