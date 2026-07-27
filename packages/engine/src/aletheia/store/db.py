"""DuckDB warehouse: migrations, provenance-aware writes, raw reads.

Two properties this module is responsible for.

**Idempotent ingest.** Every write is ``ON CONFLICT DO NOTHING`` against a real
primary key, and batches are de-duplicated in Python first. Running the same
ingest twice must leave identical row counts — otherwise "re-run the pipeline"
silently changes your data, and every downstream number with it.

**No lossy coercion.** A ``Decimal`` that will not fit the stored scale raises
rather than truncating. A value quietly losing precision on the way into a
warehouse is the kind of defect that surfaces years later as an unexplainable
discrepancy against a custodian.

Reads here are *raw* — they ignore knowledge dates. Feature and research code
must not import this module; it goes through :mod:`aletheia.pit` instead, and
that boundary is enforced by a test.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Context, Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

import duckdb
import pyarrow as pa

from aletheia.core.errors import IntegrityViolation, MigrationError, StoreError
from aletheia.core.hashing import canonical_hash, sha256_bytes
from aletheia.core.types import Fact, Filing, MacroObservation, PriceBar
from aletheia.core.version import code_version
from aletheia.store.records import (
    DelistingRecord,
    DisseminatedFiling,
    EntityRecord,
    IdentifierRecord,
)

MIGRATIONS_DIR: Final = Path(__file__).parent / "migrations"
_MIGRATION_RE: Final = re.compile(r"^(\d{3})_(\w+)\.sql$")
VALUE_SCALE: Final = 10
"""Decimal places stored for `facts.value`. Exceeding it is an error, not a round."""
VALUE_PRECISION: Final = 38
"""Total significant digits in the stored DECIMAL, so 28 digits ahead of the point."""

_VALUE_CONTEXT: Final = Context(prec=VALUE_PRECISION + 2)
"""Decimal's default context carries 28 digits, which is *fewer* than the column
holds. Quantising a large value to 10 decimal places under it raises
``InvalidOperation`` -- and filers do report such values. Advanced Energy
Industries (CIK 927003) tagged ``EntityPublicFloat`` as 2,563,579,586,000,000,000
USD in its 2020 10-K, off by a factor of a billion. That is 19 integer digits;
with 10 decimal places it needs 29, and the ingest died on it after 350
companies. The value fits DECIMAL(38,10) perfectly well; only the arithmetic
context was too small."""

_MAX_ABS_VALUE: Final = Decimal(10) ** (VALUE_PRECISION - VALUE_SCALE)
"""Exclusive bound. Beyond this the column genuinely cannot hold the number, which
is a different problem from the one above and gets its own message."""

_FACT_COLUMNS: Final = (
    "fact_key",
    "cik",
    "taxonomy",
    "concept",
    "unit",
    "period_start",
    "period_end",
    "value",
    "accn",
    "form",
    "filed_at",
    "fy",
    "fp",
    "frame",
    "source_uri",
    "retrieved_at",
    "content_sha256",
    "ingest_run_id",
)

_FACT_SCHEMA: Final = pa.schema(
    [
        ("fact_key", pa.string()),
        ("cik", pa.int64()),
        ("taxonomy", pa.string()),
        ("concept", pa.string()),
        ("unit", pa.string()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("value", pa.decimal128(38, VALUE_SCALE)),
        ("accn", pa.string()),
        ("form", pa.string()),
        ("filed_at", pa.date32()),
        ("fy", pa.int32()),
        ("fp", pa.string()),
        ("frame", pa.string()),
        ("source_uri", pa.string()),
        ("retrieved_at", pa.timestamp("us", tz="UTC")),
        ("content_sha256", pa.string()),
        ("ingest_run_id", pa.string()),
    ]
)


class Warehouse:
    """A DuckDB database file plus the invariants we keep in it."""

    def __init__(self, connection: duckdb.DuckDBPyConnection, *, path: Path) -> None:
        self._con = connection
        self.path = path
        self.current_run_id: str | None = None
        """The run most recently opened, so fetched payloads can be attributed."""
        self._owner_thread = threading.get_ident()
        self._cursors: dict[int, duckdb.DuckDBPyConnection] = {}
        self._cursor_lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle --

    @classmethod
    def open(cls, path: Path, *, read_only: bool = False, migrate: bool = True) -> Self:
        """Open (creating if needed) and bring the schema up to date."""
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = duckdb.connect(str(path), read_only=read_only)
        except duckdb.Error as exc:  # pragma: no cover - environment dependent
            raise StoreError(f"cannot open warehouse at {path}: {exc}") from exc
        warehouse = cls(connection, path=path)
        if migrate:
            if read_only:
                warehouse._assert_schema_current()
            else:
                warehouse.migrate()
        return warehouse

    @classmethod
    def in_memory(cls) -> Self:
        """Ephemeral warehouse for tests. Migrated, never persisted."""
        warehouse = cls(duckdb.connect(":memory:"), path=Path(":memory:"))
        warehouse.migrate()
        return warehouse

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------ migration --

    def migrate(self) -> list[int]:
        """Apply pending migrations in version order; return versions applied.

        Re-verifies the checksum of already-applied migrations. Editing a shipped
        migration is a silent schema divergence between machines, so it is
        refused rather than ignored.
        """
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name VARCHAR NOT NULL,
                checksum VARCHAR NOT NULL, applied_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        applied: dict[int, str] = {
            int(row[0]): str(row[1])
            for row in self._con.execute(
                "SELECT version, checksum FROM schema_migrations"
            ).fetchall()
        }

        newly_applied: list[int] = []
        for version, name, path in _discover_migrations():
            text = path.read_text(encoding="utf-8")
            checksum = sha256_bytes(text.encode("utf-8"))
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationError(
                        f"migration {version:03d}_{name}.sql changed after being applied "
                        f"(stored {applied[version][:12]}, on disk {checksum[:12]}). "
                        f"Add a new migration instead of editing a shipped one."
                    )
                continue
            try:
                self._con.execute("BEGIN TRANSACTION")
                self._con.execute(text)
                self._con.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                    [version, name, checksum, datetime.now(UTC)],
                )
                self._con.execute("COMMIT")
            except duckdb.Error as exc:
                self._con.execute("ROLLBACK")
                raise MigrationError(f"migration {version:03d}_{name} failed: {exc}") from exc
            newly_applied.append(version)
        return newly_applied

    def _assert_schema_current(self) -> None:
        """Refuse to serve reads from a warehouse whose schema is behind the code.

        A read-only handle cannot migrate, so without this check a stale database
        answers queries against views that no longer mean what the code thinks
        they mean — the worst kind of wrong, because it looks like data.
        """
        try:
            applied = {
                int(row[0])
                for row in self._con.execute("SELECT version FROM schema_migrations").fetchall()
            }
        except duckdb.Error:
            applied = set()
        pending = sorted({version for version, _, _ in _discover_migrations()} - applied)
        if pending:
            raise MigrationError(
                f"warehouse at {self.path} is missing migration(s) "
                f"{', '.join(f'{v:03d}' for v in pending)}. "
                f"Open it for writing once (e.g. `aletheia status`) to migrate."
            )

    # ----------------------------------------------------------- provenance --

    def start_run(self, *, source: str, params: dict[str, Any], run_id: str) -> str:
        """Open an ingest run. The run row exists before any data is written.

        The id is also held on the instance so payloads fetched during the run can
        be attributed to it without every layer having to thread it through.
        """
        self.current_run_id = run_id
        self._con.execute(
            """
            INSERT INTO ingest_runs
                (run_id, source, params_hash, params_json, started_at, status, code_version)
            VALUES (?, ?, ?, ?, ?, 'running', ?)
            ON CONFLICT DO NOTHING
            """,
            [
                run_id,
                source,
                canonical_hash(params),
                json.dumps(params, sort_keys=True, default=str),
                datetime.now(UTC),
                code_version(),
            ],
        )
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        rows_written: int = 0,
        bytes_fetched: int = 0,
        error: str | None = None,
    ) -> None:
        """Close an ingest run. Failed runs are recorded, never deleted."""
        if status not in {"ok", "failed"}:
            raise ValueError(f"invalid terminal status: {status!r}")
        self._con.execute(
            """
            UPDATE ingest_runs
               SET finished_at = ?, status = ?, rows_written = ?, bytes_fetched = ?, error = ?
             WHERE run_id = ?
            """,
            [datetime.now(UTC), status, rows_written, bytes_fetched, error, run_id],
        )

    def record_payload(
        self,
        *,
        content_sha256: str,
        source: str,
        source_uri: str,
        retrieved_at: datetime,
        byte_len: int,
        stored_path: Path,
        ingest_run_id: str,
        http_status: int | None = None,
    ) -> bool:
        """Index a fetched payload. Returns False if these exact bytes were seen before."""
        before = self._scalar(
            "SELECT count(*) FROM raw_payloads WHERE content_sha256 = ?", [content_sha256]
        )
        self._con.execute(
            """
            INSERT INTO raw_payloads
                (content_sha256, source, source_uri, retrieved_at, byte_len,
                 http_status, stored_path, ingest_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                content_sha256,
                source,
                source_uri,
                retrieved_at,
                byte_len,
                http_status,
                str(stored_path),
                ingest_run_id,
            ],
        )
        return before == 0

    # --------------------------------------------------------------- writes --

    def write_facts(self, facts: Iterable[Fact]) -> int:
        """Insert facts idempotently. Returns the number of *new* rows."""
        rows = _dedupe(facts, key=lambda f: f.identity())
        if not rows:
            return 0
        table = pa.table(
            {
                "fact_key": [canonical_hash(list(f.identity())) for f in rows],
                "cik": [int(f.cik) for f in rows],
                "taxonomy": [f.taxonomy for f in rows],
                "concept": [f.concept for f in rows],
                "unit": [f.unit for f in rows],
                "period_start": [f.period_start for f in rows],
                "period_end": [f.period_end for f in rows],
                "value": [_scaled(f.value, f) for f in rows],
                "accn": [f.accn.value for f in rows],
                "form": [f.form for f in rows],
                "filed_at": [f.filed_at for f in rows],
                "fy": [f.fy for f in rows],
                "fp": [f.fp for f in rows],
                "frame": [f.frame for f in rows],
                "source_uri": [f.source_uri for f in rows],
                "retrieved_at": [f.retrieved_at for f in rows],
                "content_sha256": [f.content_sha256 for f in rows],
                "ingest_run_id": [f.ingest_run_id for f in rows],
            },
            schema=_FACT_SCHEMA,
        )
        return self._insert_arrow("facts", table, _FACT_COLUMNS)

    def write_filings(self, filings: Iterable[Filing]) -> int:
        """Store filings and link each to the company it was filed under.

        The link is not optional. ``v_company_filings_pit`` inner-joins
        ``filing_filers``, so a filing written here without a link is invisible to
        :meth:`PitView.filings` -- present in the table, absent from every query
        that asks what a company published. Only the daily-index path used to
        populate the relation, which meant an entire company history pulled from
        the submissions endpoint returned nothing.
        """
        rows = _dedupe(filings, key=lambda f: f.accn.value)
        if not rows:
            return 0
        self._link_submissions(rows)
        return self._insert_rows(
            "filings",
            (
                "accn",
                "cik",
                "form",
                "filed_at",
                "accepted_at",
                "period_of_report",
                "primary_document",
                "items",
                "is_xbrl",
                "source_uri",
                "retrieved_at",
                "content_sha256",
                "ingest_run_id",
            ),
            [
                (
                    f.accn.value,
                    int(f.cik),
                    f.form,
                    f.filed_at,
                    f.accepted_at,
                    f.period_of_report,
                    f.primary_document,
                    list(f.items),
                    f.is_xbrl,
                    f.source_uri,
                    f.retrieved_at,
                    f.content_sha256,
                    f.ingest_run_id,
                )
                for f in rows
            ],
        )

    def write_macro(self, observations: Iterable[MacroObservation]) -> int:
        rows = _dedupe(observations, key=lambda o: (o.series_id, o.obs_date, o.realtime_start))
        if not rows:
            return 0
        return self._insert_rows(
            "macro_observations",
            (
                "series_id",
                "obs_date",
                "realtime_start",
                "realtime_end",
                "value",
                "source_uri",
                "retrieved_at",
                "content_sha256",
                "ingest_run_id",
            ),
            [
                (
                    o.series_id,
                    o.obs_date,
                    o.realtime_start,
                    o.realtime_end,
                    o.value,
                    o.source_uri,
                    o.retrieved_at,
                    o.content_sha256,
                    o.ingest_run_id,
                )
                for o in rows
            ],
        )

    def write_prices(self, bars: Iterable[PriceBar]) -> int:
        rows = _dedupe(bars, key=lambda b: (b.symbol, b.bar_date, b.source))
        if not rows:
            return 0
        return self._insert_rows(
            "prices",
            (
                "symbol",
                "bar_date",
                "open",
                "high",
                "low",
                "close",
                "adj_close",
                "volume",
                "source",
                "source_uri",
                "retrieved_at",
                "content_sha256",
                "ingest_run_id",
            ),
            [
                (
                    b.symbol,
                    b.bar_date,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.adj_close,
                    b.volume,
                    b.source,
                    b.source_uri,
                    b.retrieved_at,
                    b.content_sha256,
                    b.ingest_run_id,
                )
                for b in rows
            ],
        )

    def record_dissemination(self, entries: Iterable[DisseminatedFiling]) -> int:
        """Record the date each filing actually appeared in the public feed.

        Inserts a stub row for filings we have not otherwise seen, and back-fills
        ``disseminated_at`` on ones we have. Keeps the *earliest* dissemination
        observed: seeing a filing in two days' feeds does not make it public later
        than the first sighting.
        """
        entries = list(entries)
        self._link_filers(entries)
        rows = _dedupe(entries, key=lambda e: (e.accn.value, e.disseminated_at))
        if not rows:
            return 0
        before = self.count("filings")
        self._con.executemany(
            """
            INSERT INTO filings (accn, cik, form, filed_at, disseminated_at, source_uri,
                                 retrieved_at, content_sha256, ingest_run_id, items, is_xbrl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, [], FALSE)
            ON CONFLICT (accn) DO UPDATE SET
                disseminated_at = least(
                    coalesce(filings.disseminated_at, excluded.disseminated_at),
                    excluded.disseminated_at
                )
            """,
            [
                [
                    e.accn.value,
                    int(e.cik),
                    e.form,
                    e.filed_at,
                    e.disseminated_at,
                    e.source_uri,
                    e.retrieved_at,
                    e.content_sha256,
                    e.ingest_run_id,
                ]
                for e in rows
            ],
        )
        return self.count("filings") - before

    def backfill_filing_filers(self) -> int:
        """Link any filing that has no filer row, using its own ``filings.cik``.

        Repairs warehouses written before :meth:`write_filings` linked filings on
        the submissions path. Idempotent, so running it on a healthy warehouse
        inserts nothing -- which is also how a caller can confirm the problem is
        gone rather than assuming it.
        """
        before = self.count("filing_filers")
        self._con.execute(
            """
            INSERT INTO filing_filers (accn, cik, is_primary, source_uri, retrieved_at,
                                       ingest_run_id)
            SELECT f.accn, f.cik, TRUE, f.source_uri, f.retrieved_at, f.ingest_run_id
              FROM filings AS f
             WHERE NOT EXISTS (
                   SELECT 1 FROM filing_filers AS ff
                    WHERE ff.accn = f.accn AND ff.cik = f.cik
             )
            ON CONFLICT DO NOTHING
            """
        )
        return self.count("filing_filers") - before

    def _link_submissions(self, filings: Sequence[Filing]) -> int:
        """Link filings from a company's own submissions feed to that company.

        The submissions endpoint is per-company, so the company is a filer on
        everything it returns. Co-registrants on the same accession are added
        separately by :meth:`_link_filers` when the dissemination feed shows them;
        ``ON CONFLICT DO NOTHING`` keeps the two paths from fighting.
        """
        if not filings:
            return 0
        before = self.count("filing_filers")
        self._con.executemany(
            """
            INSERT INTO filing_filers (accn, cik, is_primary, source_uri, retrieved_at,
                                       ingest_run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                [
                    filing.accn.value,
                    int(filing.cik),
                    filing.accn.value.startswith(filing.cik.padded),
                    filing.source_uri,
                    filing.retrieved_at,
                    filing.ingest_run_id,
                ]
                for filing in filings
            ],
        )
        return self.count("filing_filers") - before

    def _link_filers(self, entries: Sequence[DisseminatedFiling]) -> int:
        """Record every (filing, company) pair, not just the first company seen.

        801 of one day's 3,168 filings were joint submissions — one of them by
        eight co-registrants. Keeping only the first would answer "did this
        company file anything" with a wrong "no".
        """
        if not entries:
            return 0
        seen: set[tuple[str, int]] = set()
        rows: list[list[Any]] = []
        for entry in entries:
            key = (entry.accn.value, int(entry.cik))
            if key in seen:
                continue
            seen.add(key)
            is_primary = entry.accn.value.startswith(entry.cik.padded)
            rows.append(
                [
                    entry.accn.value,
                    int(entry.cik),
                    is_primary,
                    entry.source_uri,
                    entry.retrieved_at,
                    entry.ingest_run_id,
                ]
            )
        before = self.count("filing_filers")
        self._con.executemany(
            """
            INSERT INTO filing_filers (accn, cik, is_primary, source_uri, retrieved_at, ingest_run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        return self.count("filing_filers") - before

    def write_entity(self, entity: EntityRecord) -> int:
        """Upsert company metadata, widening the observation window.

        ``first_observed``/``last_observed`` bracket when we have actually seen
        this company, which is weaker than "when it existed" — and saying so is
        the point. Claiming coverage we do not have is how a universe silently
        acquires survivorship bias.
        """
        before = self.count("entities")
        self._con.execute(
            """
            INSERT INTO entities (cik, name, entity_type, sic, sic_description, fiscal_year_end,
                                  state_of_incorp, first_observed, last_observed, source_uri,
                                  retrieved_at, content_sha256, ingest_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cik) DO UPDATE SET
                name           = excluded.name,
                entity_type    = excluded.entity_type,
                sic            = excluded.sic,
                sic_description = excluded.sic_description,
                fiscal_year_end = excluded.fiscal_year_end,
                state_of_incorp = excluded.state_of_incorp,
                first_observed = least(entities.first_observed, excluded.first_observed),
                last_observed  = greatest(entities.last_observed, excluded.last_observed),
                source_uri     = excluded.source_uri,
                retrieved_at   = excluded.retrieved_at,
                content_sha256 = excluded.content_sha256,
                ingest_run_id  = excluded.ingest_run_id
            """,
            [
                int(entity.cik),
                entity.name,
                entity.entity_type,
                entity.sic,
                entity.sic_description,
                entity.fiscal_year_end,
                entity.state_of_incorp,
                entity.observed_at,
                entity.observed_at,
                entity.source_uri,
                entity.retrieved_at,
                entity.content_sha256,
                entity.ingest_run_id,
            ],
        )
        return self.count("entities") - before

    def write_identifiers(self, identifiers: Iterable[IdentifierRecord]) -> int:
        rows = _dedupe(identifiers, key=lambda i: (int(i.cik), i.ticker, i.observed_at))
        if not rows:
            return 0
        return self._insert_rows(
            "entity_identifiers",
            (
                "cik",
                "ticker",
                "exchange",
                "observed_at",
                "source_uri",
                "retrieved_at",
                "content_sha256",
                "ingest_run_id",
            ),
            [
                (
                    int(i.cik),
                    i.ticker,
                    i.exchange,
                    i.observed_at,
                    i.source_uri,
                    i.retrieved_at,
                    i.content_sha256,
                    i.ingest_run_id,
                )
                for i in rows
            ],
        )

    def write_delistings(self, delistings: Iterable[DelistingRecord]) -> int:
        """Record names that left an exchange.

        These are the companies whose prices this system cannot obtain. Storing
        them is what turns an invisible hole into a measurable one.
        """
        rows = _dedupe(delistings, key=lambda d: (d.symbol, d.observed_at, d.source))
        if not rows:
            return 0
        return self._insert_rows(
            "delistings",
            (
                "symbol",
                "exchange",
                "company_name",
                "ipo_date",
                "delisted_date",
                "observed_at",
                "source",
                "source_uri",
                "retrieved_at",
                "content_sha256",
                "ingest_run_id",
            ),
            [
                (
                    d.symbol,
                    d.exchange,
                    d.company_name,
                    d.ipo_date,
                    d.delisted_date,
                    d.observed_at,
                    d.source,
                    d.source_uri,
                    d.retrieved_at,
                    d.content_sha256,
                    d.ingest_run_id,
                )
                for d in rows
            ],
        )

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> duckdb.DuckDBPyConnection:
        """Run SQL on a cursor private to the calling thread.

        A DuckDB connection carries **one** result set. Two threads sharing it
        interleave: thread A calls ``execute``, thread B calls ``execute``, and
        A's ``fetchone`` returns B's row. FastAPI runs synchronous endpoints in a
        threadpool, so an ordinary browser loading two pages at once was enough --
        ``/api/quality`` read an accession number where it expected a row count and
        returned a 500.

        ``cursor()`` yields an independent handle onto the same database, cached
        per thread so the cost is paid once rather than per query. Sequential
        callers -- every test, and every ingest -- keep the original connection.
        """
        return self._thread_connection().execute(sql, list(params) if params else None)

    def _thread_connection(self) -> duckdb.DuckDBPyConnection:
        thread_id = threading.get_ident()
        if thread_id == self._owner_thread:
            return self._con
        cursor = self._cursors.get(thread_id)
        if cursor is None:
            with self._cursor_lock:
                cursor = self._con.cursor()
                self._cursors[thread_id] = cursor
        return cursor

    def count(self, table: str) -> int:
        if not table.isidentifier():
            raise ValueError(f"unsafe table name: {table!r}")
        return self._scalar(f"SELECT count(*) FROM {table}")  # noqa: S608 - validated identifier

    # -------------------------------------------------------------- helpers --

    def _insert_arrow(self, table: str, arrow_table: pa.Table, columns: Sequence[str]) -> int:
        before = self.count(table)
        self._con.register("_batch", arrow_table)
        try:
            column_list = ", ".join(columns)
            self._con.execute(
                f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM _batch "  # noqa: S608
                f"ON CONFLICT DO NOTHING"
            )
        finally:
            self._con.unregister("_batch")
        return self.count(table) - before

    def _insert_rows(
        self, table: str, columns: Sequence[str], rows: Sequence[tuple[Any, ...]]
    ) -> int:
        before = self.count(table)
        placeholders = ", ".join("?" for _ in columns)
        column_list = ", ".join(columns)
        self._con.executemany(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT DO NOTHING",
            [list(row) for row in rows],
        )
        return self.count(table) - before

    def _scalar(self, sql: str, params: Sequence[Any] | None = None) -> int:
        result = self._con.execute(sql, list(params) if params else None).fetchone()
        if result is None:  # pragma: no cover - DuckDB always returns a row for aggregates
            raise StoreError(f"query returned no row: {sql}")
        return int(result[0])


def _discover_migrations() -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if match is None:
            raise MigrationError(f"migration filename must be NNN_name.sql: {path.name}")
        found.append((int(match.group(1)), match.group(2), path))
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration versions: {versions}")
    return found


def _dedupe[T](items: Iterable[T], *, key: Any) -> list[T]:
    """Keep the first occurrence of each key, preserving input order.

    Deterministic by construction: the same input sequence always yields the same
    output, which is what makes a re-run byte-identical.
    """
    seen: set[Any] = set()
    unique: list[T] = []
    for item in items:
        identity = key(item)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def _scaled(value: Decimal, fact: Fact) -> Decimal:
    """Fit ``value`` to the stored scale, refusing to lose precision silently.

    An implausible number is still stored. This layer records what was filed; a
    filer that reports a public float a billion times too large has said that, and
    the faithful record is the one that says it too, with the accession number
    attached. Judging plausibility is a separate concern and belongs somewhere it
    can be seen, not in a silent write-time filter.
    """
    if not value.is_finite():
        raise IntegrityViolation(
            f"{fact.concept} for CIK {int(fact.cik)} period ending {fact.period_end} "
            f"is {value}, which is not a number that can be stored or reasoned about"
        )
    if abs(value) >= _MAX_ABS_VALUE:
        raise IntegrityViolation(
            f"{fact.concept} for CIK {int(fact.cik)} period ending {fact.period_end} "
            f"is {value}, too large for DECIMAL({VALUE_PRECISION},{VALUE_SCALE}); "
            f"storing it would truncate the reported number"
        )
    quantized = value.quantize(Decimal(1).scaleb(-VALUE_SCALE), context=_VALUE_CONTEXT)
    if quantized != value:
        raise IntegrityViolation(
            f"{fact.concept} for CIK {int(fact.cik)} period ending {fact.period_end} "
            f"has more than {VALUE_SCALE} decimal places ({value}); storing it would "
            f"silently change the reported number"
        )
    return quantized


__all__ = ["VALUE_PRECISION", "VALUE_SCALE", "Warehouse"]
