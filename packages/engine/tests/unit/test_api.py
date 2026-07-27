"""The HTTP surface, exercised against a real warehouse rather than mocks.

The fixture is the Apple FY2008 restatement, so the acceptance test for the whole
system is also the acceptance test for the API: the same request at two knowledge
dates must return two different numbers.

No route is mocked. A mocked API test proves the mock matches the test's idea of
the database, which is the thing least likely to be wrong.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from aletheia.api import app as api
from aletheia.store.db import Warehouse
from tests._factories import (
    FIRST_REPORT_FILED,
    FY2008_END,
    RESTATEMENT_FILED,
    first_report,
    make_entity,
    make_filing,
    make_identifier,
    restatement,
)


@pytest.fixture
def client(warehouse: Warehouse) -> Iterator[TestClient]:
    warehouse.write_entity(make_entity())
    warehouse.write_identifiers([make_identifier()])
    warehouse.write_filings(
        [
            make_filing(accn="0001193125-09-214859", filed_at=FIRST_REPORT_FILED),
            make_filing(accn="0001193125-10-012091", filed_at=RESTATEMENT_FILED, form="10-K/A"),
            make_filing(
                accn="0001193125-10-000001",
                filed_at=date(2010, 1, 25),
                form="8-K",
                items=("4.02",),
                period_of_report=None,
            ),
        ]
    )
    warehouse.write_facts([first_report(), restatement()])

    # Override the dependency rather than the module-level handle, and construct
    # the client WITHOUT entering it as a context manager: entering runs the
    # lifespan, which would open the real on-disk warehouse from settings. Tests
    # must not touch it -- it may be mid-ingest, and DuckDB allows one writer.
    api.app.dependency_overrides[api.get_warehouse] = lambda: warehouse
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.clear()


class TestTheCoreGuaranteeOverHttp:
    def test_the_same_request_at_two_dates_returns_two_numbers(self, client: TestClient) -> None:
        """The acceptance test, through the API."""
        early = client.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": "2009-12-01",
                "concept": "EarningsPerShareDiluted",
                "period_end": FY2008_END.isoformat(),
            },
        ).json()
        late = client.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": "2010-06-01",
                "concept": "EarningsPerShareDiluted",
                "period_end": FY2008_END.isoformat(),
            },
        ).json()
        assert early["as_known"]["value"] == "5.36"
        assert late["as_known"]["value"] == "5.36", (
            "first_reported must stay first-reported regardless of the knowledge date"
        )
        assert early["as_it_stands_today"]["value"] == "6.78"
        assert early["is_restated"] is True

    def test_the_drift_between_the_two_is_reported(self, client: TestClient) -> None:
        payload = client.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": "2009-12-01",
                "concept": "EarningsPerShareDiluted",
                "period_end": FY2008_END.isoformat(),
            },
        ).json()
        assert payload["relative_drift"] == pytest.approx((6.78 - 5.36) / 5.36)

    def test_money_crosses_the_wire_as_a_string(self, client: TestClient) -> None:
        """A JSON number is an IEEE double in every browser that parses it."""
        payload = client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2010-06-01", "period_end": FY2008_END.isoformat()},
        ).json()
        assert isinstance(payload["as_known"]["value"], str)

    def test_every_figure_carries_its_accession(self, client: TestClient) -> None:
        payload = client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2009-12-01", "period_end": FY2008_END.isoformat()},
        ).json()
        assert payload["as_known"]["accn"] == "0001193125-09-214859"
        assert payload["as_it_stands_today"]["accn"] == "0001193125-10-012091"
        assert payload["as_known"]["source_uri"].startswith("https://data.sec.gov/")

    def test_a_date_before_publication_is_a_404_not_a_blank(self, client: TestClient) -> None:
        """Absence must be loud over HTTP too."""
        response = client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2009-01-01", "period_end": FY2008_END.isoformat()},
        )
        assert response.status_code == 404
        assert "no first report" in response.json()["detail"]


class TestLookups:
    def test_an_unknown_ticker_is_a_404(self, client: TestClient) -> None:
        response = client.get("/api/asof/NOTREAL", params={"knowledge_date": "2010-06-01"})
        assert response.status_code == 404

    def test_search_finds_by_ticker_prefix(self, client: TestClient) -> None:
        results = client.get("/api/search", params={"q": "AAP"}).json()["results"]
        assert results and results[0]["ticker"] == "AAPL"

    def test_search_finds_by_company_name(self, client: TestClient) -> None:
        results = client.get("/api/search", params={"q": "APPLE"}).json()["results"]
        assert any(item["cik"] == 320193 for item in results)

    def test_an_empty_query_is_rejected_rather_than_scanning_everything(
        self, client: TestClient
    ) -> None:
        assert client.get("/api/search", params={"q": ""}).status_code == 422


class TestRevisions:
    def test_the_restatement_appears_with_both_values(self, client: TestClient) -> None:
        payload = client.get("/api/revisions/AAPL").json()
        assert payload["n_revisions"] == 1
        revision = payload["revisions"][0]
        assert revision["prior_value"] == "5.36"
        assert revision["new_value"] == "6.78"
        assert revision["days_to_revision"] == 90
        assert revision["relative_change"] == pytest.approx(0.2649, abs=1e-4)

    def test_a_high_threshold_filters_it_out(self, client: TestClient) -> None:
        """Control: the endpoint is actually applying the threshold."""
        payload = client.get("/api/revisions/AAPL", params={"min_change": 0.5}).json()
        assert payload["n_revisions"] == 0


class TestFeed:
    def test_a_non_reliance_filing_is_ranked_and_explained(self, client: TestClient) -> None:
        payload = client.get("/api/feed", params={"day": "2010-01-25"}).json()
        assert payload["n_flagged"] >= 1
        top = payload["items"][0]
        assert top["confidence"] == "CONFESSED"
        assert any("4.02" in finding["evidence"] for finding in top["findings"])

    def test_a_day_with_no_filings_returns_an_empty_feed_not_an_error(
        self, client: TestClient
    ) -> None:
        """Weekends exist. An empty answer is an answer."""
        response = client.get("/api/feed", params={"day": "2010-03-14"})
        assert response.status_code == 200
        assert response.json()["items"] == []


class TestQuality:
    def test_row_counts_and_revision_coverage_are_reported(self, client: TestClient) -> None:
        payload = client.get("/api/quality").json()
        assert payload["row_counts"]["facts"] == 2
        assert payload["row_counts"]["entities"] == 1
        assert payload["revision_coverage"]["distinct_periods"] == 1
        assert payload["revision_coverage"]["periods_with_a_changed_value"] == 1

    def test_health_reports_the_data_vintage(self, client: TestClient) -> None:
        payload = client.get("/api/health").json()
        assert payload["status"] == "ok"
        assert payload["data_vintage"] == RESTATEMENT_FILED.isoformat()


class TestReadOnly:
    def test_no_route_accepts_a_write_method(self, client: TestClient) -> None:
        """Ingest has a human behind it. A web request must not be able to start one."""
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)("/api/quality")
            assert response.status_code == 405, f"{method.upper()} /api/quality was allowed"

    def test_a_fact_cannot_be_written_through_the_asof_route(
        self, client: TestClient, warehouse: Warehouse
    ) -> None:
        before = warehouse.execute("SELECT count(*) FROM facts").fetchone()
        client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2010-06-01", "period_end": FY2008_END.isoformat()},
        )
        after = warehouse.execute("SELECT count(*) FROM facts").fetchone()
        assert before == after
