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

**Also post-hoc: the split decomposition.** Reading the finished card, the AAPL
control's quantiles were 0.75 and ~0.857 -- which are exactly ``3/4`` and
``6/7``, the arithmetic of Apple's 4:1 (2020) and 7:1 (2014) stock splits. A
split retroactively rewrites every per-share figure in the archive, so it is a
genuine point-in-time restatement -- the split-adjusted number did not exist on
the earlier date -- but it is not an accounting revision, and letting the two
share one headline would overstate what the headline means.
:func:`contamination_by_unit_class` sizes it by splitting the corpus into the
units a split can mechanically touch (per-share, share counts) and the units it
cannot. It was written after the first population run, so it is labelled
post-hoc for the same reason the sign-flip panel is.
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

SURVIVAL_CUTOFFS: tuple[str, ...] = (
    "2018-01-01",
    "2020-01-01",
    "2022-01-01",
    "2024-01-01",
    "2025-01-01",
)
"""Dates at which a filer is called dormant, for :func:`contamination_by_survival`.

All five are reported, always, and that is the design rather than thoroughness
for its own sake. "Has this filer stopped filing?" has no natural cutoff, which
makes the cutoff a free parameter -- and a free parameter reported at one value
is a result chosen after seeing five. Reporting the whole curve removes the
choice instead of making it well.

On this corpus the curve happens to be well behaved: the dormant-minus-active
gap runs +0.42pp to +0.88pp and keeps its sign at every cutoff. That is a
reassuring answer, not a reason to have asked at one point -- a single-cutoff
version of this statistic would have looked identical while carrying none of the
evidence that it holds up.
"""


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
    returned_to_first_value: int
    """Restated facts whose latest value equals the first-published one.

    The third direction, and the one that is easy to forget exists: a fact can
    change and change back. ``distinct_values`` reads the whole report sequence
    while ``first`` and ``latest`` read only its ends, so these are genuinely
    restated -- a backtest reading a middle vintage saw a different number --
    while being invisible to any comparison of the two endpoints.

    Added post-hoc, and it changes no pre-registered figure. It exists because
    ``revised_up + revised_down`` came to 347,745 against 357,842 restated facts
    on the first population run, and an unexplained 10,097 is the first thing a
    reader subtracts. With this field the three directions sum to
    :attr:`restated_facts` exactly, which
    ``test_the_three_directions_account_for_every_restated_fact`` asserts."""
    quantiles: dict[str, Decimal]
    """Relative change among restated facts, at :data:`QUANTILES`."""
    threshold_counts: dict[str, int]
    """Restated facts whose relative change exceeds each of :data:`THRESHOLDS`."""
    undefined_relative_change: int
    """Restated facts where first and latest are both zero, so the relative change
    has no value. Counted rather than silently dropped from the denominator.

    Post-hoc, like :attr:`restated_from_or_to_zero` below and for the same
    reason: both were added after the first population run, on seeing a spike the
    pre-registered statistics could not name. Neither is subtracted from any
    pre-registered figure -- these facts remain inside the headline and inside
    the quantiles exactly as D20 specified -- so this is an addition to what is
    reported, not a change to what was registered. Labelled anyway, because
    which additions get labelled should not itself be a judgement call.

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
            "returned_to_first_value": self.returned_to_first_value,
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
class UnitClass:
    """One row of the split decomposition: how a unit family restates."""

    unit_class: str
    """``per-share``, ``share count``, or ``other`` -- see :data:`_UNIT_CLASS_SQL`."""
    facts: int
    restated_facts: int
    revised_up: int
    revised_down: int
    returned_to_first_value: int
    median_relative_change: Decimal
    """Median relative change among this class's restated facts. Reported because
    the split shows up in magnitude before it shows up in rate: a 4:1 split lands
    every affected fact at exactly 0.75."""

    @property
    def fact_share(self) -> Decimal:
        """Restatement rate within this class."""
        return _share(self.restated_facts, self.facts)

    @property
    def downward_share(self) -> Decimal:
        """Share of directional revisions that went down.

        The denominator excludes :attr:`returned_to_first_value`, which has no
        direction. This is the figure that tests whether the population's
        downward skew is a split artefact: splits divide per-share values, so
        they push this number up for ``per-share`` and down for ``share count``.
        If the skew survives in ``other``, it is not the splits.
        """
        return _share(self.revised_down, self.revised_up + self.revised_down)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_class": self.unit_class,
            "facts": self.facts,
            "restated_facts": self.restated_facts,
            "fact_share": self.fact_share,
            "revised_up": self.revised_up,
            "revised_down": self.revised_down,
            "returned_to_first_value": self.returned_to_first_value,
            "downward_share": self.downward_share,
            "median_relative_change": self.median_relative_change,
        }


@dataclass(frozen=True, slots=True)
class SurvivalSplit:
    """Restatement rates for dormant against still-active filers, at one cutoff."""

    cutoff: str
    """A filer whose last filing predates this date is counted dormant."""
    active_facts: int
    active_restated: int
    dormant_facts: int
    dormant_restated: int

    @property
    def active_share(self) -> Decimal:
        return _share(self.active_restated, self.active_facts)

    @property
    def dormant_share(self) -> Decimal:
        return _share(self.dormant_restated, self.dormant_facts)

    @property
    def gap(self) -> Decimal:
        """Dormant rate minus active rate. Positive means dead filers restate more.

        This is the quantity the survivorship caveat is about. A universe drawn
        from today's index would hold only the active column, so a large positive
        gap would mean such a universe understates restatement -- and a gap near
        zero means the selection barely matters.
        """
        return self.dormant_share - self.active_share

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff,
            "active_facts": self.active_facts,
            "active_restated": self.active_restated,
            "active_share": self.active_share,
            "dormant_facts": self.dormant_facts,
            "dormant_restated": self.dormant_restated,
            "dormant_share": self.dormant_share,
            "gap": self.gap,
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


_UNIT_CLASS_SQL = """
        CASE
            WHEN unit LIKE '%/shares%' THEN 'per-share'
            WHEN unit = 'shares'       THEN 'share count'
            ELSE 'other'
        END
