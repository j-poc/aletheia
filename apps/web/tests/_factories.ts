import type { AsOf, Fact, Feed, Quality, Revision } from "@/lib/api";

/**
 * Fixtures built from cases observed in the warehouse, not invented ones.
 *
 * Both anchors below are named in the source comments of `app/page.tsx` as the
 * cases that caught shipped defects: Apple's FY2008 diluted EPS (5.36 restated to
 * 6.78) and AAR Corp's revised-and-revised-back figure. Keeping the fixtures on
 * the real numbers means a failure here reads as "the page now says the wrong
 * thing about Apple" rather than about an invented company, and it keeps the
 * suite honest about the shape of the data -- values are strings because the API
 * serialises Decimals as strings, and rounding them to floats in a fixture would
 * hide precision loss the real payload does not have.
 */

const APPLE_FIRST: Fact = {
  value: "5.36",
  unit: "USD/shares",
  period_start: "2007-09-30",
  period_end: "2008-09-27",
  filed_at: "2009-10-27",
  knowledge_date: "2009-10-27",
  accn: "0001193125-09-214859",
  form: "10-K",
  report_seq: 1,
  source_uri: "https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/",
};

const APPLE_RESTATED: Fact = {
  ...APPLE_FIRST,
  value: "6.78",
  filed_at: "2010-01-25",
  knowledge_date: "2010-01-25",
  accn: "0001193125-10-012091",
  form: "10-K/A",
  report_seq: 2,
};

export function fact(overrides: Partial<Fact> = {}): Fact {
  return { ...APPLE_FIRST, ...overrides };
}

/**
 * The default is the case the whole system was built to show, at a date before
 * the restatement was public: 5.36 on the left, 6.78 on the right.
 */
export function asOf(overrides: Partial<AsOf> = {}): AsOf {
  return {
    ticker: "AAPL",
    company: "Apple Inc.",
    cik: 320193,
    concept: "EarningsPerShareDiluted",
    knowledge_date: "2009-12-01",
    as_known: APPLE_FIRST,
    as_first_reported: APPLE_FIRST,
    as_it_stands_today: APPLE_RESTATED,
    relative_drift: 0.2649253731343284,
    is_restated: true,
    value_changed: true,
    already_restated_by_then: false,
    known_is_current: false,
    value_ever_changed: true,
    ...overrides,
  };
}

/** A period no filing ever moved: every flag false, both columns equal. */
export function unchanged(overrides: Partial<AsOf> = {}): AsOf {
  return asOf({
    as_known: APPLE_FIRST,
    as_first_reported: APPLE_FIRST,
    as_it_stands_today: APPLE_FIRST,
    relative_drift: 0,
    is_restated: false,
    value_changed: false,
    already_restated_by_then: false,
    known_is_current: true,
    value_ever_changed: false,
    ...overrides,
  });
}

export function revision(overrides: Partial<Revision> = {}): Revision {
  return {
    concept: "EarningsPerShareDiluted",
    unit: "USD/shares",
    period_end: "2008-09-27",
    prior_value: "5.36",
    new_value: "6.78",
    prior_knowledge_date: "2009-10-27",
    new_knowledge_date: "2010-01-25",
    days_to_revision: 90,
    relative_change: 0.2649253731343284,
    new_accn: "0001193125-10-012091",
    new_form: "10-K/A",
    ...overrides,
  };
}

export function feed(overrides: Partial<Feed> = {}): Feed {
  return {
    date: "2026-07-24",
    n_filings: 1_412,
    n_flagged: 1,
    items: [
      {
        accn: "0001193125-26-000123",
        cik: 320193,
        form: "8-K",
        knowledge_date: "2026-07-24",
        score: 8.5,
        confidence: "CONFESSED",
        confidence_meaning:
          "the filer stated in this document that prior financials should no longer be relied upon",
        findings: [{ flag: "ITEM_4_02", evidence: "Item 4.02 non-reliance on previously issued financial statements" }],
      },
    ],
    ...overrides,
  };
}

export function quality(overrides: Partial<Quality> = {}): Quality {
  return {
    data_vintage: "2026-07-27",
    row_counts: { facts: 13_447_437, filings: 1_364_574, filers: 800 },
    revision_coverage: {
      distinct_periods: 7_133_070,
      periods_with_a_changed_value: 357_842,
    },
    ingest_runs: [{ source: "edgar_bulk", runs: 8, last_started: "2026-07-27T09:12:04" }],
    ...overrides,
  };
}
