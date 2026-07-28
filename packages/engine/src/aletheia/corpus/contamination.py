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

On this corpus the pooled gap runs +0.42pp to +0.88pp and keeps its sign at every
cutoff. **That stability is not evidence of a dormancy effect, and this docstring
used to claim it was.** Varying the cutoff controls one confound; it says nothing
about the one that actually decides the answer. See
:func:`survival_by_period_band` -- stratifying by accounting period reverses the
sign in every band. The pooled comparison is Simpson's paradox, and reporting
five cutoffs of it is thorough about the wrong axis.

Nor is the banded view the fix; it is a second confounded view. See
:attr:`SurvivalSplit.conditional_gap`.
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
    active_restatable: int
    """Active facts reported more than once -- the ones that *could* have been
    restated. Carried because the cohorts differ in it enormously, and a fact
    published once is a guaranteed non-restatement that says nothing about
    whether its filer revises."""
    active_restated: int
    dormant_facts: int
    dormant_restatable: int
    dormant_restated: int

    @property
    def active_share(self) -> Decimal:
        return _share(self.active_restated, self.active_facts)

    @property
    def dormant_share(self) -> Decimal:
        return _share(self.dormant_restated, self.dormant_facts)

    @property
    def active_restatable_share(self) -> Decimal:
        """Share of active facts that were published more than once."""
        return _share(self.active_restatable, self.active_facts)

    @property
    def dormant_restatable_share(self) -> Decimal:
        """Share of dormant facts that were published more than once."""
        return _share(self.dormant_restatable, self.dormant_facts)

    @property
    def active_conditional_share(self) -> Decimal:
        """Restated share of active facts *that had a second report*."""
        return _share(self.active_restated, self.active_restatable)

    @property
    def dormant_conditional_share(self) -> Decimal:
        """Restated share of dormant facts *that had a second report*."""
        return _share(self.dormant_restated, self.dormant_restatable)

    @property
    def conditional_gap(self) -> Decimal:
        """:attr:`gap`, recomputed on facts that had the opportunity to change.

        The second confound, and the reason the period-band table is not the
        correction it was written to be. Stratifying by accounting period removes
        the vintage entanglement and *maximises* this one: a filer that went dark
        stopped filing, so inside a recent band its facts were mostly published
        once and could not be restated for a reason unrelated to restating.

        Conditioning here does not rescue the comparison either -- it trades one
        confound for another, since which facts get a second report is itself a
        function of how long the filer kept filing. It is computed so the
        instability is visible rather than asserted.
        """
        return self.dormant_conditional_share - self.active_conditional_share

    @property
    def gap(self) -> Decimal:
        """Dormant rate minus active rate. Positive means dead filers restate more.

        A contrast between cohorts, and **not** the size of any bias. It was once
        described here as "the quantity the survivorship caveat is about", which
        was wrong twice over: it is confounded with period vintage (see
        :func:`survival_by_period_band`), and even taken at face value it is not
        the counterfactual anyone cares about. For that, use
        :attr:`active_only_bias`.
        """
        return self.dormant_share - self.active_share

    @property
    def pooled_share(self) -> Decimal:
        """Restatement rate over both cohorts -- the headline, recomputed here."""
        return _share(
            self.active_restated + self.dormant_restated,
            self.active_facts + self.dormant_facts,
        )

    @property
    def active_only_bias(self) -> Decimal:
        """How much a still-active-only universe would understate this corpus.

        The actual counterfactual behind the survivorship caveat: a universe
        drawn from *today's* index would contain the active cohort and nothing
        else, so its headline would be :attr:`active_share` rather than
        :attr:`pooled_share`. The difference is this.

        It is much smaller than :attr:`gap`, and necessarily so -- the gap is a
        difference of rates, while this is that difference weighted by the
        dormant cohort's share of the corpus. Quoting the gap in its place
        overstated the effect several-fold, which is the error this property
        exists to make hard to repeat.
        """
        return self.pooled_share - self.active_share

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff,
            "active_facts": self.active_facts,
            "active_restatable": self.active_restatable,
            "active_restated": self.active_restated,
            "active_share": self.active_share,
            "active_restatable_share": self.active_restatable_share,
            "active_conditional_share": self.active_conditional_share,
            "dormant_facts": self.dormant_facts,
            "dormant_restatable": self.dormant_restatable,
            "dormant_restated": self.dormant_restated,
            "dormant_share": self.dormant_share,
            "dormant_restatable_share": self.dormant_restatable_share,
            "dormant_conditional_share": self.dormant_conditional_share,
            "gap": self.gap,
            "conditional_gap": self.conditional_gap,
            "pooled_share": self.pooled_share,
            "active_only_bias": self.active_only_bias,
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
    SELECT cik,
           max(period_distinct_values) AS distinct_values,
           -- How many times the fact was reported at all. A fact published once
           -- cannot be restated, so this is the denominator of opportunity.
           max(report_seq)             AS reports
      FROM v_facts_pit
     GROUP BY cik, taxonomy, concept, unit, period_start, period_end
), joined AS (
    SELECT p.distinct_values, p.reports, l.last_filed_at < CAST(? AS DATE) AS dormant
      FROM per_fact p JOIN last_filing l USING (cik)
)
SELECT
    count(*) FILTER (WHERE NOT dormant)                          AS active_facts,
    count(*) FILTER (WHERE NOT dormant AND reports >= 2)         AS active_restatable,
    count(*) FILTER (WHERE NOT dormant AND distinct_values >= 2) AS active_restated,
    count(*) FILTER (WHERE dormant)                              AS dormant_facts,
    count(*) FILTER (WHERE dormant AND reports >= 2)             AS dormant_restatable,
    count(*) FILTER (WHERE dormant AND distinct_values >= 2)     AS dormant_restated
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
                active_restatable=int(row[1]),
                active_restated=int(row[2]),
                dormant_facts=int(row[3]),
                dormant_restatable=int(row[4]),
                dormant_restated=int(row[5]),
            )
        )
    return tuple(splits)