"""
"""Which units a stock split can mechanically rewrite.

The corpus carries 518 distinct units; these predicates were written against an
enumeration of them, not guessed. ``LIKE '%/shares%'`` rather than
``'%/shares'`` because ``USD/shares_unit`` (103 facts) sits alongside
``USD/shares`` (612,164) -- an earlier draft ended the pattern at ``shares`` and
silently filed those into ``other``. The wildcard on both sides still excludes
``shares/USD``, which is an inverse (shares *per* dollar) and is correctly not a
per-share quantity.

Not complete, and here is what it misses: 495 rows carry currency-per-unit forms
(``USD/PartnershipUnit``, ``USD/Unit`` and case variants) for partnerships and
trusts, which are economically per-share equivalents and can be adjusted by a
unit split. They fall into ``other``. At 0.0037% of rows they cannot move the
5.05% ``other`` rate, so the classification is left simple and the residual is
stated rather than chased. ``reporting_unit`` and ``business_unit``, which look
similar, are segment counts and belong in ``other`` on the merits.
"""

# S608: the only interpolation is _UNIT_CLASS_SQL, a module constant written
# directly above. This query takes no parameters and no caller input reaches it.
# It is interpolated rather than pasted so the classification exists once, and a
# mutation of it cannot leave a second copy disagreeing.
_BY_UNIT_CLASS = f"""
WITH per_fact AS (
    SELECT
        {_UNIT_CLASS_SQL}                           AS unit_class,
        max(period_distinct_values)                 AS distinct_values,
        arg_min("value", report_seq)                AS first_value,
        arg_max("value", report_seq)                AS latest_value
      FROM v_facts_pit
     GROUP BY cik, taxonomy, concept, unit, period_start, period_end
)
SELECT
    unit_class,
    count(*)                                                            AS facts,
    count(*) FILTER (WHERE distinct_values >= 2)                        AS restated,
    count(*) FILTER (WHERE distinct_values >= 2
                       AND latest_value > first_value)                  AS revised_up,
    count(*) FILTER (WHERE distinct_values >= 2
                       AND latest_value < first_value)                  AS revised_down,
    count(*) FILTER (WHERE distinct_values >= 2
                       AND latest_value = first_value)                  AS returned,
    quantile_cont(
        CASE
            WHEN greatest(abs(first_value), abs(latest_value)) = 0 THEN NULL
            ELSE abs(CAST(latest_value - first_value AS DOUBLE))
                 / CAST(greatest(abs(first_value), abs(latest_value)) AS DOUBLE)
        END, 0.5
    ) FILTER (WHERE distinct_values >= 2)                               AS median_change
  FROM per_fact
 GROUP BY unit_class
 -- Fixed order, not by count: the panel is read as a comparison and a corpus
 -- where one class overtook another would otherwise reorder the rows.
 ORDER BY CASE unit_class
              WHEN 'per-share'   THEN 1
              WHEN 'share count' THEN 2
              ELSE 3
          END
