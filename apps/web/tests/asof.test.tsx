import { describe, expect, it } from "vitest";
import Page from "@/app/page";
import { asOf, fact, unchanged } from "./_factories";
import { render, type Backend } from "./harness";
import type { AsOf } from "@/lib/api";

/**
 * The as-of viewer's prose, which is the product.
 *
 * The two value cards are the easy part -- they print whatever the API sent. The
 * paragraph underneath is the part that makes a claim, and it is derived from
 * four booleans whose combinations do not all mean what a first reading suggests.
 * Every defect this page has shipped has been in that derivation, and each one is
 * pinned below by the case that caught it.
 *
 * These tests assert on the rendered sentence rather than on the branch taken. A
 * test that checked "the `value_changed` branch ran" would still pass if that
 * branch printed something false, which is precisely how the shipped defects got
 * through review.
 */

const ASOF = "/api/asof/";

function backend(payload: AsOf): Backend {
  return { [ASOF]: { status: 200, body: payload } };
}

async function prose(payload: AsOf) {
  const { text } = await render(Page, { searchParams: Promise.resolve({}) }, backend(payload));
  return text;
}

describe("the sentence under the two cards", () => {
  it("calls a moved value a restatement and quantifies the lookahead", async () => {
    // Apple FY2008 diluted EPS read on 2009-12-01: 5.36 was filed, 6.78 did not
    // exist yet. This is the case in the README and the one the system was built
    // to show, so it is the first thing that must never break.
    const text = await prose(asOf());

    expect(text).toContain("This period was restated.");
    expect(text).toContain("Apple Inc. first reported 5.36 on 2009-10-27");
    expect(text).toContain("it now stands at 6.78 as of 2010-01-25");
    expect(text).toContain("a change of +26.49%");
    expect(text).toContain(
      "A simulation dated 2009-12-01 that used the restated figure would be reading 3 months into the future.",
    );
  });

  it("does not call it never-restated once the restatement is old news", async () => {
    // The shipped defect. The first branch was keyed off `is_restated`, which
    // compares accessions: after 2010-01-25 the restated filing is also the
    // latest one a reader would have had, the accessions match, the flag goes
    // false, and the page printed "this period's value was never revised" on the
    // single most important example in the repository.
    const text = await prose(
      asOf({
        knowledge_date: "2010-06-01",
        as_known: fact({ value: "6.78", accn: "0001193125-10-012091", form: "10-K/A", report_seq: 2 }),
        is_restated: false,
        already_restated_by_then: true,
        known_is_current: true,
      }),
    );

    expect(text).toContain("This period was restated.");
    expect(text).not.toContain("never revised");
    expect(text).toContain(
      "By the knowledge date above, the restatement was already public",
    );
  });

  it("does not claim the columns agree when they visibly do not", async () => {
    // Also shipped. Between two revisions the left column holds an intermediate
    // figure -- neither the original nor the one standing today -- and the page
    // printed "the left column shows it too" directly above two different
    // numbers. 4.8% of revised periods carry three or more distinct values.
    const text = await prose(
      asOf({
        knowledge_date: "2010-06-01",
        as_known: fact({ value: "6.40", accn: "0001193125-10-012091", report_seq: 2 }),
        already_restated_by_then: true,
        known_is_current: false,
      }),
    );

    expect(text).toContain("moved again afterwards");
    expect(text).toContain("an intermediate figure that is neither the original nor the one standing today");
    expect(text).not.toContain("the left column shows it too");
  });

  it("catches a value revised and then revised back, which the two columns cannot", async () => {
    // Shipped, live on AAR Corp: 174.2m -> 148.3m -> 174.2m. `value_changed`
    // compares first-reported against current, so it is false here and the page
    // printed "The value never moved" beside a left column reading 148300000 and
    // a right column reading 174200000. 10,080 of 357,101 revised periods end
    // where they started.
    const aar = fact({
      value: "174200000",
      unit: "USD",
      period_end: "2015-05-31",
      accn: "0001104659-15-052073",
    });
    const text = await prose(
      asOf({
        ticker: "AIR",
        company: "AAR CORP",
        cik: 1750,
        concept: "Revenues",
        knowledge_date: "2016-09-01",
        as_known: { ...aar, value: "148300000", report_seq: 2 },
        as_first_reported: aar,
        as_it_stands_today: { ...aar, report_seq: 3 },
        relative_drift: 0,
        is_restated: true,
        value_changed: false,
        value_ever_changed: true,
        already_restated_by_then: true,
        known_is_current: false,
      }),
    );

    expect(text).toContain("This period was revised, and then revised back.");
    expect(text).not.toContain("never moved");
    expect(text).toContain("The two columns agree; the history does not.");
    expect(text).toContain(
      "On the knowledge date above a reader held 148300000 — a figure that no longer exists anywhere in the record.",
    );
  });

  it("calls a repeated figure a re-presentation rather than a restatement", async () => {
    // 90.6% of refilings carry the number forward unchanged. Calling those
    // restatements would cry wolf on the common case and devalue the flag on the
    // uncommon one.
    const text = await prose(
      unchanged({ is_restated: true, as_it_stands_today: fact({ form: "10-K/A", report_seq: 2 }) }),
    );

    expect(text).toContain("This period was re-presented, not revised.");
    expect(text).toContain("carried the same figure forward under a different accession");
    expect(text).toContain("The value never moved; only its source document did.");
  });

  it("says plainly when nothing ever happened to the number", async () => {
    const text = await prose(unchanged());

    expect(text).toContain("This period’s value was never revised, so both columns agree.");
    expect(text).not.toContain("restated");
    expect(text).not.toContain("re-presented");
  });

  it("omits the percentage when there is no denominator rather than printing one", async () => {
    // `relative_drift` is null whenever the first report was 0, and 123,177
    // refilings in the warehouse are 0 -> 0. Rendering `null` as a number would
    // put "+0.00%" or "NaN%" on a period where the change is undefined.
    const text = await prose(asOf({ relative_drift: null }));

    expect(text).toContain("This period was restated.");
    expect(text).not.toContain("a change of");
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("%");
  });
});

