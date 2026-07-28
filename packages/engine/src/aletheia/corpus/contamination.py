"""How much of a modern fundamentals panel did not exist at the time.

A population statistic, not a backtest. It asks one question of the whole
corpus: for what share of facts does the value standing today differ from the
one first published? That is the size of the gap a vendor panel hides, stated as
a number rather than as the single AAPL anecdote the README opens with.

Pre-registered in decision D20, before any aggregate here was run. Three choices
in particular were fixed in advance because each is a place where a result could
otherwise be tuned after the fact:

**Grain.** A *fact* is ``(cik, taxonomy, concept, unit, period_start,
period_end)`` -- the same partition ``v_facts_pit`` windows over.
``differs_from_first_report`` is a per-row flag, so a fact restated five times
contributes five flagged rows; counting rows would inflate the headline by the
restatement frequency. Row-grain is computed too and reported alongside, because
the gap between the two is informative and reporting only the flattering one
would be a choice.

**Units and taxonomies are excluded by the grain, not by a filter.** A value
that moves from ``USD`` to ``USD/shares``, or from one taxonomy to another, is
not an economic restatement. Because unit and taxonomy are *inside* the grain,
such a move produces two separate facts rather than one restated fact, and no
exclusion filter is needed. :func:`cross_grain_spread` counts how often this
happens so the reader can see the size of what the grain absorbs instead of
taking it on trust.

**No single materiality threshold is the headline.** The distribution of
relative change is reported in full, with the shares above several cutoffs.
Choosing one cutoff after seeing the distribution is exactly the degree of
freedom this package exists to close.

Sign flips are counted separately rather than folded in. In XBRL a sign change
is frequently a presentation-convention change (an expense filed positive, then
negative) rather than a restatement of what happened, and a reader deserves to
size that themselves.

**Added post-hoc, and labelled as such.** The relative-change measure is bounded
above by 2, since dividing by the larger magnitude caps a full sign flip of equal
size at ``2x/x``. The first population run came back with p95 and p99 both at
exactly 2.0, which is the cap rather than a coincidence: the upper tail is
dominated by sign flips, and sign flips are the thing this module already says
are usually presentation conventions. So the quantiles and threshold counts are
reported a second time with sign flips excluded. This was decided *after* seeing
the distribution and is not part of the D20 pre-registration -- which is why it
is a supplementary panel and not the headline, and why it is said here rather
than quietly folded into the pre-registered numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aletheia.store.db import Warehouse

QUANTILES: tuple[float, ...] = (0.5, 0.75, 0.9, 0.95, 0.99)
"""Reported for the relative change among restated facts. Fixed here, in advance."""

THRESHOLDS: tuple[str, ...] = ("0.01", "0.05", "0.10")
"""Materiality cutoffs reported side by side. None of them is the headline."""


@dataclass(frozen=True, slots=True)
class Contamination:
    """The population statistic, at both grains, with its distribution."""

    facts: int
    """Distinct ``(cik, taxonomy, concept, unit, period_start, period_end)`` tuples."""
    restated_facts: int
    """Facts carrying more than one distinct value across their report sequence."""
    rows: int
    """Fact-report pairs -- one per (fact, filing) that published a value."""
    restated_rows: int
    """Rows whose value differs from the first report of that fact."""
    facts_reported_once: int
    """Facts published exactly once, which therefore *cannot* show a restatement.
    Reported because they dilute the headline share and a reader may reasonably
    want the rate among facts that were ever republished at all."""
    restatable_facts: int
    """Facts published more than once -- the denominator for that second rate."""
    sign_flips: int
    """Restated facts whose first and latest values have opposite signs and are
    both non-zero. Often a presentation convention, not a restatement."""
    revised_up: int
    """Restated facts whose latest value exceeds the first-published one."""
    revised_down: int
    """Restated facts whose latest value is below the first-published one.

    Direction is reported for its own sake -- a panel that is mostly revised
    *down* is a different hazard from one revised up -- and it is the only
    statistic here that depends on ``first`` meaning *first published* rather
    than *smallest*. Every other figure is symmetric in the two values, so
    without this pair, replacing ``arg_min(value, report_seq)`` with
    ``min(value)`` would leave the whole suite green."""
    quantiles: dict[str, Decimal]
    """Relative change among restated facts, at :data:`QUANTILES`."""
    threshold_counts: dict[str, int]
    """Restated facts whose relative change exceeds each of :data:`THRESHOLDS`."""
    undefined_relative_change: int
    """Restated facts where first and latest are both zero, so the relative change
    has no value. Counted rather than silently dropped from the denominator.

    Reachable because ``distinct_values`` looks at the whole report sequence
    while ``first`` and ``latest`` look only at its ends: a fact reported 0, then
    5, then 0 has two distinct values and two zero endpoints. It is genuinely a
    restatement -- a backtest reading the middle vintage saw 5 -- with no
    denominator to express the size of it."""
    restated_from_or_to_zero: int
    """Restated facts where exactly one of the two values is zero.

    A different phenomenon from a revision, and worth separating: the fact was
    reported as zero and later given a value, or reported and later zeroed. Every
    one of these sits at exactly 1.0 on the relative-change scale, because
    ``|x - 0| / max(|x|, 0)`` is 1 for any non-zero ``x`` -- so a spike at 1.0 in
    the distribution is this and nothing else. Counted rather than inferred from
    the spike."""
    quantiles_excluding_sign_flips: dict[str, Decimal]
    """The same quantiles with sign flips removed. Post-hoc -- see the module
    docstring. The measure is capped at 2, a full sign flip of equal magnitude
    sits exactly at the cap, and on the real corpus the top of the distribution
    turned out to be that cap. This panel is what the tail looks like once the
    presentation conventions are taken out of it."""
    threshold_counts_excluding_sign_flips: dict[str, int]
    """Threshold counts with sign flips removed. Post-hoc, same reason: a sign
    flip clears every cutoff by construction, so it inflates all three."""

    @property
    def fact_share(self) -> Decimal:
        """The headline: share of facts whose value changed after first publication."""
        return _share(self.restated_facts, self.facts)

    @property
    def row_share(self) -> Decimal:
        return _share(self.restated_rows, self.rows)

    @property
    def restatable_share(self) -> Decimal:
        """Share among facts that were republished at least once."""
        return _share(self.restated_facts, self.restatable_facts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "facts": self.facts,
            "restated_facts": self.restated_facts,
            "fact_share": self.fact_share,
            "rows": self.rows,
            "restated_rows": self.restated_rows,
            "row_share": self.row_share,
            "facts_reported_once": self.facts_reported_once,
            "restatable_facts": self.restatable_facts,
            "restatable_share": self.restatable_share,
            "sign_flips": self.sign_flips,
            "restated_from_or_to_zero": self.restated_from_or_to_zero,
            "revised_up": self.revised_up,
            "revised_down": self.revised_down,
            "undefined_relative_change": self.undefined_relative_change,
            "relative_change_quantiles": dict(sorted(self.quantiles.items())),
            "restated_facts_above": dict(sorted(self.threshold_counts.items())),
            "post_hoc_excluding_sign_flips": {
                "relative_change_quantiles": dict(
                    sorted(self.quantiles_excluding_sign_flips.items())
                ),
                "restated_facts_above": dict(
                    sorted(self.threshold_counts_excluding_sign_flips.items())
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class CrossGrainSpread:
    """How much the grain absorbs, so the reader need not take it on trust."""

    triples: int
    """Distinct ``(cik, concept, period_start, period_end)`` -- the grain with unit
    and taxonomy removed."""
    multi_unit: int
    """Triples reported under more than one unit."""
    multi_taxonomy: int
    """Triples reported under more than one taxonomy."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "triples": self.triples,
            "multi_unit": self.multi_unit,
            "multi_taxonomy": self.multi_taxonomy,
            "multi_unit_share": _share(self.multi_unit, self.triples),
            "multi_taxonomy_share": _share(self.multi_taxonomy, self.triples),
        }


