import { describe, expect, it } from "vitest";

import AsOfPage from "@/app/page";
import { render, UnexpectedRequest } from "./harness";

/**
 * Tests of the test harness.
 *
 * The commit that introduced this suite claimed, as a design property, that "an
 * unstubbed request throws rather than returning {}, because a page calling the
 * wrong endpoint would otherwise render its error panel and the assertion would
 * pass for the wrong reason." Nothing in the suite had ever exercised that path,
 * so the claim was an assertion about untested code -- and checking it showed the
 * property did not hold. `api()` wraps `fetch` in `try { ... } catch` and turns
 * *any* rejection into ApiError(0, "cannot reach the API"), and every page then
 * catches that and renders the error panel. The strict stub's throw was being
 * swallowed twice over. A test asserting on the error panel would have passed
 * against a page requesting an endpoint the test never described -- exactly the
 * failure the strictness exists to prevent.
 *
 * It is enforced now by recording unstubbed paths and raising after the render
 * returns, where no page-level catch can reach it. These tests are what makes
 * that observable; scripts/web_mutation_gate.mjs removes the post-render check
 * and requires them to fail.
 */

const NO_PARAMS = { searchParams: Promise.resolve({}) };

describe("the fetch stub", () => {
  it("fails the test when the page requests a path with no stub", async () => {
    await expect(render(AsOfPage, NO_PARAMS, {})).rejects.toThrow(UnexpectedRequest);
  });

  it("names the missing path and the stubs that were offered", async () => {
    await expect(
      render(AsOfPage, NO_PARAMS, { "/api/nothing/the/page/asks/for": { status: 200, body: {} } }),
    ).rejects.toThrow(/no stub for \/api\/asof.*stubs: \/api\/nothing\/the\/page\/asks\/for/s);
  });

  it("does not let a page swallow the missing stub into its error panel", async () => {
    // The regression proper. Before the fix this resolved -- with `html`
    // containing "cannot reach the API" -- instead of rejecting.
    const attempt = render(AsOfPage, NO_PARAMS, {});
    await expect(attempt).rejects.toThrow();
    await attempt.catch((caught: unknown) => {
      expect(String(caught)).not.toContain("cannot reach the API");
    });
  });

  it("rejects a request that leaves the API base entirely", async () => {
    await expect(
      render(
        async () => {
          await fetch("https://www.sec.gov/cgi-bin/browse-edgar");
          return <p>unreachable</p>;
        },
        undefined,
        {},
      ),
    ).rejects.toThrow(/no stub for https:\/\/www\.sec\.gov/);
  });

  it("still reports a genuinely unreachable backend as the page's own error", async () => {
    // The counterpart: `{ unreachable: true }` is a *described* backend state, so
    // it must reach the page and be rendered, not fail the test. Without this the
    // check above could be satisfied by failing on every fetch rejection, which
    // would delete the suite's coverage of the offline path.
    const { text } = await render(AsOfPage, NO_PARAMS, { "/api/asof/": { unreachable: true } });
    expect(text).toContain("cannot reach the API");
  });
});
