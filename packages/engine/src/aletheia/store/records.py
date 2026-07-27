"""Storage-side records that have no natural home in the domain types.

These describe things we *observed* rather than things that are true: a company's
metadata as of the day we read it, a ticker mapping as of a snapshot, a delisting
as reported by a vendor. Keeping the observation date on each one is what stops
"what we happened to see" from being mistaken for "what was the case".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aletheia.core.types import Accession, Cik


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """Company metadata as observed on ``observed_at``."""

    cik: Cik
    name: str
    entity_type: str | None
    sic: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_incorp: str | None
    observed_at: date
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str


@dataclass(frozen=True, slots=True)
class IdentifierRecord:
    """A ticker↔CIK observation.

    Deliberately not called a "mapping": the SEC publishes only a current
    snapshot, so this is what was true on ``observed_at`` and nothing more.
    """

    cik: Cik
    ticker: str
    exchange: str | None
    observed_at: date
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str


@dataclass(frozen=True, slots=True)
class DisseminatedFiling:
    """A filing seen in the public dissemination feed on ``disseminated_at``.

    Distinct from ``filed_at``: 3.1% of a sampled day's feed carried an earlier
    filing date, the oldest by eleven months. The knowledge date is the later of
    the two — see migration 002.
    """

    accn: Accession
    cik: Cik
    form: str
    filed_at: date
    disseminated_at: date
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str


@dataclass(frozen=True, slots=True)
class DelistingRecord:
    """A name that left an exchange, as reported by a vendor."""

    symbol: str
    exchange: str | None
    company_name: str | None
    ipo_date: date | None
    delisted_date: date | None
    observed_at: date
    source: str
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str