_PER_FACT = """
    SELECT
        count(*)                                    AS n_reports,
        max(period_distinct_values)                 AS distinct_values,
        arg_min("value", report_seq)                AS first_value,
        arg_max("value", report_seq)                AS latest_value
      FROM v_facts_pit
     WHERE {predicate}
     GROUP BY cik, taxonomy, concept, unit, period_start, period_end
"""

_TALLY = """
WITH per_fact AS (
{per_fact}
), scored AS (
    SELECT
        n_reports,
        distinct_values,
        first_value,
        latest_value,
        -- Written once and read by every statistic below. It used to appear a
        -- second time in a separate quantile query, which meant a mutation of
        -- one copy left the other reporting the old formula -- the survivor that
        -- made this single-query shape necessary.
        CASE
            WHEN greatest(abs(first_value), abs(latest_value)) = 0 THEN NULL
            ELSE abs(CAST(latest_value - first_value AS DOUBLE))
                 / CAST(greatest(abs(first_value), abs(latest_value)) AS DOUBLE)
        END AS relative_change,
        -- Written once for the same reason: the sign-flip count and the
        -- sign-flip-excluded distribution have to agree on what a flip is.
        sign(first_value) <> sign(latest_value)
            AND first_value <> 0
            AND latest_value <> 0                       AS is_sign_flip
      FROM per_fact
)
SELECT
    count(*)                                                        AS facts,
    count(*) FILTER (WHERE distinct_values >= 2)                    AS restated_facts,
    coalesce(sum(n_reports), 0)                                     AS rows_total,
    count(*) FILTER (WHERE n_reports = 1)                           AS reported_once,
    count(*) FILTER (WHERE n_reports > 1)                           AS restatable,
    count(*) FILTER (WHERE distinct_values >= 2 AND is_sign_flip)   AS sign_flips,
    count(*) FILTER (
        WHERE distinct_values >= 2
          AND (first_value = 0) <> (latest_value = 0)
    )                                                               AS zero_endpoint,
    count(*) FILTER (
        WHERE distinct_values >= 2 AND relative_change IS NULL
    )                                                               AS undefined_change,
    count(*) FILTER (WHERE distinct_values >= 2 AND latest_value > first_value) AS revised_up,
    count(*) FILTER (WHERE distinct_values >= 2 AND latest_value < first_value) AS revised_down,
    {extras}
  FROM scored
"""

