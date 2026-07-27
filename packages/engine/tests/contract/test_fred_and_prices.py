"""FRED vintage handling and the price adapter.

The critical test here is the window-overlap collapse. ALFRED caps a request at
2,000 vintage dates, so long daily series must be fetched in windows — and a
naive concatenation of windows manufactures a "revision" at every window
boundary, on a date chosen by our chunking rather than by the publisher. Both
directions are asserted: fabricated revisions must disappear, real ones must
survive.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aletheia.core.config import Secret
from aletheia.core.errors import ContractViolation, PermanentSourceError
from aletheia.core.types import MacroObservation
from aletheia.provenance.payloads import PayloadStore
from aletheia.sources.fred import FredClient, _merge_unrevised_runs
from aletheia.sources.prices import DelistedCoverageError, FmpPriceSource
from tests._fakes import FIXED_INSTANT, RecordedFetcher

RUN_ID = "run-contract-0002"


def _observation(obs: str, start: str, end: str, value: float | None) -> MacroObservation:
    return MacroObservation(
        series_id="GDPC1",
        obs_date=date.fromisoformat(obs),
        value=value,
        realtime_start=date.fromisoformat(start),
        realtime_end=date.fromisoformat(end),
        source_uri="https://api.stlouisfed.org/fred/series/observations?api_key=***",
        retrieved_at=FIXED_INSTANT,
        content_sha256="0" * 64,
        ingest_run_id=RUN_ID,
    )


class TestVintageWindowMerge:
    def test_collapses_a_boundary_that_is_not_a_revision(self) -> None:
        """Same value either side of a window edge is one publication, not two."""
        merged = _merge_unrevised_runs(
            [
                _observation("2020-04-01", "2020-07-30", "2022-12-31", 17205.822),
                _observation("2020-04-01", "2023-01-01", "2024-12-31", 17205.822),
            ]
        )
        assert len(merged) == 1
        assert merged[0].realtime_start == date(2020, 7, 30)
        assert merged[0].realtime_end == date(2024, 12, 31)

    def test_keeps_a_genuine_revision(self) -> None:
        """Positive control: a real value change must survive the same code path."""
        merged = _merge_unrevised_runs(
            [
                _observation("2020-04-01", "2020-07-30", "2020-08-26", 17205.822),
                _observation("2020-04-01", "2020-08-27", "2020-09-29", 17282.188),
            ]
        )
        assert len(merged) == 2
        assert [round(m.value or 0.0, 3) for m in merged] == [17205.822, 17282.188]

    def test_a_value_that_reverts_is_two_publications_not_one(self) -> None:
        """A → B → A is three publications; merging the two A's would lose history."""
        merged = _merge_unrevised_runs(
            [
                _observation("2020-04-01", "2020-01-01", "2020-02-01", 1.0),
                _observation("2020-04-01", "2020-02-02", "2020-03-01", 2.0),
                _observation("2020-04-01", "2020-03-02", "2020-04-01", 1.0),
            ]
        )
        assert [m.value for m in merged] == [1.0, 2.0, 1.0]

    def test_missing_values_merge_like_any_other_value(self) -> None:
        merged = _merge_unrevised_runs(
            [
                _observation("2020-04-01", "2020-01-01", "2020-02-01", None),
                _observation("2020-04-01", "2020-02-02", "2020-03-01", None),
            ]
        )
        assert len(merged) == 1
        assert merged[0].value is None

    def test_output_order_is_deterministic(self) -> None:
        """Re-runs must be byte-identical, so write order cannot depend on a dict."""
        records = [
            _observation("2020-07-01", "2020-10-01", "2021-01-01", 3.0),
            _observation("2020-04-01", "2020-07-30", "2020-10-01", 1.0),
        ]
        assert [m.obs_date for m in _merge_unrevised_runs(records)] == [
            date(2020, 4, 1),
            date(2020, 7, 1),
        ]


