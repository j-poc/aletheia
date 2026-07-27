"""Composition root.

The only place that reads the environment, constructs a real clock, or opens a
network connection. Everything below this module receives its dependencies, which
is what makes the rest of the system testable without mocks and deterministic
without patching.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from aletheia.core.clock import Clock, SystemClock
from aletheia.core.config import Settings, load_settings
from aletheia.ingest import Ingestor
from aletheia.provenance.payloads import PayloadStore, StoredPayload
from aletheia.sources.edgar import EdgarClient
from aletheia.sources.fred import FredClient
from aletheia.sources.http import Fetcher
from aletheia.sources.prices import FmpPriceSource
from aletheia.store.db import Warehouse


@dataclass
class Application:
    """Wired-up engine. Use as a context manager."""

    settings: Settings
    clock: Clock
    warehouse: Warehouse
    fetcher: Fetcher
    edgar: EdgarClient
    fred: FredClient | None
    prices: FmpPriceSource | None
    ingestor: Ingestor
    _stack: ExitStack

    @classmethod
    def build(
        cls,
        *,
        data_dir: Path | None = None,
        clock: Clock | None = None,
        read_only: bool = False,
    ) -> Self:
        settings = load_settings(data_dir=data_dir)
        resolved_clock = clock or SystemClock()
        stack = ExitStack()

        warehouse = stack.enter_context(
            Warehouse.open(settings.warehouse_path, read_only=read_only)
        )
        payloads = PayloadStore(settings.raw_dir)

        def index_payload(source: str, stored: StoredPayload) -> None:
            """Record every fetched payload in the provenance ledger.

            Wired here rather than inside Fetcher so the HTTP layer keeps no
            reference to the warehouse, and rather than at each ingest call site
            because doing it per-source is how the ledger came to hold one row
            against several thousand files.
            """
            warehouse.record_payload(
                content_sha256=stored.content_sha256,
                source=source,
                source_uri=stored.source_uri,
                retrieved_at=stored.retrieved_at,
                byte_len=stored.byte_len,
                stored_path=stored.path,
                ingest_run_id=warehouse.current_run_id or "unattributed",
                http_status=stored.http_status,
            )

        fetcher = stack.enter_context(
            Fetcher(
                payloads=payloads,
                clock=resolved_clock,
                on_stored=index_payload,
                user_agent=settings.sec_user_agent,
                rate_per_second=settings.sec_rate_limit_per_sec,
                timeout_seconds=settings.http_timeout_seconds,
                max_attempts=settings.max_retries,
            )
        )

        edgar = EdgarClient(fetcher)
        # Optional sources stay None when unconfigured rather than failing at
        # startup: an EDGAR-only run is legitimate and should not require an FMP
        # key to exist.
        fred = FredClient(fetcher, api_key=settings.fred_api_key) if settings.fred_api_key else None
        prices = (
            FmpPriceSource(fetcher, api_key=settings.fmp_api_key) if settings.fmp_api_key else None
        )

        return cls(
            settings=settings,
            clock=resolved_clock,
            warehouse=warehouse,
            fetcher=fetcher,
            edgar=edgar,
            fred=fred,
            prices=prices,
            ingestor=Ingestor(
                settings=settings,
                warehouse=warehouse,
                clock=resolved_clock,
                edgar=edgar,
                fred=fred,
                prices=prices,
            ),
            _stack=stack,
        )

    def close(self) -> None:
        self._stack.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
