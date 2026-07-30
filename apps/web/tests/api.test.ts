import { describe, expect, it } from "vitest";
import { ApiError, api, edgarUrl, pct, pctChange } from "@/lib/api";

/**
 * The client, whose whole job is to fail legibly.
 *
 * Every page in this application distinguishes "could not load" from "nothing
 * here", and it can only do that if the client below refuses to turn a failure
 * into an empty object. These tests are about the failure paths for that reason;
 * the success path is one line and is covered incidentally by every page test.
 */

const OK = { headers: { "content-type": "application/json" } };

async function withFetch<T>(stub: typeof globalThis.fetch, body: () => Promise<T>): Promise<T> {
  const real = globalThis.fetch;
  globalThis.fetch = stub;
  try {
    return await body();
  } finally {
    globalThis.fetch = real;
  }
}

describe("api()", () => {
  it("returns the parsed body on success", async () => {
    const value = await withFetch(
      async () => new Response(JSON.stringify({ value: "5.36" }), OK),
      () => api<{ value: string }>("/api/asof/AAPL"),
    );

    expect(value).toEqual({ value: "5.36" });
  });

  it("requests the configured base and never caches", async () => {
    // `no-store` is the contract, not a performance choice: a point-in-time
    // viewer that served a cached answer would be undermining the one property
    // the system exists to provide.
    let seen: { url: string; init?: RequestInit } | null = null;
    await withFetch(
      async (input, init) => {
        seen = { url: String(input), init };
        return new Response("{}", OK);
      },
      () => api("/api/quality"),
    );

    expect(seen!.url).toBe("http://api.test/api/quality");
    expect(seen!.init?.cache).toBe("no-store");
  });

  it("turns an unreachable backend into an error that names it", async () => {
    // An empty page reads as "there is nothing here", which is a different and
    // much worse claim than "this could not be loaded".
    const caught = await withFetch(
      async () => {
        throw new TypeError("fetch failed");
      },
      () => api("/api/quality").catch((error: unknown) => error),
    );

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(0);
    expect((caught as ApiError).detail).toContain("cannot reach the API at http://api.test");
    expect((caught as ApiError).detail).toContain("make api");
  });

  it("surfaces the API's own explanation of a 4xx", async () => {
    const caught = await withFetch(
      async () =>
        new Response(JSON.stringify({ detail: "no facts for NOSUCH" }), { status: 404, ...OK }),
      () => api("/api/asof/NOSUCH").catch((error: unknown) => error),
    );

    expect((caught as ApiError).status).toBe(404);
    expect((caught as ApiError).detail).toBe("no facts for NOSUCH");
  });

  it("falls back to the status text when the error body is not JSON", async () => {
    // A proxy or a crashed worker returns HTML, not the API's error shape.
    // Letting the JSON parse throw here would replace a 502 the reader can act
    // on with an unhandled SyntaxError.
    const caught = await withFetch(
      async () => new Response("<html>502 Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" }),
      () => api("/api/quality").catch((error: unknown) => error),
    );

    expect((caught as ApiError).status).toBe(502);
    expect((caught as ApiError).detail).toBe("Bad Gateway");
  });

  it("is an Error, so an uncaught one still reads as a failure", async () => {
    const caught = (await withFetch(
      async () => new Response(JSON.stringify({ detail: "gone" }), { status: 410, ...OK }),
      () => api("/api/quality").catch((error: unknown) => error),
    )) as ApiError;

    expect(caught).toBeInstanceOf(Error);
    expect(caught.message).toBe("gone");
  });
});

describe("edgarUrl()", () => {
  it("builds the document index URL EDGAR actually serves", async () => {
    // The directory segment strips the dashes and the file name keeps them.
    // Getting either wrong yields a 404 on every provenance link on every page,
    // which is the one link that makes a figure checkable.
    expect(edgarUrl(320193, "0001193125-09-214859")).toBe(
      "https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/0001193125-09-214859-index.htm",
    );
  });
});

describe("pct()", () => {
  it("signs a gain explicitly so the direction is never ambiguous", () => {
    expect(pct(0.2649253731343284)).toBe("+26.49%");
  });

  it("keeps the minus sign on a loss without doubling it", () => {
    expect(pct(-0.0512)).toBe("-5.12%");
  });

  it("shows exact zero as a signed zero rather than hiding it", () => {
    expect(pct(0)).toBe("+0.00%");
  });

  it("rounds to two places rather than truncating", () => {
    expect(pct(0.123456)).toBe("+12.35%");
  });

  it("refuses an absent value rather than formatting it as no change", () => {
    // The trap this guard exists for: `null >= 0` is true and `null * 100` is 0,
    // so without it an undefined change prints "+0.00%" -- indistinguishable from
    // a period that genuinely did not move. Callers with a nullable figure render
    // a dash; arriving here with one is a bug, and a loud one is worth more than
    // a plausible number.
    expect(() => pct(null as unknown as number)).toThrow(/must render as absent/);
    expect(() => pct(undefined as unknown as number)).toThrow(/must render as absent/);
    expect(() => pct(Number.NaN)).toThrow(/must render as absent/);
    expect(() => pct(Number.POSITIVE_INFINITY)).toThrow(/must render as absent/);
  });
});

describe("pctChange()", () => {
  it("refuses to round a real change down to nothing", () => {
    // Live case: Apple's FY2023 long-term debt revised from 105,100,000,000 to
    // 105,103,000,000 -- a genuine $3m move that `toFixed(2)` renders as
    // "+0.00%" in a table whose every row is a value that changed.
    expect(pctChange(2.8544243577545195e-5)).toBe("+<0.01%");
    expect(pctChange(-2.854342882696022e-5)).toBe("-<0.01%");
  });

  it("still reports an exact zero as zero", () => {
    // The threshold is about rounding, not about smallness. A figure that is
    // actually zero must not be dressed up as a change too small to show.
    expect(pctChange(0)).toBe("+0.00%");
  });

  it("agrees with pct() everywhere the rounding is not misleading", () => {
    for (const value of [0.2649253731343284, -0.0512, 0.123456, 0.0001, -0.00005001]) {
      expect(pctChange(value)).toBe(pct(value));
    }
  });

  it("inherits the refusal to format an absent value", () => {
    expect(() => pctChange(null as unknown as number)).toThrow(/must render as absent/);
  });
});