"""  # noqa: S608

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
    -- The third arm. Strict > and < above leave equality uncounted, so without
    -- this the direction figures silently fail to sum to restated_facts.
    count(*) FILTER (WHERE distinct_values >= 2 AND latest_value = first_value) AS returned,
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
        returned_to_first_value=int(tally["returned"]),
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


_BY_SURVIVAL = """
WITH last_filing AS (
    -- Derived from the facts themselves rather than joined in from ``filings``.
    -- The first version joined, and the reconciliation test below caught it: an
    -- inner join drops any fact whose filer is missing from ``filings``, which
    -- would silently shrink the denominator of a study about numbers going
    -- silently missing. Every fact carries the date it was filed, so the cohort
    -- can be decided without leaving the table and nothing can fall out.
    SELECT cik, max(filed_at) AS last_filed_at FROM v_facts_pit GROUP BY cik
), per_fact AS (
    SELECT cik, max(period_distinct_values) AS distinct_values
      FROM v_facts_pit
     GROUP BY cik, taxonomy, concept, unit, period_start, period_end
), joined AS (
    SELECT p.distinct_values, l.last_filed_at < CAST(? AS DATE) AS dormant
      FROM per_fact p JOIN last_filing l USING (cik)
)
SELECT
    count(*) FILTER (WHERE NOT dormant)                         AS active_facts,
    count(*) FILTER (WHERE NOT dormant AND distinct_values >= 2) AS active_restated,
    count(*) FILTER (WHERE dormant)                             AS dormant_facts,
    count(*) FILTER (WHERE dormant AND distinct_values >= 2)    AS dormant_restated
  FROM joined
"""


def contamination_by_survival(warehouse: Warehouse) -> tuple[SurvivalSplit, ...]:
    """Restatement rates for dormant against still-active filers, at every cutoff.

    Post-hoc, and it exists because a caveat asserted something false. The card
    used to claim this universe was drawn from a *current* ticker map and was
    therefore "alive-today by construction", and reasoned from that to the
    headline being biased down. Neither half held: the universe is a 2011
    point-in-time cross-section that already contains filers which later went
    dark, so the bias had to be measured rather than argued about.

    Every cutoff in :data:`SURVIVAL_CUTOFFS` is returned. Reporting one would
    make the cutoff a choice made after seeing the answer, and on this corpus
    that choice changes the sign of the result.
    """
    splits = []
    for cutoff in SURVIVAL_CUTOFFS:
        row = warehouse.execute(_BY_SURVIVAL, [cutoff]).fetchone()
        if row is None:  # pragma: no cover - an aggregate without GROUP BY returns a row
            raise RuntimeError("aggregate returned no row")
        splits.append(
            SurvivalSplit(
                cutoff=cutoff,
                active_facts=int(row[0]),
                active_restated=int(row[1]),
                dormant_facts=int(row[2]),
                dormant_restated=int(row[3]),
            )
        )
    return tuple(splits)


def contamination_by_unit_class(warehouse: Warehouse) -> tuple[UnitClass, ...]:
    """Split the corpus by whether a stock split could have caused the restatement.

    Post-hoc -- see the module docstring. It answers one question the headline
    cannot answer for itself: a split rewrites every per-share figure in the
    archive at once, so if per-share facts dominated the corpus, the headline
    would be measuring corporate actions rather than accounting revisions.

    Reading this panel means reading the split into the numbers. A split divides
    per-share values and multiplies share counts, so it should push ``per-share``
    *down* and ``share count`` *up* -- and if that asymmetry is not in the output,
    the classification is not finding what it claims to find.
    """
    rows = warehouse.execute(_BY_UNIT_CLASS).fetchall()
    return tuple(
        UnitClass(
            unit_class=str(row[0]),
            facts=int(row[1]),
            restated_facts=int(row[2]),
            revised_up=int(row[3]),
            revised_down=int(row[4]),
            returned_to_first_value=int(row[5]),
            median_relative_change=_round(row[6]),
        )
        for row in rows
    )


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