describe("the two value cards", () => {
  it("colours the right-hand card only when the value itself moved", async () => {
    // Tone keys off `value_ever_changed`, not off a new accession. The colour is
    // the warning, and firing it on 90.6% of refilings would make it noise.
    const moved = await render(Page, { searchParams: Promise.resolve({}) }, backend(asOf()));
    const same = await render(Page, { searchParams: Promise.resolve({}) }, backend(unchanged({ is_restated: true })));

    expect(moved.html).toContain("var(--color-restated)");
    expect(same.html).not.toContain("var(--color-restated)");
  });

  it("labels the period by both dates, because an end date is not a period", async () => {
    // A fiscal year and its fourth quarter end on the same day and report
    // different numbers. A card labelled only by the end date is ambiguous about
    // what the figure means.
    const text = await prose(asOf());

    expect(text).toContain("2007-09-30 to 2008-09-27 (363d)");
  });

  it("labels a balance-sheet instant as an instant rather than inventing a start", async () => {
    const instant = fact({ period_start: null, period_end: "2008-09-27" });
    const text = await prose(asOf({ as_known: instant, as_first_reported: instant }));

    expect(text).toContain("instant, 2008-09-27");
    expect(text).not.toContain("NaNd");
  });

  it("counts refilings from one rather than calling the first publication a refiling", async () => {
    const first = await prose(asOf());
    expect(first).toContain("first publication");

    const second = await prose(asOf({ as_known: fact({ report_seq: 2 }) }));
    expect(second).toContain("refiling #1");
  });

  it("links every figure to the EDGAR document it came from", async () => {
    const { html } = await render(Page, { searchParams: Promise.resolve({}) }, backend(asOf()));

    expect(html).toContain(
      "https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/0001193125-09-214859-index.htm",
    );
  });
});

describe("when the question cannot be answered", () => {
  it("shows the failure instead of an empty page that reads as no data", async () => {
    const { text } = await render(
      Page,
      { searchParams: Promise.resolve({}) },
      { [ASOF]: { status: 400, body: { detail: "period_end 2015-09-26 is shared by FY2015 and Q4 2015" } } },
    );

    expect(text).toContain("Could not answer that");
    expect(text).toContain("period_end 2015-09-26 is shared by FY2015 and Q4 2015");
    expect(text).not.toContain("This period was restated.");
    expect(text).not.toContain("never revised");
  });

  it("names the backend when it is not running at all", async () => {
    const { text } = await render(
      Page,
      { searchParams: Promise.resolve({}) },
      { [ASOF]: { unreachable: true } },
    );

    expect(text).toContain("cannot reach the API");
    expect(text).toContain("make api");
  });
});

describe("the query the page sends", () => {
  it("asks about the default case when the reader has chosen nothing", async () => {
    const { requested } = await render(Page, { searchParams: Promise.resolve({}) }, backend(asOf()));

    expect(requested).toHaveLength(1);
    expect(requested[0]).toBe(
      "/api/asof/AAPL?knowledge_date=2009-12-01&concept=EarningsPerShareDiluted&period_end=2008-09-27",
    );
  });

  it("omits a blank period start rather than sending an empty one", async () => {
    // Blank asks the API to choose, which it refuses to do when the end date is
    // genuinely shared. Sending `period_start=` instead would ask it to match a
    // period starting on the empty string, and it would find none.
    const { requested } = await render(
      Page,
      { searchParams: Promise.resolve({ period_start: "" }) },
      backend(asOf()),
    );

    expect(requested[0]).not.toContain("period_start");
  });

  it("passes a period start through when the reader supplies one", async () => {
    const { requested } = await render(
      Page,
      { searchParams: Promise.resolve({ period_start: "2007-09-30" }) },
      backend(asOf()),
    );

    expect(requested[0]).toContain("period_start=2007-09-30");
  });

  it("escapes a ticker rather than pasting it into the path", async () => {
    const { requested } = await render(
      Page,
      { searchParams: Promise.resolve({ ticker: "BRK/B" }) },
      backend(asOf()),
    );

    expect(requested[0]).toContain("/api/asof/BRK%2FB?");
  });
});
