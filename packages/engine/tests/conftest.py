"""Shared fixtures.

Everything here is deterministic: a frozen clock, a fixed retrieval instant, an
in-memory warehouse. No test reads the wall clock or the developer's shell, so a
suite that passes in July passes in December on someone else's machine.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from aletheia.core.clock import FrozenClock
from aletheia.provenance.payloads import PayloadStore
from aletheia.store.db import Warehouse
from tests._factories import RETRIEVED_AT, RUN_ID


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(RETRIEVED_AT)


@pytest.fixture
def warehouse() -> Iterator[Warehouse]:
    """In-memory warehouse with an open ingest run, ready to accept writes."""
    with Warehouse.in_memory() as store:
        store.start_run(source="test", params={"fixture": True}, run_id=RUN_ID)
        yield store


@pytest.fixture
def tmp_warehouse(tmp_path: Path) -> Iterator[Warehouse]:
    """On-disk warehouse, for anything that must survive a reopen."""
    with Warehouse.open(tmp_path / "warehouse.duckdb") as store:
        store.start_run(source="test", params={"fixture": True}, run_id=RUN_ID)
        yield store


@pytest.fixture
def payloads(tmp_path: Path) -> PayloadStore:
    return PayloadStore(tmp_path / "raw")
