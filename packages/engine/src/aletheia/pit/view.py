"""Point-in-time access — the only door between stored data and research.

Everything this system claims rests on one guarantee: **a research routine cannot
read a number that was not public on the date it is simulating.** That guarantee
is enforced in three independent ways, because one mechanism is a promise and
three is a design:

1. **Filtered at the source.** Every query bounds ``knowledge_date <= as_of``.
2. **Checked on the way out.** Every row is re-inspected before it is returned;
   a value that slipped past the filter raises :class:`LookaheadViolation`. This
   catches the realistic failure — a hand-written SQL predicate that is subtly
   wrong — which the filter alone cannot.
3. **Unreachable by accident.** ``features/``, ``research/`` and ``book/`` may not
   import :mod:`aletheia.store`; a test walks their imports and fails if they do.
   The only way to reach data is through this module.

Reading the future is still *possible* — a backtest that needs to know how a
period was eventually restated is a legitimate research question. It is spelled
``unsafe_latest_restated``, so it is greppable, and it can never be typed by
accident.

**Default semantics.** ``as_of(d).value(...)`` returns the most recent report
published on or before ``d`` — what a practitioner would have had in front of
them on that date. For Apple's FY2008 diluted EPS that is 5.36 as of 2009-12-01
and 6.78 as of 2010-06-01, because the restatement was published in between.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

from aletheia.core.errors import AmbiguousPeriod, InsufficientData, LookaheadViolation
from aletheia.core.types import Accession, Cik
from aletheia.store.db import Warehouse

DEFAULT_TAXONOMY: Final = "us-gaap"


@dataclass(frozen=True, slots=True)
class PitFact:
    """A reported number together with when it became knowable."""

    cik: Cik
    taxonomy: str
    concept: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accn: Accession
    form: str
    filed_at: date
    knowledge_date: date
    report_seq: int
    """1 for the first publication of this period; 2+ for each restatement."""
    source_uri: str
    content_sha256: str

    @property
    def is_first_report(self) -> bool:
        return self.report_seq == 1

    @property
    def age_days_at(self) -> int:
        """Days between the end of the period and its publication."""
        return (self.knowledge_date - self.period_end).days


@dataclass(frozen=True, slots=True)
class PitFiling:
    """A filing, dated by when it actually became public."""

    accn: Accession
    cik: Cik
    form: str
    filed_at: date
    disseminated_at: date | None
    knowledge_date: date
    period_of_report: date | None
    items: tuple[str, ...]

    @property
    def has_non_reliance_item(self) -> bool:
        """8-K Item 4.02: previously issued financials should not be relied upon."""
        return any(item.startswith("4.02") for item in self.items)


@dataclass(frozen=True, slots=True)
class PitPrice:
    """A daily bar with the date it could first be acted on.

    ``tradable_from`` is derived from an execution lag the caller must state.
    There is no default: assuming same-close execution is the most common way a
    backtest quietly buys at a price it could not have got.
    """

    symbol: str
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float | None
    volume: float
    tradable_from: date


@dataclass(frozen=True, slots=True)
class PitEntity:
    """Registrant metadata as recorded at ingest."""

    cik: Cik
    name: str
    sic: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    """'MMDD' as EDGAR reports it."""

    @property
    def sic_major_group(self) -> int | None:
        """The first two digits, which is what sector screens are written against."""
        if self.sic is None or not self.sic.isdigit():
            return None
        return int(self.sic[:2])

    @property
    def is_financial(self) -> bool:
        """SIC 6000-6799. Accruals are not comparable for these filers.

        A bank's balance sheet is its business, so the accrual construct -- earnings
        not yet backed by operating cash -- does not mean for a bank what it means
        for a manufacturer. The accruals literature excludes them for this reason,
        not to improve the result.
        """
        group = self.sic_major_group
        return group is not None and 60 <= group <= 67

    @property
    def is_utility(self) -> bool:
        """SIC 4900-4949. Rate-regulated, so accruals reflect the regulator."""
        if self.sic is None or not self.sic.isdigit():
            return False
        return 4900 <= int(self.sic) <= 4949


@dataclass(frozen=True, slots=True)
class Revision:
    """A published value being replaced by a different one."""

    cik: Cik
    concept: str
    unit: str
    period_end: date
    prior_value: Decimal
    prior_knowledge_date: date
    new_value: Decimal
    new_knowledge_date: date
    new_accn: Accession
    new_form: str

    @property
    def relative_change(self) -> Decimal:
        """Signed fractional change. Undefined when the prior value was zero."""
        if self.prior_value == 0:
            raise ZeroDivisionError(
                f"prior value for {self.concept} ({self.period_end}) was zero; "
                f"use absolute_change instead of a ratio"
            )
        return (self.new_value - self.prior_value) / self.prior_value

    @property
    def absolute_change(self) -> Decimal:
        return self.new_value - self.prior_value

    @property
    def days_to_revision(self) -> int:
        return (self.new_knowledge_date - self.prior_knowledge_date).days


class PitView:
    """The world as it was knowable on one date."""

    __slots__ = ("_warehouse", "as_of")

    def __init__(self, warehouse: Warehouse, as_of: date) -> None:
        self._warehouse = warehouse
        self.as_of = as_of

    def __repr__(self) -> str:
        return f"PitView(as_of={self.as_of.isoformat()})"

    # ---------------------------------------------------------------- facts --

    def value(
        self,
        cik: Cik | int,
        concept: str,
        *,
        period_end: date | None = None,
        taxonomy: str = DEFAULT_TAXONOMY,
        unit: str | None = None,
    ) -> Decimal:
        """The value a practitioner would have had on ``as_of``.

        Raises :class:`InsufficientData` when nothing was published in time —
        deliberately, rather than returning ``None`` or a NaN that would flow
        into a Sharpe ratio unnoticed.
        """
        fact = self.fact(cik, concept, period_end=period_end, taxonomy=taxonomy, unit=unit)
        return fact.value

    def fact(
        self,
        cik: Cik | int,
        concept: str,
        *,
        period_end: date | None = None,
        period_start: date | None = None,
        taxonomy: str = DEFAULT_TAXONOMY,
        unit: str | None = None,
    ) -> PitFact:
        """Latest report of one period that was public on or before ``as_of``.

        Raises :class:`AmbiguousPeriod` when ``period_end`` matches more than one
        reporting period and ``period_start`` was not given -- see the note on
        :meth:`first_reported`.
        """
        facts = self.facts(
            cik,
            concept,
            period_end=period_end,
            period_start=period_start,
            taxonomy=taxonomy,
            unit=unit,
        )
        _assert_one_period(facts, concept=concept, cik=cik, period_end=period_end)
        if not facts:
            raise InsufficientData(
                f"{concept} for CIK {int(cik)}"
                f"{f' period ending {period_end}' if period_end else ''} "
                f"had not been published as of {self.as_of}"
            )
        return facts[0]

    def facts(
        self,
        cik: Cik | int,
        concept: str,
        *,
        period_end: date | None = None,
        period_start: date | None = None,
        taxonomy: str = DEFAULT_TAXONOMY,
        unit: str | None = None,
        limit: int | None = None,
    ) -> list[PitFact]:
        """Most recently published report per period, newest period first.

        One row per period: the report that was current on ``as_of``. Earlier
        reports of the same period are superseded and are reachable through
        :meth:`revisions` when the question is about the revision itself.
        """
        conditions = ["cik = ?", "concept = ?", "taxonomy = ?", "knowledge_date <= ?"]
        params: list[Any] = [int(cik), concept, taxonomy, self.as_of]
        if period_end is not None:
            conditions.append("period_end = ?")
            params.append(period_end)
        if period_start is not None:
            conditions.append("period_start = ?")
            params.append(period_start)
        if unit is not None:
            conditions.append("unit = ?")
            params.append(unit)

        sql = f"""
            SELECT {_FACT_SELECT}
              FROM (
                SELECT *, ROW_NUMBER() OVER (
                           PARTITION BY cik, taxonomy, concept, unit, period_start, period_end
                           ORDER BY knowledge_date DESC, accn DESC
                       ) AS recency
                  FROM v_facts_pit
                 WHERE {" AND ".join(conditions)}
              )
             WHERE recency = 1
             ORDER BY period_end DESC, unit
        """  # noqa: S608 - conditions are literals, every value is bound
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self._facts(sql, params)

    def first_reported(
        self,
        cik: Cik | int,
        concept: str,
        *,
        period_end: date,
        period_start: date | None = None,
        taxonomy: str = DEFAULT_TAXONOMY,
        unit: str | None = None,
    ) -> PitFact:
        """The value as originally published — never a later restatement.

        The right input for any study of what the market actually reacted to.

        **``period_end`` alone does not identify a period.** A fiscal year and its
        fourth quarter end on the same day: Apple's FY2015 net income is $53.394B
        over 363 days and its Q4 2015 net income is $11.124B over 90 days, both
        tagged ``2015-09-26`` at ``report_seq = 1``. 860,961 groups in a
        778-filer warehouse are ambiguous this way.

        When more than one period matches, this raises :class:`AmbiguousPeriod`
        rather than picking. Returning either silently is how an accruals ratio
        comes to divide a quarter's earnings by a year's cash flow and report it
        as a finding.
        """
        conditions = [
            "cik = ?",
            "concept = ?",
            "taxonomy = ?",
            "period_end = ?",
            "knowledge_date <= ?",
            "report_seq = 1",
        ]
        params: list[Any] = [int(cik), concept, taxonomy, period_end, self.as_of]
        if period_start is not None:
            conditions.append("period_start = ?")
            params.append(period_start)
        if unit is not None:
            conditions.append("unit = ?")
            params.append(unit)
        facts = self._facts(
            f"SELECT {_FACT_SELECT} FROM v_facts_pit WHERE {' AND '.join(conditions)} "  # noqa: S608
            f"ORDER BY unit, period_start",
            params,
        )
        _assert_one_period(facts, concept=concept, cik=cik, period_end=period_end)
        if not facts:
            raise InsufficientData(
                f"{concept} for CIK {int(cik)} period ending {period_end} "
                f"had no first report on or before {self.as_of}"
            )
        return facts[0]

    def unsafe_latest_restated(
        self,
        cik: Cik | int,
        concept: str,
        *,
        period_end: date,
        period_start: date | None = None,
        taxonomy: str = DEFAULT_TAXONOMY,
        unit: str | None = None,
    ) -> PitFact:
        """The value as it stands TODAY, ignoring the knowledge date. **Lookahead.**

        This is what a commercial fundamentals panel gives you, and using it in a
        simulation is the bug this system exists to prevent. It is provided
        because comparing it against :meth:`first_reported` is how you *measure*
        the bias — and it is named so that every use is visible in a grep and
        impossible to type by accident.
        """
        conditions = ["cik = ?", "concept = ?", "taxonomy = ?", "period_end = ?"]
        params: list[Any] = [int(cik), concept, taxonomy, period_end]
        if period_start is not None:
            conditions.append("period_start = ?")
            params.append(period_start)
        if unit is not None:
            conditions.append("unit = ?")
            params.append(unit)
        rows = self._warehouse.execute(
            f"SELECT {_FACT_SELECT} FROM v_facts_pit WHERE {' AND '.join(conditions)} "  # noqa: S608
            f"ORDER BY knowledge_date DESC, accn DESC",
            params,
        ).fetchall()
        _assert_one_period(
            [_to_fact(row) for row in rows], concept=concept, cik=cik, period_end=period_end
        )
        rows = rows[:1]
        if not rows:
            raise InsufficientData(
                f"{concept} for CIK {int(cik)} period ending {period_end} was never published"
            )
        # Deliberately NOT guarded: this method's whole purpose is to see past
        # the knowledge date.
        return _to_fact(rows[0])

    def revisions(
        self,
        cik: Cik | int | None = None,
        *,
        concept: str | None = None,
        taxonomy: str = DEFAULT_TAXONOMY,
        min_relative_change: float | None = None,
    ) -> list[Revision]:
        """Value changes that had become public by ``as_of``.

        Both the original and the replacement must have been published in time —
        a revision you have not seen yet is not information you have.
        """
        conditions = ["taxonomy = ?", "knowledge_date <= ?", "prior_value IS NOT NULL"]
        params: list[Any] = [taxonomy, self.as_of]
        if cik is not None:
            conditions.append("cik = ?")
            params.append(int(cik))
        if concept is not None:
            conditions.append("concept = ?")
            params.append(concept)
        if min_relative_change is not None:
            conditions.append("prior_value <> 0 AND abs((value - prior_value) / prior_value) >= ?")
            params.append(Decimal(str(min_relative_change)))

        rows = self._warehouse.execute(
            f"""
            SELECT cik, concept, unit, period_end, prior_value, prior_knowledge_date,
                   value, knowledge_date, accn, form
              FROM (
                SELECT *,
                       LAG(value)          OVER w AS prior_value,
                       LAG(knowledge_date) OVER w AS prior_knowledge_date
                  FROM v_facts_pit
                WINDOW w AS (
                    PARTITION BY cik, taxonomy, concept, unit, period_start, period_end
                    ORDER BY knowledge_date, accn
                )
              )
             WHERE {" AND ".join(conditions)} AND value <> prior_value
             ORDER BY knowledge_date DESC, cik, concept
            """,  # noqa: S608 - conditions are literals, every value is bound
            params,
        ).fetchall()

        revisions = [
            Revision(
                cik=Cik(row[0]),
                concept=str(row[1]),
                unit=str(row[2]),
                period_end=row[3],
                prior_value=row[4],
                prior_knowledge_date=row[5],
                new_value=row[6],
                new_knowledge_date=row[7],
                new_accn=Accession(str(row[8])),
                new_form=str(row[9]),
            )
            for row in rows
        ]
        for revision in revisions:
            self._assert_knowable(revision.new_knowledge_date, f"revision of {revision.concept}")
        return revisions

    # -------------------------------------------------------------- filings --

    def filings(
        self,
        cik: Cik | int | None = None,
        *,
        forms: Sequence[str] | None = None,
        since: date | None = None,
        limit: int | None = None,
    ) -> list[PitFiling]:
        """Filings public on or before ``as_of``, newest first.

        Reads the co-filer relation, so a company that was a co-registrant on a
        joint filing sees it here.
        """
        conditions = ["knowledge_date <= ?"]
        params: list[Any] = [self.as_of]
        if cik is not None:
            conditions.append("cik = ?")
            params.append(int(cik))
        if forms:
            conditions.append(f"form IN ({', '.join('?' for _ in forms)})")
            params.extend(forms)
        if since is not None:
            conditions.append("knowledge_date >= ?")
            params.append(since)

        sql = (
            f"SELECT accn, cik, form, filed_at, disseminated_at, knowledge_date, "  # noqa: S608
            f"period_of_report, items FROM v_company_filings_pit "
            f"WHERE {' AND '.join(conditions)} ORDER BY knowledge_date DESC, accn"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        filings = [
            PitFiling(
                accn=Accession(str(row[0])),
                cik=Cik(row[1]),
                form=str(row[2]),
                filed_at=row[3],
                disseminated_at=row[4],
                knowledge_date=row[5],
                period_of_report=row[6],
                items=tuple(row[7] or ()),
            )
            for row in self._warehouse.execute(sql, params).fetchall()
        ]
        for filing in filings:
            self._assert_knowable(filing.knowledge_date, f"filing {filing.accn}")
        return filings

    # ------------------------------------------------------------- entities --

    def entity(self, cik: Cik | int) -> PitEntity:
        """Registrant metadata: name, and the SIC code a sector screen needs."""
        row = self._warehouse.execute(
            "SELECT cik, name, sic, sic_description, fiscal_year_end FROM entities WHERE cik = ?",
            [int(cik)],
        ).fetchone()
        if row is None:
            raise InsufficientData(f"no entity record for CIK {int(cik)}")
        return PitEntity(
            cik=Cik(int(row[0])),
            name=str(row[1]),
            sic=str(row[2]) if row[2] else None,
            sic_description=str(row[3]) if row[3] else None,
            fiscal_year_end=str(row[4]) if row[4] else None,
        )

    def tickers(self, cik: Cik | int) -> list[str]:
        """Ticker symbols observed for this registrant, alphabetically.

        **Not point-in-time.** The SEC publishes only a current ticker-to-CIK
        snapshot, so this is the map as observed at ingest, not as it stood on
        ``as_of``. A company that changed ticker resolves to its current one.

        This is a real limitation and any study using it must say so. It is
        tolerable in a comparison across data vintages, because the same map is
        applied to every arm and therefore cancels in the difference -- but it
        would not be tolerable in a claim about the level of a return.
        """
        rows = self._warehouse.execute(
            "SELECT DISTINCT ticker FROM entity_identifiers WHERE cik = ? ORDER BY ticker",
            [int(cik)],
        ).fetchall()
        return [str(row[0]) for row in rows]

    # ---------------------------------------------------------------- macro --

    def macro(self, series_id: str, obs_date: date) -> float:
        """The value of ``series_id`` for ``obs_date`` **as published by** ``as_of``.

        For heavily revised series this differs sharply from today's figure: real
        US GDP for 2020Q2 was first published at 17,205.8 and stands at 19,078.0
        after eight revisions and a rebasing.
        """
        row = self._warehouse.execute(
            """
            SELECT value, realtime_start FROM macro_observations
             WHERE series_id = ? AND obs_date = ? AND realtime_start <= ?
             ORDER BY realtime_start DESC LIMIT 1
            """,
            [series_id, obs_date, self.as_of],
        ).fetchone()
        if row is None or row[0] is None:
            raise InsufficientData(
                f"{series_id} for {obs_date} had no published value as of {self.as_of}"
            )
        self._assert_knowable(row[1], f"{series_id} vintage")
        return float(row[0])

    def macro_series(self, series_id: str, *, start: date, end: date) -> list[tuple[date, float]]:
        """The whole series as it stood on ``as_of`` — every point at its vintage."""
        rows = self._warehouse.execute(
            """
            SELECT obs_date, value, realtime_start FROM (
                SELECT *, ROW_NUMBER() OVER (
                           PARTITION BY obs_date ORDER BY realtime_start DESC
                       ) AS recency
                  FROM macro_observations
                 WHERE series_id = ? AND realtime_start <= ?
                   AND obs_date BETWEEN ? AND ?
            ) WHERE recency = 1 AND value IS NOT NULL
            ORDER BY obs_date
            """,
            [series_id, self.as_of, start, end],
        ).fetchall()
        for row in rows:
            self._assert_knowable(row[2], f"{series_id} vintage")
        return [(row[0], float(row[1])) for row in rows]

    # --------------------------------------------------------------- prices --

    def prices(
        self,
        symbol: str,
        *,
        start: date,
        end: date | None = None,
        execution_lag_days: int,
    ) -> list[PitPrice]:
        """Daily bars, each tagged with when it could first be acted on.

        ``execution_lag_days`` has no default on purpose. A daily bar is only
        complete at the close, so acting on it at that same close is the most
        common silent lookahead in equity backtesting. Stating the lag forces the
        assumption into the open — 1 for next-day open, 0 only if you genuinely
        model a market-on-close order.
        """
        if execution_lag_days < 0:
            raise ValueError("execution_lag_days cannot be negative")
        upper = min(end or self.as_of, self.as_of)
        rows = self._warehouse.execute(
            """
            SELECT symbol, bar_date, open, high, low, close, adj_close, volume
              FROM prices
             WHERE symbol = ? AND bar_date BETWEEN ? AND ?
             ORDER BY bar_date
            """,
            [symbol, start, upper],
        ).fetchall()
        bars = [
            PitPrice(
                symbol=str(row[0]),
                bar_date=row[1],
                open=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                close=float(row[5]),
                adj_close=float(row[6]) if row[6] is not None else None,
                volume=float(row[7]),
                tradable_from=row[1] + timedelta(days=execution_lag_days),
            )
            for row in rows
        ]
        for bar in bars:
            self._assert_knowable(bar.bar_date, f"price bar for {symbol}")
        return bars

    # -------------------------------------------------------------- private --

    def _facts(self, sql: str, params: Sequence[Any]) -> list[PitFact]:
        facts = [_to_fact(row) for row in self._warehouse.execute(sql, params).fetchall()]
        for fact in facts:
            self._assert_knowable(fact.knowledge_date, f"{fact.concept} ({fact.accn})")
        return facts

    def _assert_knowable(self, knowledge_date: date, what: str) -> None:
        """The canary.

        The WHERE clause should already have excluded this row. If it did not,
        the predicate is wrong — and a wrong predicate is exactly the failure a
        filter cannot catch, because the filter is the thing that is wrong.
        """
        if knowledge_date > self.as_of:
            raise LookaheadViolation(
                f"{what} became knowable on {knowledge_date}, after the as-of date {self.as_of}",
                as_of=self.as_of,
                offending_filed_at=knowledge_date,
            )


def as_of(warehouse: Warehouse, knowledge_date: date) -> PitView:
    """Open the world as it was on ``knowledge_date``."""
    return PitView(warehouse, knowledge_date)


_FACT_SELECT: Final = (
    "cik, taxonomy, concept, unit, period_start, period_end, value, accn, form, "
    "filed_at, knowledge_date, report_seq, source_uri, content_sha256"
)


def _to_fact(row: Sequence[Any]) -> PitFact:
    return PitFact(
        cik=Cik(row[0]),
        taxonomy=str(row[1]),
        concept=str(row[2]),
        unit=str(row[3]),
        period_start=row[4],
        period_end=row[5],
        value=row[6],
        accn=Accession(str(row[7])),
        form=str(row[8]),
        filed_at=row[9],
        knowledge_date=row[10],
        report_seq=int(row[11]),
        source_uri=str(row[12]),
        content_sha256=str(row[13]),
    )


def _assert_one_period(
    facts: Sequence[PitFact], *, concept: str, cik: Cik | int, period_end: date | None
) -> None:
    """Refuse to answer when the request named more than one reporting period.

    The check is on ``period_start``: two facts sharing an end date but starting on
    different days are a year and a quarter, not a value and its restatement.
    Restatements are already collapsed by ``report_seq`` and by the recency
    window, so anything left here is a genuine ambiguity in the question.
    """
    starts = {fact.period_start for fact in facts}
    if len(starts) <= 1:
        return
    spans = ", ".join(
        f"{start.isoformat() if start else 'instant'}..{period_end} ({(period_end - start).days}d)"
        if start and period_end
        else "instant"
        for start in sorted(starts, key=lambda value: (value is None, value))
    )
    raise AmbiguousPeriod(
        f"{concept} for CIK {int(cik)} ending {period_end} matches "
        f"{len(starts)} reporting periods ({spans}); pass period_start to say which. "
        f"A fiscal year and its fourth quarter share an end date.",
        candidates=tuple(sorted(starts, key=lambda value: (value is None, value))),
    )