_ROW_FLAGS = """
SELECT count(*) FILTER (WHERE differs_from_first_report)
  FROM v_facts_pit
 WHERE {predicate}
"""


def measure_contamination(
    warehouse: Warehouse, *, cik: int | None = None, concept: str | None = None
) -> Contamination:
    """Measure restatement contamination over the corpus, or over a slice of it.

    The optional ``cik``/``concept`` narrowing exists so the same code path that
    produces the population figure can be pointed at a slice whose answer is
    already known independently -- see
    ``test_the_aapl_slice_reproduces_the_independently_measured_answer``. A
    statistic computed by one query and validated by another is validated only
    against the second query's bugs.
    """
    predicate, params = _predicate(cik=cik, concept=concept)
    per_fact = _PER_FACT.format(predicate=predicate)

    # Quantiles and threshold counts are appended to the same aggregate rather
    # than run as their own queries, so that every statistic reads the one
    # ``relative_change`` expression. The cutoffs and quantile points are module
    # constants, not caller input, so the interpolation carries no user data.
    # Each statistic is emitted twice: once over all restated facts (the
    # pre-registered figure) and once with sign flips removed (the post-hoc
    # panel). Both read the same ``relative_change`` and ``is_sign_flip``
    # expressions, so the two panels cannot disagree about their definitions.
    restated = "distinct_values >= 2"
    extras = ", ".join(
        [
            *(
                f"quantile_cont(relative_change, {q}) FILTER (WHERE {restated})"
                f' AS "{_quantile_key(q)}"'
                for q in QUANTILES
            ),
            *(
                f"quantile_cont(relative_change, {q})"
                f" FILTER (WHERE {restated} AND NOT is_sign_flip)"
                f' AS "{_quantile_key(q)}_nf"'
                for q in QUANTILES
            ),
            *(
                f"count(*) FILTER (WHERE {restated} AND relative_change > {threshold})"
                f' AS "above_{threshold}"'
                for threshold in THRESHOLDS
            ),
            *(
                f"count(*) FILTER (WHERE {restated} AND NOT is_sign_flip"
                f" AND relative_change > {threshold})"
                f' AS "above_{threshold}_nf"'
                for threshold in THRESHOLDS
            ),
        ]
    )
    cursor = warehouse.execute(_TALLY.format(per_fact=per_fact, extras=extras), params)
    row = cursor.fetchone()
    if row is None:  # pragma: no cover - an aggregate without GROUP BY always returns a row
        raise RuntimeError("aggregate returned no row")
    # Read by name. Positional indexing into an aggregate this wide is one
    # inserted column away from silently reporting the wrong statistic, and every
    # figure here is load-bearing.
    tally = dict(zip((column[0] for column in cursor.description), row, strict=True))

    restated_rows_row = warehouse.execute(_ROW_FLAGS.format(predicate=predicate), params).fetchone()
    restated_rows = int(restated_rows_row[0]) if restated_rows_row else 0

    return Contamination(
        facts=int(tally["facts"]),
        restated_facts=int(tally["restated_facts"]),
        rows=int(tally["rows_total"]),
        restated_rows=restated_rows,
        facts_reported_once=int(tally["reported_once"]),
        restatable_facts=int(tally["restatable"]),
        sign_flips=int(tally["sign_flips"]),
        restated_from_or_to_zero=int(tally["zero_endpoint"]),
        quantiles={f"p{int(q * 100)}": _round(tally[_quantile_key(q)]) for q in QUANTILES},
        threshold_counts={threshold: int(tally[f"above_{threshold}"]) for threshold in THRESHOLDS},
        quantiles_excluding_sign_flips={
            f"p{int(q * 100)}": _round(tally[f"{_quantile_key(q)}_nf"]) for q in QUANTILES
        },
        threshold_counts_excluding_sign_flips={
            threshold: int(tally[f"above_{threshold}_nf"]) for threshold in THRESHOLDS
        },
        undefined_relative_change=int(tally["undefined_change"]),
        revised_up=int(tally["revised_up"]),
        revised_down=int(tally["revised_down"]),
    )


