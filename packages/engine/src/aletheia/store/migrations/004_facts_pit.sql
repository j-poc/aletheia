-- Migration 004: the knowledge date of a fact is the knowledge date of the
-- filing that published it.
--
-- `facts.filed_at` comes from EDGAR's XBRL payload. `filings.disseminated_at`
-- comes from the dissemination feed. When they disagree, the later one wins:
-- a number inside a filing cannot be known before the filing itself is public.
--
-- `report_seq` numbers each report of a (company, concept, unit, period) in
-- publication order, so "first reported" and "as restated" are both addressable
-- without a second query. That ordering is over ALL filings, not just the ones
-- before a given knowledge date -- point-in-time filtering happens in the query
-- layer, deliberately, so this view stays a plain description of the data.

CREATE OR REPLACE VIEW v_facts_pit AS
SELECT
    f.fact_key,
    f.cik,
    f.taxonomy,
    f.concept,
    f.unit,
    f.period_start,
    f.period_end,
    f.value,
    f.accn,
    f.form,
    f.filed_at,
    greatest(f.filed_at, coalesce(fl.disseminated_at, f.filed_at)) AS knowledge_date,
    f.fy,
    f.fp,
    f.frame,
    f.source_uri,
    f.content_sha256,
    f.ingest_run_id,
    ROW_NUMBER() OVER (
        PARTITION BY f.cik, f.taxonomy, f.concept, f.unit, f.period_start, f.period_end
        ORDER BY greatest(f.filed_at, coalesce(fl.disseminated_at, f.filed_at)), f.accn
    ) AS report_seq
FROM facts AS f
LEFT JOIN filings AS fl ON fl.accn = f.accn;
