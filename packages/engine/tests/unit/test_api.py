"""The HTTP surface, exercised against a real warehouse rather than mocks.

The fixture is the Apple FY2008 restatement, so the acceptance test for the whole
system is also the acceptance test for the API: the same request at two knowledge
dates must return two different numbers.

No route is mocked. A mocked API test proves the mock matches the test's idea of
the database, which is the thing least likely to be wrong.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from aletheia.api import app as api
from aletheia.core.types import Fact
from aletheia.store.db import Warehouse
from tests._factories import (
    FIRST_REPORT_ACCN,
    FIRST_REPORT_FILED,
    FY2008_END,
    RESTATEMENT_FILED,
    first_report,
    make_entity,
    make_fact,
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
        assert late["as_known"]["value"] == "6.78", (
            "what was knowable changes when the restatement is published; pinning "
            "this to the first report would return 5.36 on both dates and remove "
            "the entire demonstration"
        )
        assert early["as_it_stands_today"]["value"] == "6.78"
        assert early["is_restated"] is True

    def test_the_first_report_is_returned_alongside_and_does_not_move(
        self, client: TestClient
    ) -> None:
        """Both semantics are served, because research wants the other one.

        `as_known` answers "what would I have seen on this date". `as_first_reported`
        answers "what did the market originally react to" -- which is the right
        input for an event study and does not change with the viewing date.
        """
        for day in ("2009-12-01", "2010-06-01"):
            payload = client.get(
                "/api/asof/AAPL",
                params={"knowledge_date": day, "period_end": FY2008_END.isoformat()},
            ).json()
            assert payload["as_first_reported"]["value"] == "5.36"

    def test_the_viewer_says_whether_the_restatement_had_already_landed(
        self, client: TestClient
    ) -> None:
        early = client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2009-12-01", "period_end": FY2008_END.isoformat()},
        ).json()
        late = client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2010-06-01", "period_end": FY2008_END.isoformat()},
        ).json()
        assert early["already_restated_by_then"] is False
        assert late["already_restated_by_then"] is True

    def test_the_drift_is_measured_from_the_first_report(self, client: TestClient) -> None:
        """And therefore does not collapse to zero once the restatement is public.

        Measuring drift against whatever was current on the knowledge date reports
        +0.00% for every date after the restatement landed, while the text on the
        same card still describes the move from the original figure.
        """
        for day in ("2009-12-01", "2010-06-01"):
            payload = client.get(
                "/api/asof/AAPL",
                params={
                    "knowledge_date": day,
                    "concept": "EarningsPerShareDiluted",
                    "period_end": FY2008_END.isoformat(),
                },
            ).json()
            assert payload["relative_drift"] == pytest.approx((6.78 - 5.36) / 5.36), day

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
        assert "had not been published" in response.json()["detail"]


class TestTheFutureCannotUnanswerAPastQuestion:
    """A filing made later must not turn a 200 into a 400.

    ``/api/asof`` reads the restated figure from the FULL data vintage, by design
    -- the gap between the first report and today's value is the number the page
    exists to show. That read therefore sees filings made after the knowledge date
    being asked about, and an end date that named one period then can name two now.

    Real case behind this: AAR Corp (CIK 1750) ProfitLoss ending 2018-11-30 was a
    single 182-day period on 2018-12-19; the 2019-03-20 10-Q added a 90-day period
    ending the same day. Unpinned, the endpoint answered the knowledge-date part
    fine and then raised on the restated part, so the request 400'd on account of
    data from its own future -- in the system whose one claim is that this cannot
    happen.
    """

    SHARED_END = date(2018, 11, 30)
    KNOWN_ON = "2018-12-19"

    @pytest.fixture
    def with_a_later_second_period(self, client: TestClient, warehouse: Warehouse) -> TestClient:
        warehouse.write_facts(
            [
                make_fact(
                    value="22100000",
                    filed_at=date(2018, 12, 19),
                    accn="0001104659-18-073842",
                    concept="ProfitLoss",
                    unit="USD",
                    period_start=date(2018, 6, 1),
                    period_end=self.SHARED_END,
                )
            ]
        )
        warehouse.write_facts(
            [
                make_fact(
                    value="7000000",
                    filed_at=date(2019, 3, 20),
                    accn="0001104659-19-016320",
                    concept="ProfitLoss",
                    unit="USD",
                    period_start=date(2018, 9, 1),
                    period_end=self.SHARED_END,
                )
            ]
        )
        return client

    def test_the_request_still_answers(self, with_a_later_second_period: TestClient) -> None:
        response = with_a_later_second_period.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": self.KNOWN_ON,
                "concept": "ProfitLoss",
                "period_end": self.SHARED_END.isoformat(),
            },
        )
        assert response.status_code == 200, response.json()
        payload = response.json()
        assert payload["as_known"]["value"] == "22100000"
        # Every figure on the card describes the same period, not three periods
        # that happen to share an end date.
        assert payload["as_it_stands_today"]["period_start"] == "2018-06-01"
        assert payload["as_first_reported"]["period_start"] == "2018-06-01"


class TestNamingAPeriodOverHttp:
    """The 400 tells the caller to pass ``period_start``; it has to be sendable.

    Including for a period that has no start date. The ambiguity is often between
    a balance-sheet instant and a duration sharing its end date, and a date-typed
    parameter can name only one of the two candidates the error just listed.
    """

    SHARED_END = date(2015, 9, 26)

    @pytest.fixture
    def instant_and_duration(self, client: TestClient, warehouse: Warehouse) -> TestClient:
        for start, value in ((None, "1400000"), (date(2015, 6, 28), "900000")):
            warehouse.write_facts(
                [
                    make_fact(
                        value=value,
                        filed_at=date(2015, 10, 28),
                        accn="0001193125-15-356351",
                        concept="AntidilutiveSecurities",
                        unit="shares",
                        period_start=start,
                        period_end=self.SHARED_END,
                    )
                ]
            )
        return client

    def _ask(self, client: TestClient, **extra: str) -> object:
        return client.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": "2016-01-01",
                "concept": "AntidilutiveSecurities",
                "period_end": self.SHARED_END.isoformat(),
                **extra,
            },
        )

    def test_the_collision_is_a_400_naming_both_candidates(
        self, instant_and_duration: TestClient
    ) -> None:
        response = self._ask(instant_and_duration)
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "instant" in detail
        assert "2015-06-28..2015-09-26" in detail

    def test_the_literal_instant_resolves_it(self, instant_and_duration: TestClient) -> None:
        response = self._ask(instant_and_duration, period_start="instant")
        assert response.status_code == 200, response.json()
        payload = response.json()
        assert payload["as_known"]["value"] == "1400000"
        assert payload["as_known"]["period_start"] is None

    def test_a_date_resolves_it_to_the_duration(self, instant_and_duration: TestClient) -> None:
        """Control: 'instant' is not the only branch that works."""
        response = self._ask(instant_and_duration, period_start="2015-06-28")
        assert response.status_code == 200, response.json()
        assert response.json()["as_known"]["value"] == "900000"

    def test_a_malformed_period_start_says_what_is_accepted(
        self, instant_and_duration: TestClient
    ) -> None:
        response = self._ask(instant_and_duration, period_start="last quarter")
        assert response.status_code == 400
        assert "YYYY-MM-DD or the literal 'instant'" in response.json()["detail"]


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


class TestConcurrentRequests:
    """A DuckDB connection carries one result set; a browser makes several requests.

    Two threads sharing a connection interleave -- thread A executes, thread B
    executes, and A's fetch returns B's row. Loading two pages at once was enough
    to make /api/quality read an accession number where it expected a row count
    and return a 500. Every existing test was sequential, so none of them could
    have caught it.
    """

    def test_parallel_requests_do_not_return_each_others_rows(self, client: TestClient) -> None:
        import concurrent.futures

        def fetch(path: str) -> tuple[int, object]:
            response = client.get(path)
            return response.status_code, response.json()

        paths = ["/api/quality", "/api/feed?day=2010-01-25", "/api/revisions/AAPL"] * 8
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(fetch, paths))

        assert all(status == 200 for status, _ in results), [
            (path, status)
            for path, (status, _) in zip(paths, results, strict=True)
            if status != 200
        ]

    def test_row_counts_stay_correct_under_load(self, client: TestClient) -> None:
        """Not just 200s -- the same query must keep returning the same answer."""
        import concurrent.futures

        def counts() -> dict[str, int]:
            payload = client.get("/api/quality").json()
            return payload["row_counts"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            observed = list(pool.map(lambda _: counts(), range(16)))

        assert all(item == observed[0] for item in observed)
        assert observed[0]["facts"] == 2


class TestRevisionCoverageCountsPeriodsNotEndDates:
    """A fiscal year and its fourth quarter share a period_end.

    They are different periods reporting different numbers. Grouping revision
    coverage without period_start reads that as a value that changed after
    publication. On the real warehouse the mistake manufactured 667,003 spurious
    revisions and turned a true 5.0% into a published 16.4% — which I had already
    written into the README before re-deriving it.
    """

    @pytest.fixture
    def year_and_quarter(self, client: TestClient, warehouse: Warehouse) -> TestClient:
        """One concept, one end date, two genuinely different periods."""
        warehouse.write_facts(
            [
                make_fact(
                    value="10000",
                    filed_at=date(2016, 2, 1),
                    accn="0000320193-16-000001",
                    concept="Revenues",
                    unit="USD",
                    period_start=date(2015, 1, 1),  # full year
                    period_end=date(2015, 12, 31),
                ),
                make_fact(
                    value="2500",
                    filed_at=date(2016, 2, 1),
                    accn="0000320193-16-000001",
                    concept="Revenues",
                    unit="USD",
                    period_start=date(2015, 10, 1),  # Q4, same end date
                    period_end=date(2015, 12, 31),
                ),
            ]
        )
        return client

    def test_a_year_and_its_quarter_are_not_a_revision(self, year_and_quarter: TestClient) -> None:
        payload = year_and_quarter.get("/api/quality").json()["revision_coverage"]
        # The AAPL fixture contributes exactly one genuine revision (5.36 -> 6.78).
        assert payload["periods_with_a_changed_value"] == 1, (
            "the year/quarter pair sharing an end date must not count as a revision"
        )

    def test_both_periods_are_still_counted_as_periods(self, year_and_quarter: TestClient) -> None:
        """Control: they are two distinct periods, so the denominator sees both."""
        payload = year_and_quarter.get("/api/quality").json()["revision_coverage"]
        assert payload["distinct_periods"] == 3  # AAPL FY2008 + the year + the quarter

    def test_a_genuine_restatement_of_one_period_still_counts(
        self, year_and_quarter: TestClient, warehouse: Warehouse
    ) -> None:
        """The other side: the guard must not suppress real revisions."""
        warehouse.write_facts(
            [
                make_fact(
                    value="11000",
                    filed_at=date(2017, 2, 1),
                    accn="0000320193-17-000001",
                    concept="Revenues",
                    unit="USD",
                    period_start=date(2015, 1, 1),
                    period_end=date(2015, 12, 31),
                )
            ]
        )
        payload = year_and_quarter.get("/api/quality").json()["revision_coverage"]
        assert payload["periods_with_a_changed_value"] == 2


class TestARefilingIsNotAutomaticallyARestatement:
    """Whether a later filing supersedes the earlier one, and whether the number
    moved, are two questions. Over the whole warehouse they have very different
    answers: of 3,956,913 (first report, later report) pairs, 3,586,802 -- 90.6%
    -- carry the value forward unchanged. Answering them as one would put a
    restatement warning on nine periods out of ten that never had one.

    The fixture is real. AAR Corp (CIK 1750) reported profit of $22.1M for the
    six months ending 2018-11-30 in the 10-Q filed 2018-12-19, and the 10-Q filed
    2019-12-20 carried the identical figure forward under a new accession as the
    prior-year comparative column. Nothing was restated; the number simply has a
    second source document now.
    """

    AIR_CIK = 1750
    HALF_YEAR_START = date(2018, 6, 1)
    HALF_YEAR_END = date(2018, 11, 30)
    ORIGINAL_ACCN = "0001104659-18-073842"
    ORIGINAL_FILED = date(2018, 12, 19)
    REPRESENTED_ACCN = "0001104659-19-074491"
    REPRESENTED_FILED = date(2019, 12, 20)
    PROFIT = "22100000"

    @pytest.fixture
    def air(self, client: TestClient, warehouse: Warehouse) -> TestClient:
        warehouse.write_entity(make_entity(cik=self.AIR_CIK, name="AAR CORP", sic="3720"))
        warehouse.write_identifiers([make_identifier(cik=self.AIR_CIK, ticker="AIR")])
        warehouse.write_filings(
            [
                make_filing(
                    accn=self.ORIGINAL_ACCN,
                    filed_at=self.ORIGINAL_FILED,
                    form="10-Q",
                    cik=self.AIR_CIK,
                    period_of_report=self.HALF_YEAR_END,
                ),
                make_filing(
                    accn=self.REPRESENTED_ACCN,
                    filed_at=self.REPRESENTED_FILED,
                    form="10-Q",
                    cik=self.AIR_CIK,
                    period_of_report=date(2019, 11, 30),
                ),
            ]
        )
        warehouse.write_facts(
            [
                self._profit(accn=self.ORIGINAL_ACCN, filed_at=self.ORIGINAL_FILED),
                self._profit(accn=self.REPRESENTED_ACCN, filed_at=self.REPRESENTED_FILED),
            ]
        )
        return client

    def _profit(self, *, accn: str, filed_at: date) -> Fact:
        return make_fact(
            value=self.PROFIT,
            filed_at=filed_at,
            accn=accn,
            concept="ProfitLoss",
            unit="USD",
            cik=self.AIR_CIK,
            form="10-Q",
            period_start=self.HALF_YEAR_START,
            period_end=self.HALF_YEAR_END,
        )

    def _ask(self, air: TestClient, day: str) -> dict[str, object]:
        response = air.get(
            "/api/asof/AIR",
            params={
                "knowledge_date": day,
                "concept": "ProfitLoss",
                "period_end": self.HALF_YEAR_END.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def test_the_refiling_is_reported_as_a_refiling(self, air: TestClient) -> None:
        payload = self._ask(air, "2018-12-31")
        assert payload["is_restated"] is True, (
            "a later accession does supersede the one a reader would have had, and "
            "the viewer still has to say so -- a vendor panel cites the later one"
        )

    def test_but_the_value_did_not_change(self, air: TestClient) -> None:
        """The discriminator. This is what earns the restatement warning."""
        payload = self._ask(air, "2018-12-31")
        assert payload["value_changed"] is False
        assert payload["as_first_reported"]["value"] == self.PROFIT
        assert payload["as_it_stands_today"]["value"] == self.PROFIT

    def test_a_re_presentation_never_reads_as_already_restated(self, air: TestClient) -> None:
        """Including *after* the second filing has landed, which is the whole point.

        Accession-based, this flag fired on every period whose next annual report
        had been filed -- nearly all of them, a year on -- and the card then told
        the reader a restatement had already been published on a figure that never
        moved.
        """
        for day in ("2018-12-31", "2020-06-01"):
            payload = self._ask(air, day)
            assert payload["already_restated_by_then"] is False, day

    def test_the_genuine_restatement_still_reads_as_one(self, client: TestClient) -> None:
        """Two-sided: the Apple fixture must keep both flags true."""
        payload = client.get(
            "/api/asof/AAPL",
            params={"knowledge_date": "2010-06-01", "period_end": FY2008_END.isoformat()},
        ).json()
        assert payload["value_changed"] is True
        assert payload["already_restated_by_then"] is True


class TestAZeroFirstReportStillAnswersTheQuestion:
    """`relative_drift` is None whenever the first report was 0 -- there is no
    denominator -- so a card that decides "did this change" from the drift is
    blind on exactly those periods. It is not a rare corner: 123,177 refilings in
    the warehouse are 0 -> 0, and 2,924 first reports of 0 later moved to a
    non-zero figure. Arconic first reported $0 of discontinued-operations income
    for FY2018 on 2019-02-21 and restated it to $333M on 2021-02-16.

    `value_changed` compares the values, so it answers on both.
    """

    CONCEPT = "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"
    LATER_ACCN = "0001193125-10-012091"

    @pytest.fixture
    def zeroed(self, client: TestClient, warehouse: Warehouse) -> TestClient:
        """First report of 0, refiled unchanged. The blind spot in its pure form."""
        warehouse.write_facts(
            [
                make_fact(
                    value="0",
                    filed_at=FIRST_REPORT_FILED,
                    accn=FIRST_REPORT_ACCN,
                    concept=self.CONCEPT,
                    unit="USD",
                ),
                make_fact(
                    value="0",
                    filed_at=RESTATEMENT_FILED,
                    accn=self.LATER_ACCN,
                    concept=self.CONCEPT,
                    unit="USD",
                ),
            ]
        )
        return client

    def _ask(self, client: TestClient, day: str) -> dict[str, object]:
        response = client.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": day,
                "concept": self.CONCEPT,
                "period_end": FY2008_END.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def test_the_drift_is_undefined(self, zeroed: TestClient) -> None:
        """Stated first, because it is the premise of the next two tests."""
        for day in ("2009-12-01", "2010-06-01"):
            assert self._ask(zeroed, day)["relative_drift"] is None, day

    def test_and_the_value_is_still_known_not_to_have_changed(self, zeroed: TestClient) -> None:
        """Asked before the second filing, so a later document does supersede the
        one a reader had -- which is precisely when the card decides whether to
        show a restatement warning."""
        payload = self._ask(zeroed, "2009-12-01")
        assert payload["is_restated"] is True
        assert payload["value_changed"] is False, (
            "0 -> 0 under a new accession is a re-presentation; deciding from a "
            "null drift reports it as a restatement"
        )

    def test_and_it_never_reads_as_already_restated(self, zeroed: TestClient) -> None:
        for day in ("2009-12-01", "2010-06-01"):
            assert self._ask(zeroed, day)["already_restated_by_then"] is False, day

    def test_a_real_move_off_zero_is_reported_as_one(
        self, client: TestClient, warehouse: Warehouse
    ) -> None:
        """The other side, and the reason drift alone cannot be the discriminator:
        both cases carry a null drift and they are opposites."""
        warehouse.write_facts(
            [
                make_fact(
                    value="0",
                    filed_at=FIRST_REPORT_FILED,
                    accn=FIRST_REPORT_ACCN,
                    concept=self.CONCEPT,
                    unit="USD",
                ),
                make_fact(
                    value="333000000",
                    filed_at=RESTATEMENT_FILED,
                    accn=self.LATER_ACCN,
                    concept=self.CONCEPT,
                    unit="USD",
                ),
            ]
        )
        before = self._ask(client, "2009-12-01")
        assert before["relative_drift"] is None
        assert before["is_restated"] is True
        assert before["value_changed"] is True

        after = self._ask(client, "2010-06-01")
        assert after["relative_drift"] is None
        assert after["value_changed"] is True
        assert after["already_restated_by_then"] is True


class TestAPeriodRevisedMoreThanOnce:
    """A period is not restated once and then finished.

    17,296 of the warehouse's 357,101 revised periods -- 4.8% -- carry three or
    more distinct values. On those, "the value had already been revised by this
    date" does not say *which* revision the reader is looking at, and the card
    answered as though there were only ever two: the original, or the current
    one. Between two revisions it showed a third number and called it the
    current one.

    The fixture is real, and it is the same restatement the README opens with.
    Apple's other current assets at 2009-09-26 were first reported as $6.884bn
    in the FY2009 10-K, cut to $3.140bn by the 10-K/A of 2010-01-25 -- the same
    amended filing that moved FY2008 diluted EPS from 5.36 to 6.78 -- and cut
    again to $1.444bn in the 10-Q filed 2010-07-21. Six filings, three distinct
    values, and the last two repeat $1.444bn under different accessions.

    That trailing repeat is why `known_is_current` compares values rather than
    accession numbers. Asked on 2010-08-01 a reader holds $1.444bn and the
    figure standing today is $1.444bn: the two columns agree, and the card must
    not announce an intermediate figure. Accession-based the flag reads False
    there, because a later document exists -- which is the D11 error committed a
    second time, and 90.6% of refilings are exactly this shape.
    """

    AAPL_CIK = 320193
    # An instant. No period_start: a balance-sheet item is measured at a moment.
    PERIOD_END = date(2009, 9, 26)
    CONCEPT = "OtherAssetsCurrent"
    ORIGINAL = "6884000000"
    INTERMEDIATE = "3140000000"
    CURRENT = "1444000000"
    # (value, accn, filed, form) in publication order, verbatim from the warehouse.
    CHAIN: ClassVar[list[tuple[str, str, date, str]]] = [
        (ORIGINAL, "0001193125-09-214859", date(2009, 10, 27), "10-K"),
        (INTERMEDIATE, "0001193125-10-012085", date(2010, 1, 25), "10-Q"),
        (INTERMEDIATE, "0001193125-10-012091", date(2010, 1, 25), "10-K/A"),
        (INTERMEDIATE, "0001193125-10-088957", date(2010, 4, 21), "10-Q"),
        (CURRENT, "0001193125-10-162840", date(2010, 7, 21), "10-Q"),
        (CURRENT, "0001193125-10-238044", date(2010, 10, 27), "10-K"),
    ]

    @pytest.fixture
    def assets(self, warehouse: Warehouse) -> Iterator[TestClient]:
        """Its own warehouse, not the shared `client` one.

        Two of these accessions are the module-level fixture's, and layering a
        second set of filings on top of them would be writing the same primary
        key twice to make a point about something else.
        """
        warehouse.write_entity(make_entity())
        warehouse.write_identifiers([make_identifier()])
        warehouse.write_filings(
            [
                make_filing(
                    accn=accn,
                    filed_at=filed,
                    form=form,
                    cik=self.AAPL_CIK,
                    period_of_report=self.PERIOD_END,
                )
                for _, accn, filed, form in self.CHAIN
            ]
        )
        warehouse.write_facts(
            [
                make_fact(
                    value=value,
                    filed_at=filed,
                    accn=accn,
                    concept=self.CONCEPT,
                    unit="USD",
                    cik=self.AAPL_CIK,
                    form=form,
                    period_start=None,
                    period_end=self.PERIOD_END,
                )
                for value, accn, filed, form in self.CHAIN
            ]
        )
        api.app.dependency_overrides[api.get_warehouse] = lambda: warehouse
        try:
            yield TestClient(api.app)
        finally:
            api.app.dependency_overrides.clear()

    def _ask(self, assets: TestClient, day: str) -> dict[str, object]:
        response = assets.get(
            "/api/asof/AAPL",
            params={
                "knowledge_date": day,
                "concept": self.CONCEPT,
                "period_end": self.PERIOD_END.isoformat(),
                "period_start": "instant",
            },
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def _values(self, payload: dict[str, object]) -> tuple[str, str, str]:
        def value(key: str) -> str:
            fact: dict[str, object] = payload[key]  # type: ignore[assignment]
            return str(fact["value"])

        return value("as_first_reported"), value("as_known"), value("as_it_stands_today")

    def test_before_any_revision_the_reader_holds_the_original(self, assets: TestClient) -> None:
        payload = self._ask(assets, "2009-12-01")
        assert self._values(payload) == (self.ORIGINAL, self.ORIGINAL, self.CURRENT)
        assert payload["already_restated_by_then"] is False
        assert payload["known_is_current"] is False

    def test_between_two_revisions_the_reader_holds_neither_end_of_the_chain(
        self, assets: TestClient
    ) -> None:
        """The state the card had no way to describe.

        Three different numbers are on screen and the old copy said two of them
        were the same one.
        """
        payload = self._ask(assets, "2010-04-01")
        assert self._values(payload) == (self.ORIGINAL, self.INTERMEDIATE, self.CURRENT)
        assert payload["already_restated_by_then"] is True
        assert payload["known_is_current"] is False

    def test_after_the_last_revision_the_reader_holds_the_current_figure(
        self, assets: TestClient
    ) -> None:
        payload = self._ask(assets, "2010-08-01")
        assert self._values(payload) == (self.ORIGINAL, self.CURRENT, self.CURRENT)
        assert payload["already_restated_by_then"] is True
        assert payload["known_is_current"] is True

    def test_and_a_later_re_presentation_does_not_make_it_stale(self, assets: TestClient) -> None:
        """The discriminator between comparing values and comparing accessions.

        On 2010-08-01 the latest filing a reader had was the 10-Q of 2010-07-21,
        and a 10-K published 2010-10-27 repeats its figure unchanged. The
        accessions differ; the values do not. Compared by accession the card
        would tell a reader looking at $1.444bn -- beside a column also showing
        $1.444bn -- that they were holding an intermediate figure.
        """
        payload = self._ask(assets, "2010-08-01")
        known: dict[str, object] = payload["as_known"]  # type: ignore[assignment]
        current: dict[str, object] = payload["as_it_stands_today"]  # type: ignore[assignment]
        assert known["accn"] != current["accn"], "a later filing exists"
        assert known["value"] == current["value"], "and it changed nothing"
        assert payload["known_is_current"] is True


class TestAPeriodRevisedAndThenRevisedBack:
    """The chain returns to where it started, so its two ends say nothing moved.

    Every flag on this endpoint except this one compares two points: first
    against latest, or known against one of them. All of them are blind to a
    period that was revised and then revised back, because on those the two ends
    are the same number. 10,080 of the warehouse's 357,101 revised us-gaap
    periods are that shape -- 2.82%.

    The fixture is real. AAR Corp's accrued current liabilities at 2021-05-31
    were reported at $174.2m in the FY2021 10-K, cut to $148.3m in the 10-Q of
    2021-12-21, and put back to $174.2m in the FY2022 10-K. Five filings, two
    distinct values, identical endpoints -- and the page responded by printing
    "The value never moved; only its source document did" beside a left column
    reading 148300000 and a right column reading 174200000.
    """

    AAR_CIK = 1750
    # An instant. Accrued liabilities are a balance, measured at a moment.
    PERIOD_END = date(2021, 5, 31)
    CONCEPT = "AccruedLiabilitiesCurrent"
    ORIGINAL = "174200000"
    INTERIM = "148300000"
    # (value, accn, filed, form) in publication order, verbatim from the warehouse.
    CHAIN: ClassVar[list[tuple[str, str, date, str]]] = [
        (ORIGINAL, "0001104659-21-094125", date(2021, 7, 21), "10-K"),
        (ORIGINAL, "0001104659-21-118843", date(2021, 9, 23), "10-Q"),
        (INTERIM, "0001104659-21-152249", date(2021, 12, 21), "10-Q"),
        (INTERIM, "0001104659-22-036639", date(2022, 3, 22), "10-Q"),
        (ORIGINAL, "0001104659-22-081498", date(2022, 7, 21), "10-K"),
    ]

    # The control, riding the same filings. AAR tagged total equity for this
    # period in four of those five documents and never changed it: $974.4m every
    # time. Same company, same period, same accessions -- the only difference is
    # that this one is the ordinary case, and it has to keep reading as such.
    EQUITY = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    EQUITY_VALUE = "974400000"
    EQUITY_ACCNS: ClassVar[list[int]] = [0, 1, 2, 4]

    @pytest.fixture
    def liabilities(self, warehouse: Warehouse) -> Iterator[TestClient]:
        warehouse.write_entity(make_entity(cik=self.AAR_CIK, name="AAR CORP", sic="3720"))
        warehouse.write_identifiers([make_identifier(cik=self.AAR_CIK, ticker="AIR")])
        warehouse.write_filings(
            [
                make_filing(
                    accn=accn,
                    filed_at=filed,
                    form=form,
                    cik=self.AAR_CIK,
                    period_of_report=self.PERIOD_END,
                )
                for _, accn, filed, form in self.CHAIN
            ]
        )
        warehouse.write_facts(
            [
                make_fact(
                    value=value,
                    filed_at=filed,
                    accn=accn,
                    concept=self.CONCEPT,
                    unit="USD",
                    cik=self.AAR_CIK,
                    form=form,
                    period_start=None,
                    period_end=self.PERIOD_END,
                )
                for value, accn, filed, form in self.CHAIN
            ]
        )
        warehouse.write_facts(
            [
                make_fact(
                    value=self.EQUITY_VALUE,
                    filed_at=self.CHAIN[index][2],
                    accn=self.CHAIN[index][1],
                    concept=self.EQUITY,
                    unit="USD",
                    cik=self.AAR_CIK,
                    form=self.CHAIN[index][3],
                    period_start=None,
                    period_end=self.PERIOD_END,
                )
                for index in self.EQUITY_ACCNS
            ]
        )
        api.app.dependency_overrides[api.get_warehouse] = lambda: warehouse
        try:
            yield TestClient(api.app)
        finally:
            api.app.dependency_overrides.clear()

    def _ask(self, liabilities: TestClient, day: str) -> dict[str, object]:
        response = liabilities.get(
            "/api/asof/AIR",
            params={
                "knowledge_date": day,
                "concept": self.CONCEPT,
                "period_end": self.PERIOD_END.isoformat(),
                "period_start": "instant",
            },
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def test_the_endpoints_agree_and_every_two_point_flag_says_nothing_happened(
        self, liabilities: TestClient
    ) -> None:
        """Pinning the blind spot itself, so the fix cannot be mistaken for luck.

        These four assertions describe the *old* behaviour and are all still
        correct -- comparing first to latest genuinely does show no change here.
        The defect was never that they were wrong; it was that the page had
        nothing else to ask.
        """
        payload = self._ask(liabilities, "2026-06-01")
        first: dict[str, object] = payload["as_first_reported"]  # type: ignore[assignment]
        current: dict[str, object] = payload["as_it_stands_today"]  # type: ignore[assignment]
        assert first["value"] == self.ORIGINAL
        assert current["value"] == self.ORIGINAL
        assert first["accn"] != current["accn"], "five filings, not one"
        assert payload["value_changed"] is False
        assert payload["already_restated_by_then"] is False

    def test_and_the_chain_says_otherwise(self, liabilities: TestClient) -> None:
        payload = self._ask(liabilities, "2026-06-01")
        assert payload["value_ever_changed"] is True

    def test_between_the_two_filings_the_reader_holds_a_figure_that_no_longer_exists(
        self, liabilities: TestClient
    ) -> None:
        """The date at which the page contradicted itself on screen.

        Left column 148300000, right column 174200000, and a sentence between
        them saying the value never moved.
        """
        payload = self._ask(liabilities, "2022-01-01")
        known: dict[str, object] = payload["as_known"]  # type: ignore[assignment]
        assert known["value"] == self.INTERIM
        assert payload["value_ever_changed"] is True
        # Still false: today's figure really does equal the first-reported one.
        assert payload["value_changed"] is False
        # And the left column is neither the current figure nor the original.
        assert payload["already_restated_by_then"] is True
        assert payload["known_is_current"] is False

    def test_a_period_that_truly_never_moved_is_not_swept_up(self, liabilities: TestClient) -> None:
        """The discriminator. `value_ever_changed` must stay false on the common case.

        Without it, `value_ever_changed <- True` passes every test above, the
        "re-presented, not revised" and "never revised" branches become
        unreachable, and the page starts announcing a revision on the 90.6% of
        refilings that carry the figure forward untouched. Total equity for this
        very period, filed four times without moving, is that case.
        """
        response = liabilities.get(
            "/api/asof/AIR",
            params={
                "knowledge_date": "2026-06-01",
                "concept": self.EQUITY,
                "period_end": self.PERIOD_END.isoformat(),
                "period_start": "instant",
            },
        )
        assert response.status_code == 200, response.text
        payload = dict(response.json())
        current: dict[str, object] = payload["as_it_stands_today"]  # type: ignore[assignment]
        assert current["value"] == self.EQUITY_VALUE
        assert payload["is_restated"] is False, "the latest filing is the one a reader holds"
        assert payload["value_ever_changed"] is False
        assert payload["value_changed"] is False


class TestEvidenceCardsAreServedFaithfully:
    """The evidence index, which had no test at all until this one.

    This endpoint is where "no number leaves the system without an evidence card"
    stops being a design principle and becomes an HTTP response. What it serves is
    the only form of the card most readers will ever see, so the property worth
    pinning is fidelity: the values on the wire equal the values on disk, exactly.
    Rounding a statistic or truncating a hash here would silently break the audit
    trail while every status code stayed 200.

    A synthetic card in a temp directory, not the real one. There is exactly one
    real card and it is the by-product of an eighty-minute ingest; a test that read
    it would couple this suite to the warehouse and pass vacuously the day the file
    is not there.
    """

    REPRO_HASH: ClassVar[str] = "2e4799e47d1e2ad4d027d38350340ac5772564bd2f33efc085f26db13049dd4a"
    CODE_COMMIT: ClassVar[str] = "7f9b6d866e352fdbebbfce799cbecb6dbc40aa45"

    @staticmethod
    def _card(study_id: str, generated_at: str) -> dict[str, object]:
        return {
            "study_id": study_id,
            "hypothesis": "values change after first publication",
            "verdict": "5.02% of facts carry a value that changed after first publication",
            "trial_count": 1,
            "trial_family": "restatement-contamination",
            "repro_hash": TestEvidenceCardsAreServedFaithfully.REPRO_HASH,
            "generated_at": generated_at,
            "provenance": {
                "code_commit": TestEvidenceCardsAreServedFaithfully.CODE_COMMIT,
                "code_dirty": False,
                "data_vintage": "2026-07-27",
            },
            "arms": [{"name": "all-facts", "restated": 357842}],
            "comparisons": [{"name": "row-grain", "restated": 516187}],
            "caveats": ["reclassification is the largest unsized component"],
            "statistics": {
                "population": {"distinct_facts": 7133070, "facts": 13447437},
                # Deliberately more precision than any display would keep.
                "restatable_share": 0.09321674,
                "kill_threshold": 0.01,
            },
        }

    @pytest.fixture
    def evidence_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        directory = tmp_path / "evidence"
        directory.mkdir()
        monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
        return directory

    def test_a_card_survives_the_round_trip_without_losing_precision(
        self, client: TestClient, evidence_dir: Path
    ) -> None:
        card = self._card("S002-restatement-contamination", "2026-07-28T12:00:00+00:00")
        (evidence_dir / "S002.json").write_text(json.dumps(card), encoding="utf-8")

        payload = client.get("/api/evidence").json()

        assert len(payload["cards"]) == 1
        served = payload["cards"][0]
        assert served["repro_hash"] == self.REPRO_HASH, (
            "a truncated repro hash cannot be checked against a rerun, which is the "
            "only thing the hash is for"
        )
        assert served["provenance"]["code_commit"] == self.CODE_COMMIT
        assert served["trial_count"] == 1
        assert served["statistics"]["restatable_share"] == 0.09321674
        assert served["statistics"]["population"]["facts"] == 13447437
        assert served["arms"] == card["arms"]
        assert served["caveats"] == card["caveats"]

    def test_no_study_yet_is_reported_as_such_not_as_an_error(
        self, client: TestClient, evidence_dir: Path
    ) -> None:
        """An empty directory and a missing one must read the same.

        The study creates the directory before it writes a card, so a run that dies
        in between leaves an empty one. Returning 404 there would tell a reader the
        endpoint is broken when the warehouse is merely young.
        """
        assert not any(evidence_dir.iterdir())
        empty = client.get("/api/evidence")
        assert empty.status_code == 200
        assert empty.json() == {"cards": [], "note": "no study has been run in this warehouse yet"}

        evidence_dir.rmdir()
        missing = client.get("/api/evidence")
        assert missing.status_code == 200
        assert missing.json() == empty.json()

    def test_cards_are_newest_first(self, client: TestClient, evidence_dir: Path) -> None:
        for study_id, generated_at in (
            ("S001-older", "2026-07-01T00:00:00+00:00"),
            ("S003-newest", "2026-07-29T00:00:00+00:00"),
            ("S002-middle", "2026-07-15T00:00:00+00:00"),
        ):
            (evidence_dir / f"{study_id}.json").write_text(
                json.dumps(self._card(study_id, generated_at)), encoding="utf-8"
            )

        served = client.get("/api/evidence").json()["cards"]

        assert [card["study_id"] for card in served] == ["S003-newest", "S002-middle", "S001-older"]

    def test_one_unreadable_card_does_not_hide_the_others(
        self, client: TestClient, evidence_dir: Path
    ) -> None:
        """A half-written card must cost its own row, not the whole index.

        A study killed mid-write leaves truncated JSON. Failing the request would
        make one bad file conceal every good result behind it.
        """
        (evidence_dir / "good.json").write_text(
            json.dumps(self._card("S002-good", "2026-07-28T00:00:00+00:00")), encoding="utf-8"
        )
        (evidence_dir / "truncated.json").write_text('{"study_id": "S00', encoding="utf-8")

        response = client.get("/api/evidence")

        assert response.status_code == 200, response.text
        assert [card["study_id"] for card in response.json()["cards"]] == ["S002-good"]