def _quantile_key(quantile: float) -> str:
    return f"q{str(quantile).replace('.', '_')}"


def cross_grain_spread(warehouse: Warehouse) -> CrossGrainSpread:
    """Count what the grain absorbs: facts reported under several units or taxonomies."""
    row = warehouse.execute("""
        WITH triple AS (
            SELECT count(DISTINCT unit)     AS units,
                   count(DISTINCT taxonomy) AS taxonomies
              FROM v_facts_pit
             GROUP BY cik, concept, period_start, period_end
        )
        SELECT count(*),
               count(*) FILTER (WHERE units > 1),
               count(*) FILTER (WHERE taxonomies > 1)
          FROM triple
    """).fetchone()
    if row is None:  # pragma: no cover - an aggregate without GROUP BY always returns a row
        raise RuntimeError("aggregate returned no row")
    return CrossGrainSpread(triples=int(row[0]), multi_unit=int(row[1]), multi_taxonomy=int(row[2]))


def _predicate(*, cik: int | None, concept: str | None) -> tuple[str, list[Any]]:
    """Build the WHERE clause. Always non-empty so the format string stays valid."""
    clauses = ["1 = 1"]
    params: list[Any] = []
    if cik is not None:
        clauses.append("cik = ?")
        params.append(cik)
    if concept is not None:
        clauses.append("concept = ?")
        params.append(concept)
    # The same predicate is interpolated into several statements, each of which is
    # executed with its own copy of the parameter list.
    return " AND ".join(clauses), params


def _share(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return _round(numerator / denominator)


def _round(value: float | None) -> Decimal:
    """Eight decimal places, matching the evidence card's quantum.

    ``None`` becomes zero, which happens only when there is nothing to take a
    quantile of: an empty slice, or a sign-flip-excluded panel where every
    restated fact was a sign flip. The count alongside it is zero too, so the
    zero cannot be mistaken for a measurement.

    A non-finite value is an error rather than a number to round. Relative change
    is guarded against a zero denominator, so an infinity here means the guard
    and the division have drifted apart, and reporting ``Infinity`` as a
    percentile would be worse than stopping.
    """
    if value is None:
        return Decimal("0")
    if not math.isfinite(value):
        raise ValueError(f"relative change is not finite: {value}")
    return Decimal(str(round(float(value), 8)))
