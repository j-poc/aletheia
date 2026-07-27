-- Migration 006: there is one definition of publication order, and every view
-- that needs it reads that one.
--
-- `v_fact_revisions` (migration 001) predates `v_facts_pit` (004) and predates
-- the filed-vs-disseminated distinction (002). It built its own window straight
-- off `facts` with `ORDER BY filed_at, accn`, so two views in the same schema
-- disagreed about which report came first, and therefore about which value a
-- revision revised. Nothing in production read it -- `PitView.revisions()`
-- reimplemented the same LAG inline over `v_facts_pit`, correctly -- so the
-- disagreement was invisible: one test held the stale definition in place and
-- the correct one was maintained separately a few files away.
--
-- Two identical questions answered by two hand-written windows is the shape of
-- the D11-D14 defect family, one layer down. Redefining the view *on top of*
-- `v_facts_pit` makes the divergence structurally impossible rather than
-- currently-absent: ordering, dissemination lag, `report_seq`,
-- `differs_from_first_report` and `period_distinct_values` are all inherited,
-- so a future correction to publication order lands in one place.
--
-- Why it matters, in the data: of the 3,168 filings captured from the
-- dissemination feed so far, 122 became public later than their filing date --
-- one draft registration statement by 331 days. None of those carry XBRL facts
-- *yet*, so the two orderings currently agree on every row in this warehouse.
-- That is a fact about today's coverage, not a property of the schema: forward
-- capture adds dissemination dates continuously, and the first late-disseminated
-- filing that restates a period would have been ordered wrongly here while
-- looking perfectly correct in `v_facts_pit`.
--
-- Contract, unchanged from 001 and from 004: the window covers ALL filings, not
-- the ones before some knowledge date. Point-in-time filtering stays in the
-- query layer. `prior_value IS NULL` still marks the first report.

CREATE OR REPLACE VIEW v_fact_revisions AS
SELECT
    cik,
    taxonomy,
    concept,
    unit,
    period_start,
    period_end,
    accn,
    form,
    filed_at,
    knowledge_date,
    value,
    report_seq,
    differs_from_first_report,
    period_distinct_values,
    LAG(value)          OVER w AS prior_value,
    LAG(knowledge_date) OVER w AS prior_knowledge_date,
    LAG(filed_at)       OVER w AS prior_filed_at,
    LAG(accn)           OVER w AS prior_accn
FROM v_facts_pit
-- Identical to the `w_ordered` window in 005, and deliberately expressed in the
-- same terms: `knowledge_date` is already `greatest(filed_at, disseminated_at)`
-- by the time it reaches here, so ordering by it cannot drift from the ordering
-- `report_seq` was numbered with.
WINDOW w AS (
    PARTITION BY cik, taxonomy, concept, unit, period_start, period_end
    ORDER BY knowledge_date, accn
);
