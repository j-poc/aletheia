import { describe, expect, it } from "vitest";
import EvidencePage from "@/app/evidence/page";
import FeedPage from "@/app/feed/page";
import QualityPage from "@/app/quality/page";
import RevisionsPage from "@/app/revisions/page";
import { feed, quality, revision } from "./_factories";
import { cells, render, type Backend } from "./harness";

/**
 * The four supporting pages.
 *
 * The recurring hazard across all of them is the same one: a page with nothing to
 * show rendering as though it had failed, or a page that failed rendering as
 * though it had nothing to show. An empty filing feed on a Saturday and a
 * backend that is down produce the same blank table unless the page is written to
 * distinguish them, and the reader cannot tell which they are looking at.
 *
 * The other hazard is a derived number. The quality page divides two figures the
 * API sends separately, which is a place a page can print a wrong percentage from
 * entirely correct inputs.
 */

const NO_PARAMS = { searchParams: Promise.resolve({}) };

describe("data quality", () => {
  const backend = (body: unknown): Backend => ({ "/api/quality": { status: 200, body } });

  it("reports revision coverage as the study measures it", async () => {
    // These are the corpus figures from evidence card S002. The page and the
    // study disagreed once -- the endpoint grouped facts without `taxonomy` and
    // manufactured twelve restatements that never happened (D25) -- so the two
    // surfaces are pinned to the same numbers here.
    const { text } = await render(QualityPage, {}, backend(quality()));

    expect(text).toContain("357,842 of 7,133,070");
    expect(text).toContain("5.0%");
  });

  it("computes the percentage rather than trusting a supplied one", async () => {
    const { text } = await render(
      QualityPage,
      {},
      backend(quality({ revision_coverage: { distinct_periods: 200, periods_with_a_changed_value: 51 } })),
    );

    expect(text).toContain("51 of 200");
    expect(text).toContain("25.5%");
  });

  it("prints no percentage at all on an empty warehouse rather than NaN", async () => {
    // A freshly initialised warehouse has no periods. Dividing by it and
    // rendering the result puts "NaN%" on the page that exists to state coverage.
    const { text } = await render(
      QualityPage,
      {},
      backend(quality({ revision_coverage: { distinct_periods: 0, periods_with_a_changed_value: 0 } })),
    );

    expect(text).toContain("0 of 0");
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("%");
  });

  it("groups digits so a seven-figure row count is readable at a glance", async () => {
    const { text } = await render(QualityPage, {}, backend(quality()));

    expect(text).toContain("13,447,437");
    expect(text).not.toContain("13447437");
  });

  it("shows the data vintage, since a study run after it moves is a different study", async () => {
    const { text } = await render(QualityPage, {}, backend(quality()));

    expect(text).toContain("2026-07-27");
  });

  it("says it could not load rather than rendering an empty warehouse", async () => {
    const { text } = await render(QualityPage, {}, { "/api/quality": { unreachable: true } });

    expect(text).toContain("Could not load coverage");
    expect(text).toContain("cannot reach the API");
    expect(text).not.toContain("Rows held");
  });
});

describe("revision explorer", () => {
  const backend = (body: unknown): Backend => ({ "/api/revisions/": { status: 200, body } });
  const payload = (revisions: ReturnType<typeof revision>[]) => ({
    ticker: "AAPL",
    company: "Apple Inc.",
    n_revisions: revisions.length,
    revisions,
  });

  it("shows both values of a revised period, not the surviving one", async () => {
    // Keeping both rows is the whole point. Deduplicating them is exactly how a
    // vendor panel loses the history this application exists to preserve.
    const { text } = await render(RevisionsPage, NO_PARAMS, backend(payload([revision()])));

    expect(text).toContain("5.36");
    expect(text).toContain("6.78");
    expect(text).toContain("+26.49%");
    expect(text).toContain("90d");
  });

  it("calls an empty result an answer rather than an error", async () => {
    const { text } = await render(RevisionsPage, NO_PARAMS, backend(payload([])));

    expect(text).toContain("Nothing at this threshold. That is an answer, not an error");
    expect(text).not.toContain("Could not load");
  });

  it("agrees with itself on singular and plural", async () => {
    const one = await render(RevisionsPage, NO_PARAMS, backend(payload([revision()])));
    expect(one.text).toContain("1 value changed after publication");

    const two = await render(
      RevisionsPage,
      NO_PARAMS,
      backend(payload([revision(), revision({ period_end: "2009-09-26", new_accn: "0001193125-10-238044" })])),
    );
    expect(two.text).toContain("2 values changed after publication");
  });

  it("shows a dash where the relative change is undefined rather than a fabricated zero", async () => {
    // Asserted on the table cells, not on the page text. `pct(null)` does not
    // produce NaN -- in JavaScript `null >= 0` is true and `null * 100` is 0, so
    // a missing change renders as a confident "+0.00%", claiming the value did
    // not move when the truth is that the change is undefined. And an assertion
    // that the *page* contains an em-dash passes against that page anyway,
    // because the summary line above the table joins the company to the count
    // with one. Both halves of this were caught by the mutation gate, not by
    // reading the test.
    const { html, text } = await render(
      RevisionsPage,
      NO_PARAMS,
      backend(payload([revision({ relative_change: null })])),
    );

    expect(cells(html)).toContain("—");
    expect(text).not.toContain("+0.00%");
    expect(text).not.toContain("NaN");
  });

  it("never reports a row in this table as a zero change", async () => {
    // Apple's FY2023 long-term debt, verbatim from the warehouse: a real $3m
    // revision that rounds to "+0.00%" at two decimal places. Every row here has
    // `value <> prior_value` by construction, so that reading can only ever mean
    // "rounds to zero" -- which makes it wrong rather than merely coarse.
    const { html } = await render(
      RevisionsPage,
      NO_PARAMS,
      backend(
        payload([
          revision({
            concept: "LongTermDebt",
            unit: "USD",
            period_end: "2023-09-30",
            prior_value: "105100000000",
            new_value: "105103000000",
            relative_change: 2.8544243577545195e-5,
          }),
        ]),
      ),
    );

    expect(cells(html)).toContain("+<0.01%");
    expect(cells(html)).not.toContain("+0.00%");
  });

  it("passes the reader's threshold through instead of silently using the default", async () => {
    const { requested } = await render(
      RevisionsPage,
      { searchParams: Promise.resolve({ ticker: "MSFT", min_change: "0.25" }) },
      backend(payload([])),
    );

    expect(requested[0]).toBe("/api/revisions/MSFT?min_change=0.25");
  });

  it("says it could not load rather than showing an empty table", async () => {
    const { text } = await render(RevisionsPage, NO_PARAMS, { "/api/revisions/": { unreachable: true } });

    expect(text).toContain("Could not load revisions");
    expect(text).not.toContain("That is an answer, not an error");
  });
});

