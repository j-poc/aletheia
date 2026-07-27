-- ALETHEIA warehouse, migration 001.
--
-- Design rule running through every table: no row exists without provenance.
-- Each carries source_uri, retrieved_at, content_sha256 and ingest_run_id, so any
-- number in a client-facing result can be walked back to the exact bytes it came
-- from and the run that wrote it. A row that cannot answer "where did you come
-- from" is indistinguishable from a fabricated one.

-- ---------------------------------------------------------------- lineage ---

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER      PRIMARY KEY,
    name        VARCHAR      NOT NULL,
    checksum    VARCHAR      NOT NULL,   -- sha256 of the migration text as applied
    applied_at  TIMESTAMPTZ  NOT NULL
);

-- One row per ingest attempt, successful or not. Failed runs are kept: a source
-- that starts failing is itself a finding, and deleting the evidence hides it.
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id        VARCHAR      PRIMARY KEY,
    source        VARCHAR      NOT NULL,
    params_hash   VARCHAR      NOT NULL,   -- canonical hash of the run parameters
    params_json   VARCHAR      NOT NULL,
    started_at    TIMESTAMPTZ  NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        VARCHAR      NOT NULL,   -- running | ok | failed
    rows_written  BIGINT       NOT NULL DEFAULT 0,
    bytes_fetched BIGINT       NOT NULL DEFAULT 0,
    code_version  VARCHAR      NOT NULL,   -- git describe of the engine at run time
    error         VARCHAR
);

