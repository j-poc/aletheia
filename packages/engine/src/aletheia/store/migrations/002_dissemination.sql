-- Migration 002: separate "filed" from "became public".
--
-- Found by a live contract test, 2026-07-27. EDGAR's daily index is a
-- *dissemination* feed, not a filing-date feed. Of the 4,005 entries disseminated
-- on 2026-07-24, 123 (3.1%) carried an earlier filing date -- the oldest by
-- eleven months (a filing dated 2025-08-27).
--
-- That distinction is a point-in-time correctness issue, not a curiosity. Treating
-- `filed_at` as the knowledge date for one of those stragglers asserts we knew its
-- contents eleven months before anyone could read them. Small in count, but it is
-- exactly the class of error this system exists to prevent, and it would be
-- invisible in any dataset that carries only one date.
--
-- The knowledge date is therefore max(filed_at, disseminated_at).
--
-- Coverage, stated plainly: `disseminated_at` is NULL for every filing learned
-- from the submissions API, because that endpoint does not report it. For those
-- rows the PIT layer falls back to filed_at -- the same assumption every vendor
-- makes silently, but here it is visible, and it stops being an assumption for
-- everything captured forward from today via the daily index.

ALTER TABLE filings ADD COLUMN IF NOT EXISTS disseminated_at DATE;

COMMENT ON COLUMN filings.disseminated_at IS
    'Date the filing appeared in the EDGAR dissemination feed. NULL when unknown '
    '(submissions API does not report it). Knowledge date = max(filed_at, coalesce(disseminated_at, filed_at)).';

CREATE INDEX IF NOT EXISTS ix_filings_disseminated ON filings (disseminated_at);

-- Every filing with the knowledge date resolved, so no research query has to
-- remember the rule. This is the only view research code should read filings from.
CREATE OR REPLACE VIEW v_filings_pit AS
SELECT
    *,
    greatest(filed_at, coalesce(disseminated_at, filed_at)) AS knowledge_date,
    (disseminated_at IS NOT NULL AND disseminated_at > filed_at) AS was_disseminated_late,
    coalesce(date_diff('day', filed_at, disseminated_at), 0)   AS dissemination_lag_days
FROM filings;