describe("filing feed", () => {
  const backend = (body: unknown): Backend => ({ "/api/feed": { status: 200, body } });

  it("says why a filing surfaced instead of asking the reader to trust a score", async () => {
    const { text } = await render(FeedPage, NO_PARAMS, backend(feed()));

    expect(text).toContain("CONFESSED");
    expect(text).toContain("Item 4.02 non-reliance on previously issued financial statements");
    expect(text).toContain("score 8.5");
  });

  it("keeps the score ordinal, never dressed up as a probability", async () => {
    // Calibrating a probability needs labelled outcomes -- filings followed by a
    // known restatement or enforcement action -- and no such set exists here.
    const { text } = await render(FeedPage, NO_PARAMS, backend(feed()));

    expect(text).toContain("Ranked by an ordinal concern score, not a probability.");
    expect(text).not.toMatch(/\b\d+% (likely|probability|chance)/);
  });

  it("calls a quiet day a quiet day rather than a failure", async () => {
    // Weekends, holidays and ordinary days all look like this.
    const { text } = await render(
      FeedPage,
      NO_PARAMS,
      backend(feed({ date: "2026-07-25", n_filings: 0, n_flagged: 0, items: [] })),
    );

    expect(text).toContain("Nothing flagged on this date");
    expect(text).toContain("an answer, not a failure");
    expect(text).not.toContain("Could not load");
  });

  it("asks for today when the reader has picked no date", async () => {
    const { requested } = await render(FeedPage, NO_PARAMS, backend(feed()));

    expect(requested[0]).toBe("/api/feed");
  });

  it("asks for the day the reader picked", async () => {
    const { requested } = await render(
      FeedPage,
      { searchParams: Promise.resolve({ day: "2026-07-24" }) },
      backend(feed()),
    );

    expect(requested[0]).toBe("/api/feed?day=2026-07-24");
  });

  it("says it could not load rather than showing a quiet day", async () => {
    const { text } = await render(FeedPage, NO_PARAMS, { "/api/feed": { unreachable: true } });

    expect(text).toContain("Could not load the feed");
    expect(text).not.toContain("an answer, not a failure");
  });
});

describe("evidence cards", () => {
  const backend = (body: unknown): Backend => ({ "/api/evidence": { status: 200, body } });

  const card = {
    study_id: "S002",
    hypothesis: "Restated fundamentals contaminate a conventional backtest measurably.",
    verdict: "Confirmed at corpus scale; no return claim is made.",
    trial_count: 1,
    trial_family: "corpus-contamination",
    repro_hash: "2e4799e47d1e2ad4d027d38350340ac5772564bd2f33efc085f26db13049dd4a",
    generated_at: "2026-07-29T18:04:11",
    provenance: {
      code_commit: "7f9b6d866e352fdbebbfce799cbecb6dbc40aa45",
      code_dirty: false,
      data_vintage: "2026-07-27",
      universe_source: "SEC EDGAR bulk financial statement data sets",
    },
    arms: [],
    comparisons: [],
    caveats: ["No price data, so no return figure is claimed."],
  };

  it("shows the commit, the vintage and the trial count beside the result", async () => {
    // A performance figure alone is not a result. These are what decide whether
    // the number means anything.
    const { text } = await render(EvidencePage, {}, backend({ cards: [card] }));

    expect(text).toContain("S002");
    expect(text).toContain("7f9b6d866e35");
    expect(text).toContain("2026-07-27");
    expect(text).toContain('Trials in "corpus-contamination"');
    expect(text).toContain("clean");
  });

  it("says loudly when the tree was dirty, because the run is then not reproducible", async () => {
    const { text } = await render(
      EvidencePage,
      {},
      backend({ cards: [{ ...card, provenance: { ...card.provenance, code_dirty: true } }] }),
    );

    expect(text).toContain("DIRTY — not reproducible");
  });

  it("shows the author's caveats rather than only the verdict", async () => {
    const { text } = await render(EvidencePage, {}, backend({ cards: [card] }));

    expect(text).toContain("No price data, so no return figure is claimed.");
  });

  it("reports an empty warehouse as no study yet, not as an error", async () => {
    const { text } = await render(
      EvidencePage,
      {},
      backend({ cards: [], note: "no study has been run in this warehouse yet" }),
    );

    expect(text).toContain("no study has been run in this warehouse yet");
    expect(text).toContain("make study");
    expect(text).not.toContain("Could not load evidence");
  });

  it("says it could not load rather than implying no study was ever run", async () => {
    const { text } = await render(EvidencePage, {}, { "/api/evidence": { unreachable: true } });

    expect(text).toContain("Could not load evidence");
    expect(text).not.toContain("no study has been run");
  });
});