-- Content-addressed record of every payload fetched. The bytes live on disk under
-- data/raw/<sha256 prefix>/<sha256>; this table is the index. Because the key is
-- the hash, re-fetching identical bytes is a no-op, and a source that silently
-- changes its answer for the same URI produces a *new* row rather than an
-- overwrite -- which is how you find out it happened.
CREATE TABLE IF NOT EXISTS raw_payloads (
    content_sha256 VARCHAR      PRIMARY KEY,
    source         VARCHAR      NOT NULL,
    source_uri     VARCHAR      NOT NULL,
    retrieved_at   TIMESTAMPTZ  NOT NULL,
    byte_len       BIGINT       NOT NULL,
    http_status    INTEGER,
    stored_path    VARCHAR      NOT NULL,
    ingest_run_id  VARCHAR      NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_raw_payloads_uri ON raw_payloads (source_uri);

-- --------------------------------------------------------------- entities ---

CREATE TABLE IF NOT EXISTS entities (
    cik              BIGINT      PRIMARY KEY,
    name             VARCHAR     NOT NULL,
    entity_type      VARCHAR,
    sic              VARCHAR,
    sic_description  VARCHAR,
    fiscal_year_end  VARCHAR,    -- 'MMDD' as EDGAR reports it
    state_of_incorp  VARCHAR,
    first_observed   DATE        NOT NULL,
    last_observed    DATE        NOT NULL,
    source_uri       VARCHAR     NOT NULL,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    content_sha256   VARCHAR     NOT NULL,
    ingest_run_id    VARCHAR     NOT NULL
);

-- Ticker<->CIK mapping is time-varying and the SEC publishes only a *current*
-- snapshot. We therefore record what we observed and when, rather than pretending
-- to know the historical map. Going forward this accumulates into a real history;
-- backwards it is honestly incomplete, and research that depends on it must say so.
CREATE TABLE IF NOT EXISTS entity_identifiers (
    cik            BIGINT       NOT NULL,
    ticker         VARCHAR      NOT NULL,
    exchange       VARCHAR,
    observed_at    DATE         NOT NULL,
    source_uri     VARCHAR      NOT NULL,
    retrieved_at   TIMESTAMPTZ  NOT NULL,
    content_sha256 VARCHAR      NOT NULL,
    ingest_run_id  VARCHAR      NOT NULL,
    PRIMARY KEY (cik, ticker, observed_at)
);

-- ---------------------------------------------------------------- filings ---

CREATE TABLE IF NOT EXISTS filings (
    accn             VARCHAR      PRIMARY KEY,
    cik              BIGINT       NOT NULL,
    form             VARCHAR      NOT NULL,
    filed_at         DATE         NOT NULL,   -- KNOWLEDGE DATE
    accepted_at      TIMESTAMPTZ,             -- exact acceptance instant when known
    period_of_report DATE,
    primary_document VARCHAR,
    items            VARCHAR[],               -- 8-K item codes, e.g. ['4.02','2.02']
    is_xbrl          BOOLEAN      NOT NULL DEFAULT FALSE,
    source_uri       VARCHAR      NOT NULL,
    retrieved_at     TIMESTAMPTZ  NOT NULL,
    content_sha256   VARCHAR      NOT NULL,
    ingest_run_id    VARCHAR      NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_filings_cik_filed ON filings (cik, filed_at);
CREATE INDEX IF NOT EXISTS ix_filings_filed     ON filings (filed_at);

-- ------------------------------------------------------------------ facts ---

-- The bitemporal core. Two dates, always:
--   period_end  -- what the number describes
--   filed_at    -- when it became knowable        <- the column vendors drop
--
-- accn is part of the identity, NOT metadata. Two rows for the same period with
-- different accn and different value is not a duplicate to be deduplicated; it is
-- a restatement, and it is the most interesting thing in this table.
--
-- value is DECIMAL, never DOUBLE: these are reported financial statement figures.
CREATE TABLE IF NOT EXISTS facts (
    fact_key       VARCHAR        PRIMARY KEY,   -- canonical hash of the identity tuple
    cik            BIGINT         NOT NULL,
    taxonomy       VARCHAR        NOT NULL,
    concept        VARCHAR        NOT NULL,
    unit           VARCHAR        NOT NULL,
    period_start   DATE,                         -- NULL for instantaneous facts
    period_end     DATE           NOT NULL,
    value          DECIMAL(38,10) NOT NULL,
    accn           VARCHAR        NOT NULL,
    form           VARCHAR        NOT NULL,
    filed_at       DATE           NOT NULL,      -- KNOWLEDGE DATE
    fy             INTEGER,
    fp             VARCHAR,
    frame          VARCHAR,
    source_uri     VARCHAR        NOT NULL,
    retrieved_at   TIMESTAMPTZ    NOT NULL,
    content_sha256 VARCHAR        NOT NULL,
    ingest_run_id  VARCHAR        NOT NULL
);

-- The composite index that makes as-of queries fast: every PIT read filters on
-- (cik, concept) then bounds filed_at.
CREATE INDEX IF NOT EXISTS ix_facts_lookup ON facts (cik, taxonomy, concept, unit, filed_at);
CREATE INDEX IF NOT EXISTS ix_facts_period ON facts (cik, concept, period_end);
CREATE INDEX IF NOT EXISTS ix_facts_filed  ON facts (filed_at);

-- ------------------------------------------------------------------ macro ---

-- ALFRED vintages. realtime_start is the knowledge date: the day this value for
-- this observation date became public. A series revised three times has three
-- rows for one obs_date -- which is exactly what makes macro backtestable.
CREATE TABLE IF NOT EXISTS macro_observations (
    series_id      VARCHAR      NOT NULL,
    obs_date       DATE         NOT NULL,
    realtime_start DATE         NOT NULL,   -- KNOWLEDGE DATE
    realtime_end   DATE         NOT NULL,
    value          DOUBLE,                  -- NULL encodes FRED's '.' missing marker
    source_uri     VARCHAR      NOT NULL,
    retrieved_at   TIMESTAMPTZ  NOT NULL,
    content_sha256 VARCHAR      NOT NULL,
    ingest_run_id  VARCHAR      NOT NULL,
    PRIMARY KEY (series_id, obs_date, realtime_start)
);

CREATE INDEX IF NOT EXISTS ix_macro_rt ON macro_observations (series_id, realtime_start);

-- ----------------------------------------------------------------- prices ---

-- No filing date exists for a price, so the knowledge date is derived: a bar dated
-- D is knowable at D's close. Research must state its execution lag explicitly;
-- the backtest kernel refuses to assume one.
--
-- close is as-traded and stable across pulls. adj_close is vendor-rebased on every
-- pull and therefore NOT comparable between pulls -- both are stored so that a
-- return computed within one pull is correct and the trap is visible.
CREATE TABLE IF NOT EXISTS prices (
    symbol         VARCHAR      NOT NULL,
    bar_date       DATE         NOT NULL,
    open           DOUBLE       NOT NULL,
    high           DOUBLE       NOT NULL,
    low            DOUBLE       NOT NULL,
    close          DOUBLE       NOT NULL,
    adj_close      DOUBLE,
    volume         DOUBLE       NOT NULL,
    source         VARCHAR      NOT NULL,
    source_uri     VARCHAR      NOT NULL,
    retrieved_at   TIMESTAMPTZ  NOT NULL,
    content_sha256 VARCHAR      NOT NULL,
    ingest_run_id  VARCHAR      NOT NULL,
    PRIMARY KEY (symbol, bar_date, source)
);

CREATE INDEX IF NOT EXISTS ix_prices_date ON prices (bar_date);

-- Names known to have left an exchange. We cannot obtain their price history on
-- the current entitlement, so this table's job is to make the resulting
-- survivorship exposure *measurable* rather than invisible: every backtest
-- reports how many universe members it could not price and what weight they were.
CREATE TABLE IF NOT EXISTS delistings (
    symbol         VARCHAR      NOT NULL,
    exchange       VARCHAR,
    company_name   VARCHAR,
    ipo_date       DATE,
    delisted_date  DATE,
    observed_at    DATE         NOT NULL,
    source         VARCHAR      NOT NULL,
    source_uri     VARCHAR      NOT NULL,
    retrieved_at   TIMESTAMPTZ  NOT NULL,
    content_sha256 VARCHAR      NOT NULL,
    ingest_run_id  VARCHAR      NOT NULL,
    PRIMARY KEY (symbol, observed_at, source)
);

-- ------------------------------------------------------------------ views ---

-- Every value change for a (company, concept, period) across successive filings.
-- prior_value IS NULL marks the first report; a non-null prior_value that differs
-- from value is a restatement. This view is the raw material for the flagship
-- study and cannot be constructed at all from a flat vendor panel.
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
    value,
    LAG(value)    OVER w AS prior_value,
    LAG(filed_at) OVER w AS prior_filed_at,
    LAG(accn)     OVER w AS prior_accn,
    ROW_NUMBER()  OVER w AS report_seq
FROM facts
WINDOW w AS (
    PARTITION BY cik, taxonomy, concept, unit, period_start, period_end
    ORDER BY filed_at, accn
);
