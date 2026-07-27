-- Migration 003: a filing can have more than one filer.
--
-- Found while reconciling row counts, 2026-07-27. The 2026-07-24 dissemination
-- feed holds 4,005 rows but only 3,168 distinct accession numbers: 801 filings
-- are made jointly, one appearing under eight separate CIKs. Registration
-- statements with co-registrants, joint 8-Ks and 13D/G groups all do this.
--
-- `filings` is keyed by accession, which is correct -- one filing, one row. But
-- keeping only the first CIK seen quietly breaks the question research actually
-- asks: "did company X file anything on date D". For a co-registrant that answer
-- would have been no, wrongly, and nothing downstream could tell.

CREATE TABLE IF NOT EXISTS filing_filers (
    accn           VARCHAR      NOT NULL,
    cik            BIGINT       NOT NULL,
    is_primary     BOOLEAN      NOT NULL DEFAULT FALSE,
    source_uri     VARCHAR      NOT NULL,
    retrieved_at   TIMESTAMPTZ  NOT NULL,
    ingest_run_id  VARCHAR      NOT NULL,
    PRIMARY KEY (accn, cik)
);

CREATE INDEX IF NOT EXISTS ix_filing_filers_cik ON filing_filers (cik);

-- Every (company, filing) pair with the knowledge date already resolved. This is
-- the relation research should read: it answers "what did company X publish, and
-- when did it become public" without needing to know either subtlety.
CREATE OR REPLACE VIEW v_company_filings_pit AS
SELECT
    ff.cik,
    f.accn,
    f.form,
    f.filed_at,
    f.disseminated_at,
    greatest(f.filed_at, coalesce(f.disseminated_at, f.filed_at)) AS knowledge_date,
    f.period_of_report,
    f.items,
    f.is_xbrl,
    ff.is_primary
FROM filings AS f
JOIN filing_filers AS ff ON ff.accn = f.accn;
