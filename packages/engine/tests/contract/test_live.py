"""Live contract tests. ``pytest -m live``.

These hit the real APIs. They are excluded from the default suite because they
cost quota and depend on someone else's uptime — but they are the only tests
that can detect the failure that matters most: an upstream contract changing
underneath a parser that still passes every offline test.

Run them before trusting a fresh ingest, and after any upstream incident.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aletheia.core.clock import FrozenClock
from aletheia.core.config import load_settings
from aletheia.core.errors import AletheiaError
from aletheia.core.types import Cik
from aletheia.provenance.payloads import PayloadStore
from aletheia.sources.edgar import EdgarClient
from aletheia.sources.fred import FredClient
from aletheia.sources.http import Fetcher
from aletheia.sources.prices import DelistedCoverageError, FmpPriceSource

pytestmark = pytest.mark.live

RUN_ID = "run-live-0001"
AAPL = Cik(320193)


@pytest.fixture
def fetcher(tmp_path: Path) -> Fetcher:
    settings = load_settings(data_dir=tmp_path)
    contact = os.environ.get("ALETHEIA_CONTACT_EMAIL", "")
    if not contact and "@" not in settings.sec_user_agent:
        pytest.skip("set ALETHEIA_CONTACT_EMAIL — the SEC requires a real contact address")
    with Fetcher(
        payloads=PayloadStore(tmp_path / "raw"),
        clock=FrozenClock(datetime(2026, 7, 27, tzinfo=UTC)),
        user_agent=settings.sec_user_agent,
        rate_per_second=settings.sec_rate_limit_per_sec,
    ) as client:
        yield client


class TestEdgarLive:
    def test_the_aapl_restatement_is_still_there(self, fetcher: Fetcher) -> None:
        """The anchor fact of this entire system, verified against the live API.

        Apple's FY2008 diluted EPS was published at 5.36 and restated to 6.78.
        If EDGAR ever stops returning both, every claim this system makes about
        point-in-time correctness needs re-examining.
        """
        facts, report = EdgarClient(fetcher).company_concept(
            AAPL, taxonomy="us-gaap", concept="EarningsPerShareDiluted", run_id=RUN_ID
        )
        fy2008 = sorted(
            (
                f
                for f in facts
                if f.period_start == date(2007, 9, 30) and f.period_end == date(2008, 9, 27)
            ),
            key=lambda f: f.filed_at,
        )
        assert len(fy2008) >= 2, "expected the original and at least one restatement"
        assert fy2008[0].value == Decimal("5.36")
        assert fy2008[0].filed_at == date(2009, 10, 27)
        assert fy2008[0].accn.value == "0001193125-09-214859"
        assert fy2008[1].value == Decimal("6.78")
        assert fy2008[1].filed_at == date(2010, 1, 25)
        assert report.total_skipped == 0

    def test_submissions_still_uses_parallel_arrays(self, fetcher: Fetcher) -> None:
        entity, report = EdgarClient(fetcher).submissions(AAPL, run_id=RUN_ID)
        assert entity.name.startswith("Apple")
        assert "AAPL" in entity.tickers
        assert report.parsed > 1000, "expected the recent block plus older chunks"
        assert any(f.form == "10-K" for f in entity.filings)

    def test_the_ticker_file_still_maps_thousands_of_filers(self, fetcher: Fetcher) -> None:
        mappings, _ = EdgarClient(fetcher).company_tickers(observed_at=date(2026, 7, 27))
        assert len(mappings) > 5000
        assert any(m.ticker == "AAPL" and m.cik == AAPL for m in mappings)

    def test_the_daily_index_is_parseable(self, fetcher: Fetcher) -> None:
        """Walks back to the most recent weekday with a published index."""
        client = EdgarClient(fetcher)
        for offset in range(1, 8):
            day = date(2026, 7, 27) - timedelta(days=offset)
            try:
                entries, report = client.daily_index(day, run_id=RUN_ID)
            except AletheiaError:
                continue  # weekends and holidays have no index
            assert report.parsed > 100
            # The daily index is a DISSEMINATION feed, not a filing-date feed:
            # some entries carry earlier filing dates (verified 2026-07-24 —
            # 123 of 4,005, the oldest by eleven months). The knowledge date is
            # therefore max(filed_at, disseminated_at); see migration 002.
            assert all(entry.filed_at <= day for entry in entries)
            assert sum(1 for e in entries if e.filed_at == day) > len(entries) * 0.8
            return
        pytest.fail("no daily index found in the previous seven days")


class TestFredLive:
    def test_gdp_still_has_many_vintages(self, fetcher: Fetcher) -> None:
        """2020Q2 real GDP has been published eight times. That is the point of ALFRED."""
        settings = load_settings()
        if settings.fred_api_key is None:
            pytest.skip("FRED_API_KEY not configured")
        records, _ = FredClient(fetcher, api_key=settings.fred_api_key).all_vintages(
            "GDPC1",
            run_id=RUN_ID,
            observation_start=date(2020, 4, 1),
            observation_end=date(2020, 4, 1),
        )
        assert len(records) >= 8
        values = [r.value for r in records if r.value is not None]
        assert len(set(values)) == len(values), "each vintage should carry a distinct value"
        assert records[0].realtime_start < records[-1].realtime_start


class TestPricesLive:
    def test_a_listed_name_returns_bars(self, fetcher: Fetcher) -> None:
        settings = load_settings()
        if settings.fmp_api_key is None:
            pytest.skip("FMP_API_KEY not configured")
        bars, report = FmpPriceSource(fetcher, api_key=settings.fmp_api_key).daily_bars(
            "AAPL", start=date(2024, 1, 2), end=date(2024, 1, 10), run_id=RUN_ID
        )
        assert report.parsed > 3
        assert all(bar.close > 0 for bar in bars)

    def test_a_delisted_name_is_still_out_of_entitlement(self, fetcher: Fetcher) -> None:
        """The survivorship limitation, asserted rather than assumed.

        If this test ever starts failing, the entitlement changed and the
        survivorship caveat on every evidence card can be revisited.
        """
        settings = load_settings()
        if settings.fmp_api_key is None:
            pytest.skip("FMP_API_KEY not configured")
        source = FmpPriceSource(fetcher, api_key=settings.fmp_api_key)
        with pytest.raises(DelistedCoverageError):
            source.daily_bars("SIVB", start=date(2023, 1, 1), end=date(2023, 3, 1), run_id=RUN_ID)
