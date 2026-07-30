import { describe, expect, it } from "vitest";

import EvidencePage from "@/app/evidence/page";
import { VintageStrip } from "@/app/vintage-strip";
import { freshness, quality } from "./_factories";
import { type Backend, render } from "./harness";

/**
 * The staleness signal, and the empty-arms state.
 *
 * Both come from the same failure: a surface that answers confidently about
 * something it does not have. The data-quality page rendered `2026-07-27` in the
 * same weight and colour on every day after 2026-07-27, so a reader three months
 * later saw a number, not a warning, and had to know both the ingest contract and
 * today's date to work out that every other page was answering about a world that
 * had moved. The evidence card rendered eight performance column headings —
 * Sharpe among them — over an empty body, which reads as a table that failed to
 * load or a strategy that earned nothing, when in fact the study measures no
 * returns at all by design.
 *
 * Neither throws. Neither fails a type check. Both exit zero.
 */

const QUALITY = "/api/quality";

function backend(state: Parameters<typeof freshness>[0]): Backend {
  return { [QUALITY]: { status: 200, body: quality({ freshness: freshness(state) }) } };
}

describe("the freshness strip", () => {
  it("says nothing at all when the warehouse is current", async () => {
    // Absence is the assertion. A strip that is always there is furniture, and
    // furniture stops being read -- which would cost the three states that matter.
    const { html } = await render(VintageStrip, undefined, backend({ state: "fresh" }));
    expect(html).toBe("");
  });

  it("names the state in words, not only in colour", async () => {
    const { text } = await render(
      VintageStrip,
      undefined,
      backend({ state: "stale", reason: "the newest filing is 96 day(s) old" }),
    );
    // A reader on a monochrome screen share, or with colour-vision deficiency,
    // gets the same information as everyone else.
    expect(text).toContain("Out of date");
    expect(text).toContain("96 day(s) old");
  });

  it("distinguishes incomplete from out-of-date by shape, not just by hue", async () => {
    const stale = await render(VintageStrip, undefined, backend({ state: "stale" }));
    const partial = await render(VintageStrip, undefined, backend({ state: "partial" }));
    // Same colour on purpose -- same severity. The shape is what separates them,
    // so if these two ever render identically the distinction has been lost.
    expect(stale.html).not.toBe(partial.html);
    expect(stale.html).toContain("rounded-full");
    expect(partial.html).toContain("rounded-none");
    expect(partial.text).toContain("Incomplete");
  });

  it("treats a warehouse dated in the future as untrustworthy, not as very fresh", async () => {
    const { text } = await render(VintageStrip, undefined, backend({ state: "broken" }));
    expect(text).toContain("Not trustworthy");
  });

  it("reports an unreachable API as the loudest state rather than as silence", async () => {
    // The tempting `return null` on a failed fetch would leave every page below
    // looking exactly as it does when the warehouse is healthy.
    const { text } = await render(VintageStrip, undefined, {
      [QUALITY]: { unreachable: true },
    });
    expect(text).toContain("Not trustworthy");
    expect(text).toContain("Nothing below is coming from the warehouse");
  });

  it("does not make the reader subtract the vintage from today's date", async () => {
    // The reason arrives written. A strip that printed only the two dates would
    // be handing back the arithmetic the server already did.
    const { text } = await render(
      VintageStrip,
      undefined,
      backend({ state: "stale", reason: "run `make ingest`; until then every page answers as of 2026-07-27." }),
    );
    expect(text).toContain("make ingest");
  });
});

describe("an evidence card with no return arms", () => {
  const CARDS = "/api/evidence";

  const CARD = {
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
    arms: [] as unknown[],
    comparisons: [],
    caveats: ["No price data, so no return figure is claimed."],
  };

  function cards(arms: unknown[]): Backend {
    return { [CARDS]: { status: 200, body: { cards: [{ ...CARD, arms }] } } };
  }

  it("says the returns are absent rather than printing empty performance columns", async () => {
    const { html, text } = await render(EvidencePage, undefined, cards([]));
    expect(text).toContain("No return arms");
    // Asserted on the element, not the word. The first version of this checked
    // that the text lacked "Sharpe" and failed against the fix, because the
    // sentence explaining the absence names Sharpe itself -- a prose match would
    // have been satisfied by prose. What must not exist is the table.
    expect(html).not.toContain("<table");
  });

  it("says why they are absent, and that absent is not zero or pending", async () => {
    const { text } = await render(EvidencePage, undefined, cards([]));
    expect(text).toContain("absent, not zero");
    expect(text).toContain("price panel");
  });

  it("still renders the table when the study does have arms", async () => {
    // The other arm. Without it, hiding the table unconditionally would pass.
    const { html, text } = await render(
      EvidencePage,
      undefined,
      cards([
        {
          label: "contaminated",
          n_periods: 48,
          gross_annualised: 0.0812,
          net_annualised: 0.0611,
          net_stdev_annualised: 0.1433,
          annualised_sharpe: 0.43,
          mean_turnover: 1.2,
          n_excluded: 17,
        },
      ]),
    );
    expect(html).toContain("<table");
    expect(text).toContain("0.43");
    expect(text).not.toContain("No return arms");
  });
});