PERIOD_BANDS: tuple[tuple[str, str], ...] = (
    ("through 2014", "2014-12-31"),
    ("2015-2018", "2018-12-31"),
    ("2019-2022", "2022-12-31"),
    ("2023 onward", "9999-12-31"),
)
"""Accounting-period bands for :func:`survival_by_period_band`, as (label, end)."""

_BY_PERIOD_BAND = """
WITH last_filing AS (
    SELECT cik, max(filed_at) AS last_filed_at FROM v_facts_pit GROUP BY cik
), per_fact AS (
    SELECT cik, period_end,
           max(period_distinct_values) AS distinct_values,
           max(report_seq)             AS reports
      FROM v_facts_pit
     GROUP BY cik, taxonomy, concept, unit, period_start, period_end
), joined AS (
    SELECT p.distinct_values, p.reports, p.period_end,
           l.last_filed_at < CAST(? AS DATE) AS dormant
      FROM per_fact p JOIN last_filing l USING (cik)
)
SELECT
    count(*) FILTER (WHERE NOT dormant)                          AS active_facts,
    count(*) FILTER (WHERE NOT dormant AND reports >= 2)         AS active_restatable,
    count(*) FILTER (WHERE NOT dormant AND distinct_values >= 2) AS active_restated,
    count(*) FILTER (WHERE dormant)                              AS dormant_facts,
    count(*) FILTER (WHERE dormant AND reports >= 2)             AS dormant_restatable,
    count(*) FILTER (WHERE dormant AND distinct_values >= 2)     AS dormant_restated
  FROM joined
 WHERE period_end > CAST(? AS DATE) AND period_end <= CAST(? AS DATE)
"""


def survival_by_period_band(
    warehouse: Warehouse, *, cutoff: str = "2024-01-01"
) -> tuple[tuple[str, SurvivalSplit], ...]:
    """The same dormant-vs-active split, stratified by accounting period.

    This function exists because :func:`contamination_by_survival` produced a
    confident answer that was an artefact. Pooled, dormant filers appear to
    restate *more* -- consistently, across five cutoffs. Stratified by the period
    the fact describes, they restate *less*, in every band.

    Both are arithmetically correct, which is what makes it Simpson's paradox
    rather than a bug. A filer that went dark stopped producing new periods, so
    its facts concentrate in older bands; older periods have had more calendar
    time in which to be revised, and restate more for that reason alone. The
    pooled comparison reads the period mix and reports it as a property of
    dormancy.

    **This table is not the correction, and an earlier version of this docstring
    presented it as one.** It removes the vintage confound and maximises a second
    one: a filer that went dark stopped filing altogether, so inside a band its
    facts were mostly published once and could not be restated for a reason that
    has nothing to do with restating. Read
    :attr:`SurvivalSplit.dormant_restatable_share` against
    :attr:`SurvivalSplit.active_restatable_share` in the recent bands -- the two
    cohorts are not comparable there in the first place, and conditioning on a
    second report (:attr:`SurvivalSplit.conditional_gap`) moves the sign again.

    That is the same error as the one in :data:`SURVIVAL_CUTOFFS`, in the
    opposite direction: a stable sign was read as evidence, then a reversed sign
    was read as the answer, and both were written by the fix to the round before.

    The consequence for the study is a retraction, not a smaller number: cohort
    is entangled with both period vintage and republication opportunity, no
    stratification this corpus supports breaks both at once, and so it cannot
    identify a dormancy effect in either direction. What it *can* answer is the
    narrower question that actually motivated the caveat -- how much a universe
    restricted to still-active filers would differ from this one -- and that is
    the pooled-minus-active-only difference, which is small.
    """
    bands: list[tuple[str, SurvivalSplit]] = []
    lower = "0001-01-01"
    for label, upper in PERIOD_BANDS:
        row = warehouse.execute(_BY_PERIOD_BAND, [cutoff, lower, upper]).fetchone()
        if row is None:  # pragma: no cover - an aggregate without GROUP BY returns a row
            raise RuntimeError("aggregate returned no row")
        bands.append(
            (
                label,
                SurvivalSplit(
                    cutoff=cutoff,
                    active_facts=int(row[0]),
                    active_restatable=int(row[1]),
                    active_restated=int(row[2]),
                    dormant_facts=int(row[3]),
                    dormant_restatable=int(row[4]),
                    dormant_restated=int(row[5]),
                ),
            )
        )
        lower = upper
    return tuple(bands)


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