class TestFredParsing:
    @pytest.fixture
    def client(self, tmp_path: Path) -> FredClient:
        return FredClient(
            RecordedFetcher(PayloadStore(tmp_path / "raw")),  # type: ignore[arg-type]
            api_key=Secret("test-key"),
        )

    def test_parses_vintages_and_treats_a_dot_as_missing(self, client: FredClient) -> None:
        """FRED writes "." for an unpublished observation; it is NULL, not zero."""
        fetcher = client._fetch  # noqa: SLF001
        fetcher.record(json.dumps({"count": 1, "vintage_dates": ["2020-07-30"]}).encode())
        fetcher.record(
            json.dumps(
                {
                    "count": 2,
                    "limit": 100000,
                    "observations": [
                        {
                            "realtime_start": "2020-07-30",
                            "realtime_end": "2020-08-26",
                            "date": "2020-04-01",
                            "value": "17205.822",
                        },
                        {
                            "realtime_start": "2020-07-30",
                            "realtime_end": "9999-12-31",
                            "date": "2020-07-01",
                            "value": ".",
                        },
                    ],
                }
            ).encode()
        )
        records, report = client.all_vintages("GDPC1", run_id=RUN_ID)
        assert report.parsed == 2
        assert records[0].value == pytest.approx(17205.822)
        assert records[1].value is None

    def test_the_api_key_never_reaches_the_stored_uri(self, client: FredClient) -> None:
        """Provenance is written to the warehouse; a key must not travel with it."""
        fetcher = client._fetch  # noqa: SLF001
        fetcher.record(json.dumps({"count": 1, "vintage_dates": ["2020-07-30"]}).encode())
        fetcher.record(
            json.dumps(
                {
                    "count": 1,
                    "observations": [
                        {
                            "realtime_start": "2020-07-30",
                            "realtime_end": "9999-12-31",
                            "date": "2020-04-01",
                            "value": "1.0",
                        }
                    ],
                }
            ).encode()
        )
        records, _ = client.all_vintages("GDPC1", run_id=RUN_ID)
        assert "test-key" not in records[0].source_uri
        assert "api_key=***" in records[0].source_uri

    def test_a_truncated_series_is_refused(self, client: FredClient) -> None:
        """Silently receiving 100k of 150k rows understates the revision history."""
        fetcher = client._fetch  # noqa: SLF001
        fetcher.record(json.dumps({"count": 1, "vintage_dates": ["2020-07-30"]}).encode())
        fetcher.record(json.dumps({"count": 150000, "observations": []}).encode())
        with pytest.raises(ContractViolation, match="above the"):
            client.all_vintages("GDPC1", run_id=RUN_ID)


class TestFmpPrices:
    @pytest.fixture
    def source(self, tmp_path: Path) -> FmpPriceSource:
        return FmpPriceSource(
            RecordedFetcher(PayloadStore(tmp_path / "raw")),  # type: ignore[arg-type]
            api_key=Secret("test-key"),
        )

    def test_joins_adjusted_closes_onto_as_traded_bars(self, source: FmpPriceSource) -> None:
        fetcher = source._fetch  # noqa: SLF001
        fetcher.record(
            json.dumps(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2020-08-31",
                        "open": 127.58,
                        "high": 131.0,
                        "low": 126.0,
                        "close": 129.04,
                        "volume": 225702700,
                    }
                ]
            ).encode()
        )
        fetcher.record(
            json.dumps(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2020-08-31",
                        "adjOpen": 125.0,
                        "adjHigh": 128.4,
                        "adjLow": 123.5,
                        "adjClose": 126.5,
                        "volume": 225702700,
                    }
                ]
            ).encode()
        )
        bars, report = source.daily_bars(
            "AAPL", start=date(2020, 8, 31), end=date(2020, 8, 31), run_id=RUN_ID
        )
        assert report.parsed == 1
        assert bars[0].close == pytest.approx(129.04)
        assert bars[0].adj_close == pytest.approx(126.5)

    def test_a_bar_without_an_adjusted_partner_keeps_a_null(self, source: FmpPriceSource) -> None:
        """Null is honest. Copying `close` into `adj_close` would fabricate one."""
        fetcher = source._fetch  # noqa: SLF001
        fetcher.record(
            json.dumps(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2020-08-31",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1,
                    }
                ]
            ).encode()
        )
        fetcher.record(b"[]")
        bars, _ = source.daily_bars(
            "AAPL", start=date(2020, 8, 31), end=date(2020, 8, 31), run_id=RUN_ID
        )
        assert bars[0].adj_close is None

    def test_an_entitlement_refusal_is_its_own_error(self, source: FmpPriceSource) -> None:
        """402 means "delisted, not in your plan" — countable survivorship exposure."""
        fetcher = source._fetch  # noqa: SLF001
        fetcher.record_error(
            PermanentSourceError("HTTP 402: Premium Query Parameter", source="fmp", uri="https://x")
        )
        with pytest.raises(DelistedCoverageError, match="not covered"):
            source.daily_bars("SIVB", start=date(2023, 1, 1), end=date(2023, 3, 1), run_id=RUN_ID)

    def test_an_error_object_returned_with_http_200_is_not_an_empty_result(
        self, source: FmpPriceSource
    ) -> None:
        """FMP reports plan problems with 200 and an object body.

        Reading that as "no rows" would record "this symbol has no price history"
        for a symbol we were simply not served.
        """
        fetcher = source._fetch  # noqa: SLF001
        fetcher.record(json.dumps({"Error Message": "Legacy Endpoint"}).encode())
        with pytest.raises(ContractViolation, match="object, not rows"):
            source.daily_bars("AAPL", start=date(2020, 1, 1), end=date(2020, 2, 1), run_id=RUN_ID)
