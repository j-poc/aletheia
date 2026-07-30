/**
 * Thin client for the ALETHEIA API.
 *
 * Every call is server-side and uncached. A point-in-time viewer that served a
 * stale answer would be undermining the one property the system exists to
 * provide, so `no-store` is not a performance oversight -- it is the contract.
 */

const BASE = process.env.ALETHEIA_API ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

export async function api<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { cache: "no-store" });
  } catch {
    // A dead backend must say so plainly rather than rendering an empty page
    // that looks like "no data found".
    throw new ApiError(0, `cannot reach the API at ${BASE}. Is \`make api\` running?`);
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }
  return (await response.json()) as T;
}

export type Fact = {
  value: string;
  unit: string;
  /** Null for a balance-sheet instant, which is measured at a moment and has no start. */
  period_start: string | null;
  period_end: string;
  filed_at: string;
  knowledge_date: string;
  accn: string;
  form: string;
  report_seq: number;
  source_uri: string;
};

export type AsOf = {
  ticker: string;
  company: string;
  cik: number;
  concept: string;
  knowledge_date: string;
  as_known: Fact;
  as_first_reported: Fact;
  as_it_stands_today: Fact;
  /** Null when the first report was 0 — there is no denominator. */
  relative_drift: number | null;
  /** A later filing supersedes the one this date would have had. Says nothing
   * about whether the number moved: 90.6% of refilings carry it forward. */
  is_restated: boolean;
  /** The number itself moved. Compared as Decimal server-side, so it answers on
   * the periods where `relative_drift` is null and cannot. */
  value_changed: boolean;
  /** The value had already been revised by the knowledge date, so the left
   * column is showing a restated figure rather than the original. */
  already_restated_by_then: boolean;
  /** The figure on the knowledge date is the one that still stands. False when a
   * later revision followed it, which is not rare: 4.8% of revised periods carry
   * three or more distinct values, and on those the left column can show an
   * intermediate figure that is neither the original nor the current one. */
  known_is_current: boolean;
  /** The value moved at some point in this period's filing history — whether or
   * not it moved back. Every other flag here compares two points on the chain
   * and so is blind to a period revised and then revised back: 10,080 of 357,101
   * revised periods end where they started, and on those `value_changed` is
   * false while the history is anything but unchanged. */
  value_ever_changed: boolean;
};

export type Revision = {
  concept: string;
  unit: string;
  period_end: string;
  prior_value: string;
  new_value: string;
  prior_knowledge_date: string;
  new_knowledge_date: string;
  days_to_revision: number;
  relative_change: number | null;
  new_accn: string;
  new_form: string;
};

export type Feed = {
  date: string;
  n_filings: number;
  n_flagged: number;
  items: {
    accn: string;
    cik: number;
    form: string;
    knowledge_date: string;
    score: number;
    confidence: string;
    confidence_meaning: string;
    findings: { flag: string; evidence: string }[];
  }[];
};

/**
 * How old the warehouse is, judged server-side.
 *
 * `state` arrives already decided rather than as a date the page subtracts from
 * today. That is deliberate: a renderer handed `"stale"` cannot present it as
 * current, whereas a renderer handed `"2026-07-27"` has to know the contract, and
 * will go on quietly rendering fresh-looking pages on the day the contract
 * changes. The arithmetic belongs where the clock is injected and testable.
 */
export type Freshness = {
  state: "fresh" | "stale" | "partial" | "broken";
  /** Written for a reader, not a log: what is wrong, and what to do about it. */
  reason: string;
  data_vintage: string;
  observed_on: string;
  age_days: number;
  fresh_within_days: number;
  /** Missing inputs, named. Reported at every state so `stale` cannot mask them. */
  gaps: string[];
};

export type Quality = {
  data_vintage: string;
  freshness: Freshness;
  row_counts: Record<string, number>;
  revision_coverage: {
    distinct_periods: number;
    periods_with_a_changed_value: number;
  };
  ingest_runs: { source: string; runs: number; last_started: string }[];
};

/** EDGAR's document URL for an accession, so every figure is one click from its source. */
export function edgarUrl(cik: number, accn: string): string {
  return `https://www.sec.gov/Archives/edgar/data/${cik}/${accn.replace(/-/g, "")}/${accn}-index.htm`;
}

/**
 * A signed percentage, or a loud failure -- never a quiet zero.
 *
 * The guard is not defensive clutter. `null >= 0` is true in JavaScript and
 * `null * 100` is 0, so an absent value formats as `+0.00%`: a confident claim
 * that the number did not move, printed on a period where the change is
 * undefined. Every caller is typed against that, but the payloads come from JSON
 * and the type annotation is a compile-time promise about a runtime value that
 * nothing validates. Absent has to render as absent -- callers with a nullable
 * figure show a dash -- and reaching here with one is a bug that should say so
 * rather than fabricate a number the reader cannot tell from a real one.
 */
export const pct = (value: number) => {
  if (!Number.isFinite(value)) {
    throw new TypeError(`pct() received ${JSON.stringify(value)}; an absent change must render as absent`);
  }
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
};

/**
 * The same, for a figure already known to have moved -- so rounding cannot say it did not.
 *
 * Apple's FY2023 long-term debt was revised from 105,100,000,000 to
 * 105,103,000,000. That is 0.0029%, which `toFixed(2)` renders as `+0.00%`: a
 * row in a table of values that changed, reporting that nothing changed. Every
 * row in that table has `value <> prior_value` by construction, so `+0.00%`
 * there can only ever mean "rounds to zero" and never "is zero" -- which makes
 * the display unambiguously wrong rather than merely coarse.
 *
 * Kept separate from `pct` rather than folded into it, because a genuine zero is
 * meaningful elsewhere: an evidence card reporting a 0.00% difference between two
 * arms is stating a result, not rounding one away.
 */
export const pctChange = (value: number) => {
  const rendered = pct(value);
  if (value !== 0 && /^[+-]?0\.00%$/.test(rendered)) {
    return `${value > 0 ? "+" : "-"}<0.01%`;
  }
  return rendered;
};
