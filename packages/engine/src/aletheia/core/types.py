"""Domain types.

The two dates on every fact are the point of this module. ``period_end`` says
*what the number describes*; ``filed_at`` says *when it became knowable*. Vendor
panels carry only the first, which is precisely why they cannot answer "what did
I know on date D".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final, Self

_ACCN_RE: Final = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ACCN_BARE_RE: Final = re.compile(r"^\d{18}$")

# Forms whose facts we treat as periodic financial statements.
PERIODIC_FORMS: Final = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F"})


class Cik(int):
    """SEC Central Index Key. An int, but one that formats itself correctly.

    The SEC's URLs demand a zero-padded 10-digit form while its JSON payloads use
    a bare integer; conflating the two is the classic EDGAR client bug.
    """

    __slots__ = ()

    def __new__(cls, value: int | str) -> Self:
        number = int(value)
        if not 0 < number < 10**10:
            raise ValueError(f"CIK out of range: {value!r}")
        return super().__new__(cls, number)

    @property
    def padded(self) -> str:
        """Zero-padded 10-digit form, as used in EDGAR paths."""
        return f"{int(self):010d}"

    def __repr__(self) -> str:
        return f"Cik({int(self)})"


@dataclass(frozen=True, slots=True, order=True)
class Accession:
    """An SEC accession number — the identity of a single filing.

    This is what makes restatement detection possible: two filings reporting the
    same period carry different accession numbers, so "the value changed" and
    "we re-read the same filing" are distinguishable facts.
    """

    value: str

    def __post_init__(self) -> None:
        if not _ACCN_RE.match(self.value):
            raise ValueError(
                f"malformed accession number: {self.value!r} (want 0000320193-18-000145)"
            )

    @classmethod
    def parse(cls, raw: str) -> Accession:
        """Accept either dashed or bare 18-digit form."""
        text = raw.strip()
        if _ACCN_BARE_RE.match(text):
            text = f"{text[:10]}-{text[10:12]}-{text[12:]}"
        return cls(text)

    @property
    def bare(self) -> str:
        """18-digit form, as used in EDGAR archive directory paths."""
        return self.value.replace("-", "")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Fact:
    """One reported number, with both of its dates and its full provenance.

    ``value`` is ``Decimal``: these are reported financial statement figures, and
    binary floating point cannot represent them exactly.
    """

    cik: Cik
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None  # None for instantaneous facts (balance-sheet items)
    period_end: date
    value: Decimal
    accn: Accession
    form: str
    filed_at: date  # THE KNOWLEDGE DATE
    fy: int | None
    fp: str | None
    frame: str | None
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str

    @property
    def is_instantaneous(self) -> bool:
        """True for point-in-time measures (assets, shares outstanding)."""
        return self.period_start is None

    @property
    def filing_lag_days(self) -> int:
        """Calendar days between the end of the period and its publication.

        An unusually long lag relative to a firm's own history is one of the
        signals in the library — late filings precede bad news.
        """
        return (self.filed_at - self.period_end).days

    def identity(self) -> tuple[int, str, str, str, str, str, str]:
        """The tuple that makes this fact unique within the warehouse.

        Includes ``accn``: the same period reported by a *different* filing is a
        different fact, not a duplicate. That is what a restatement is.
        """
        return (
            int(self.cik),
            self.taxonomy,
            self.concept,
            self.unit,
            self.period_start.isoformat() if self.period_start else "",
            self.period_end.isoformat(),
            self.accn.value,
        )


@dataclass(frozen=True, slots=True)
class Filing:
    """Filing-level metadata, independent of any XBRL facts inside it."""

    accn: Accession
    cik: Cik
    form: str
    filed_at: date
    accepted_at: datetime | None
    period_of_report: date | None
    primary_document: str | None
    items: tuple[str, ...]  # 8-K item codes, e.g. ("4.02", "2.02")
    is_xbrl: bool
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str

    @property
    def has_non_reliance_item(self) -> bool:
        """Item 4.02 — "non-reliance on previously issued financial statements".

        The single most severe accounting red flag a filing can carry: management
        is stating that numbers already published should not be relied upon.
        """
        return any(item.startswith("4.02") for item in self.items)


@dataclass(frozen=True, slots=True)
class MacroObservation:
    """A macro datapoint *as published in a specific vintage*.

    ``realtime_start`` is the knowledge date: the day this value for this
    observation date became public. A series revised three times has three rows
    for the same ``obs_date``, which is the only way to backtest macro honestly.
    """

    series_id: str
    obs_date: date
    value: float | None  # None encodes a genuinely missing observation ('.') in FRED
    realtime_start: date  # KNOWLEDGE DATE
    realtime_end: date
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One daily bar.

    Prices carry no filing date, so their knowledge date is derived: a bar dated
    ``D`` is knowable at ``D``'s close. Research must therefore state its
    execution lag explicitly — the backtest kernel refuses to default it.

    ``adj_close`` is dividend- and split-adjusted; ``close`` is as-traded. Both
    are kept because the adjusted series is rebased by the vendor on every pull
    and is therefore not comparable across pulls, while ``close`` is stable.
    """

    symbol: str
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float | None
    volume: float
    source: str
    source_uri: str
    retrieved_at: datetime
    content_sha256: str
    ingest_run_id: str
