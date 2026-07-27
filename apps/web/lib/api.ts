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
  as_it_stands_today: Fact;
  relative_drift: number | null;
  is_restated: boolean;
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

export type Quality = {
  data_vintage: string;
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

export const pct = (value: number) =>
  `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
