-- Migration 005: whether a fact restates its period is a property of the
-- value, not of where the fact sits in the filing sequence.
--
-- `report_seq` counts publications. A 10-Q that carries the prior year's
-- balance sheet forward unchanged gets seq 2, so every consumer that read
-- `report_seq > 1` as "restated" said so on 6,314,367 rows of this warehouse
-- and was wrong on 5,798,180 of them -- 91.8% figures that never moved.
-- `differs_from_first_report` is the question those consumers meant to ask.
--
-- `period_distinct_values` answers a second one they also needed. A period can
-- be revised and then revised back, and code that compares the two ends of the
-- chain reads A -> B -> A as though nothing ever happened: AAR Corp's accrued
-- current liabilities for 2021-05-31 went 174.2m -> 148.3m -> 174.2m across
-- five filings, and first-versus-latest sees no change at all. 10,080 of the
-- 357,101 revised us-gaap periods here do this. Counting distinct values over
-- the whole partition is the only form of the question that survives it.
--
-- Both columns describe the complete chain, not the chain up to some knowledge
-- date -- the same contract `report_seq` keeps. Point-in-time filtering stays
-- in the query layer, deliberately, so this view remains a plain description
-- of the data.

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
    ROW_NUMBER() OVER w_ordered AS report_seq,
    -- IS DISTINCT FROM, not <>: a NULL value would make `<>` NULL, and a NULL
    -- boolean read as "not restated" is the failure this migration exists to
    -- stop, not one to reintroduce one column over.
    (f.value IS DISTINCT FROM first_value(f.value) OVER w_ordered)
        AS differs_from_first_report,
    count(DISTINCT f.value) OVER w_partition AS period_distinct_values
FROM facts AS f
LEFT JOIN filings AS fl ON fl.accn = f.accn
WINDOW
    w_ordered AS (
        PARTITION BY f.cik, f.taxonomy, f.concept, f.unit, f.period_start, f.period_end
        ORDER BY greatest(f.filed_at, coalesce(fl.disseminated_at, f.filed_at)), f.accn
    ),
    -- No ORDER BY. An ordered window would make the count cumulative, which is
    -- "how many values had appeared by this row" -- a different question, and
    -- one that answers 1 on the first row of every revised period.
    w_partition AS (
        PARTITION BY f.cik, f.taxonomy, f.concept, f.unit, f.period_start, f.period_end
    );
