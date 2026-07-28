"""Evidence cards: no number leaves this system without its provenance.

A performance figure on its own is not a result, because the reader cannot tell
what would have made it different. The card carries, alongside every number, the
things that determine whether it means anything:

* the **commit** the code was at, and whether the tree was dirty;
* the **data vintage** -- the latest filing date in the warehouse, since a study
  re-run next month sees restatements that did not exist today;
* the **trial count** for the hypothesis family, without which a Sharpe ratio
  cannot be interpreted at all;
* the **costs and turnover**, so a gross figure can never be quoted alone;
* the **exclusions**, so the reader knows how much of the intended universe was
  actually traded;
* the **caveats**, written by the author, in the card rather than in a covering
  note that gets separated from it.

The card serialises to canonical JSON, so two runs of the same study over the same
data produce byte-identical cards apart from the generation timestamp, which is
segregated for exactly that reason. That property is what ``make determinism``
checks.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Final

import numpy as np

from aletheia.core.hashing import canonical_hash
from aletheia.research.kernel import BacktestResult, annualise

REDACTED_FOR_COMPARISON: Final = ("generated_at",)
"""Fields excluded from the reproducibility hash. Only wall-clock belongs here."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a result came from, precisely enough to reproduce or refute it."""

    code_commit: str
    code_dirty: bool
    """True when the working tree had uncommitted changes. A dirty result is not
    reproducible, and saying so is better than a commit hash that lies."""
    config_hash: str
    data_vintage: date
    """Latest knowledge date in the warehouse. Re-running later is a different study."""
    universe_source: str
    row_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code_commit": self.code_commit,
            "code_dirty": self.code_dirty,
            "config_hash": self.config_hash,
            "data_vintage": self.data_vintage.isoformat(),
            "universe_source": self.universe_source,
            "row_counts": dict(sorted(self.row_counts.items())),
        }


@dataclass(frozen=True, slots=True)
class ArmSummary:
    """One backtest arm, reduced to the numbers a reader needs.

    Gross and net are both reported. Quoting gross alone is the commonest way a
    strategy that does not survive its own trading costs gets published.
    """

    label: str
    n_periods: int
    periods_per_year: float
    gross_annualised: float
    """Geometric. Compounding -50% then +50% is a loss; an arithmetic mean says flat."""
    net_annualised: float
    """Geometric, net of modelled costs. The figure to quote as a return."""
    net_arithmetic_annualised: float
    """Arithmetic: mean per period x periods per year.

    Reported alongside the geometric figure so the Sharpe below is on a matched
    basis with a stated return. The Sharpe's numerator is an arithmetic mean, so
    pairing it with the geometric return would compare two different quantities --
    the error that makes an annualised Sharpe and an annualised return disagree."""
    net_stdev_annualised: float
    """Per-period deviation x sqrt(periods per year). The Sharpe's denominator."""
    net_mean_per_period: float
    net_stdev_per_period: float
    sharpe_per_period: float
    annualised_sharpe: float
    """``net_arithmetic_annualised / net_stdev_annualised`` -- both moments scaled,
    which is the whole content of the matched-basis rule."""
    mean_turnover: float
    mean_cost_per_period: float
    n_excluded: int
    exclusions: dict[str, int]
    skipped_formations: tuple[str, ...]

    @classmethod
    def of(cls, result: BacktestResult, *, periods_per_year: float) -> ArmSummary:
        net = list(result.net_returns)
        if not net:
            raise ValueError(f"arm '{result.label}' produced no periods to summarise")
        mean = statistics.fmean(net)
        # Population stdev when there is a single period: refusing would be worse
        # than reporting a zero that the period count already makes obvious.
        stdev = statistics.stdev(net) if len(net) > 1 else 0.0
        sharpe = mean / stdev if stdev > 0 else 0.0
        return cls(
            label=result.label,
            n_periods=len(net),
            periods_per_year=periods_per_year,
            gross_annualised=annualise(result.gross_returns, periods_per_year=periods_per_year),
            net_annualised=annualise(net, periods_per_year=periods_per_year),
            net_arithmetic_annualised=mean * periods_per_year,
            net_stdev_annualised=stdev * float(np.sqrt(periods_per_year)),
            net_mean_per_period=mean,
            net_stdev_per_period=stdev,
            sharpe_per_period=sharpe,
            # Both terms scale together: mean by n, stdev by sqrt(n). Annualising
            # the Sharpe means multiplying by sqrt(periods per year), and doing it
            # to only one of the two is the classic overstatement.
            annualised_sharpe=sharpe * float(np.sqrt(periods_per_year)),
            mean_turnover=result.mean_turnover,
            mean_cost_per_period=statistics.fmean(period.cost for period in result.periods),
            n_excluded=result.total_excluded,
            exclusions={reason.value: count for reason, count in sorted(result.exclusions.items())},
            skipped_formations=tuple(day.isoformat() for day in result.skipped_formations),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_periods": self.n_periods,
            "periods_per_year": _round(self.periods_per_year),
            "gross_annualised": _round(self.gross_annualised),
            "net_annualised": _round(self.net_annualised),
            "net_arithmetic_annualised": _round(self.net_arithmetic_annualised),
            "net_stdev_annualised": _round(self.net_stdev_annualised),
            "net_mean_per_period": _round(self.net_mean_per_period),
            "net_stdev_per_period": _round(self.net_stdev_per_period),
            "sharpe_per_period": _round(self.sharpe_per_period),
            "annualised_sharpe": _round(self.annualised_sharpe),
            "mean_turnover": _round(self.mean_turnover),
            "mean_cost_per_period": _round(self.mean_cost_per_period),
            "n_excluded": self.n_excluded,
            "exclusions": dict(sorted(self.exclusions.items())),
            "skipped_formations": list(self.skipped_formations),
        }


@dataclass(frozen=True, slots=True)
class Comparison:
    """A difference between two arms -- the thing a vintage study actually claims."""

    name: str
    baseline: str
    variant: str
    metric: str
    baseline_value: float
    variant_value: float
    interpretation: str

    @property
    def difference(self) -> float:
        return self.variant_value - self.baseline_value

    @property
    def relative(self) -> float | None:
        if self.baseline_value == 0:
            return None
        return self.difference / abs(self.baseline_value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "baseline": self.baseline,
            "variant": self.variant,
            "metric": self.metric,
            "baseline_value": _round(self.baseline_value),
            "variant_value": _round(self.variant_value),
            "difference": _round(self.difference),
            "relative": None if self.relative is None else _round(self.relative),
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True, slots=True)
class EvidenceCard:
    """An immutable result, with everything needed to judge or refute it."""

    study_id: str
    hypothesis: str
    verdict: str
    """The conclusion in one sentence, written to follow the numbers whichever way
    they fell. A study whose verdict could only ever have been positive was not a
    study."""
    provenance: Provenance
    arms: tuple[ArmSummary, ...]
    comparisons: tuple[Comparison, ...]
    trial_count: int
    """Attempts in this hypothesis family, from the pre-registration ledger."""
    trial_family: str
    caveats: tuple[str, ...]
    generated_at: datetime
    statistics: dict[str, Any] = field(default_factory=dict)
    """Deflated Sharpe, PBO and haircuts, computed by ``trialkeeper``."""

    def body(self) -> dict[str, Any]:
        """Everything except the wall-clock, which is what the repro hash covers."""
        return {
            "study_id": self.study_id,
            "hypothesis": self.hypothesis,
            "verdict": self.verdict,
            "provenance": self.provenance.as_dict(),
            "arms": [arm.as_dict() for arm in self.arms],
            "comparisons": [comparison.as_dict() for comparison in self.comparisons],
            "trial_count": self.trial_count,
            "trial_family": self.trial_family,
            "caveats": list(self.caveats),
            "statistics": _canonicalise(self.statistics),
        }

    @property
    def repro_hash(self) -> str:
        """sha256 of the card excluding the generation timestamp.

        Two runs of the same study over the same warehouse must produce the same
        value. ``make determinism`` fails the build when they do not.

        Hashed from :meth:`body`, where every statistic is a fixed-precision
        ``Decimal``. ``canonical_json`` refuses raw floats on purpose: two runs of
        identical arithmetic can differ in the last bit, and a hash that changes
        for that reason would make the gate useless by crying wolf.
        """
        return canonical_hash(self.body())

    def as_dict(self) -> dict[str, Any]:
        """The readable form written to disk. Decimals become ordinary numbers."""
        return {
            **_as_plain(self.body()),
            "repro_hash": self.repro_hash,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_json(self) -> bytes:
        return (json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8")

    def to_markdown(self) -> str:
        lines = [
            f"# {self.study_id}",
            "",
            f"**Hypothesis.** {self.hypothesis}",
            "",
            f"**Verdict.** {self.verdict}",
        ]

        # A study with no return arms is not a degenerate backtest -- a population
        # statistic has no periods, no turnover and no Sharpe. Emitting the header
        # row and the geometric-vs-arithmetic note over an empty table would
        # describe a methodology the result does not use.
        if self.arms:
            lines += [
                "",
                "## Arms",
                "",
                "| Arm | Periods | Gross p.a. | Net p.a. | Net p.a. (arith.) | Vol p.a. "
                "| Sharpe p.a. | Turnover | Excluded |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
            lines.extend(
                f"| `{arm.label}` | {arm.n_periods} | {arm.gross_annualised:+.2%} | "
                f"{arm.net_annualised:+.2%} | {arm.net_arithmetic_annualised:+.2%} | "
                f"{arm.net_stdev_annualised:.2%} | {arm.annualised_sharpe:.2f} | "
                f"{arm.mean_turnover:.2f}x | {arm.n_excluded:,} |"
                for arm in self.arms
            )
            lines += [
                "",
                "Gross and net returns are geometric. The arithmetic column and the "
                "volatility are on a matched basis with the Sharpe ratio, whose "
                "numerator is an arithmetic mean -- quoting the geometric return "
                "against that Sharpe would compare two different quantities.",
            ]

        if self.comparisons:
            lines += [
                "",
                "## What the differences mean",
                "",
                "| Comparison | Metric | Baseline | Variant | Difference |",
                "|---|---|---:|---:|---:|",
            ]
            lines.extend(
                f"| {comparison.name} | {comparison.metric} | "
                f"{comparison.baseline_value:+.4f} | {comparison.variant_value:+.4f} | "
                f"**{comparison.difference:+.4f}** |"
                for comparison in self.comparisons
            )
            lines += [""]
            lines.extend(
                f"- **{comparison.name}** — {comparison.interpretation}"
                for comparison in self.comparisons
            )

        if self.statistics:
            lines += ["", "## Statistical treatment", ""]
            for key, value in sorted(self.statistics.items()):
                lines.extend(_stat_lines(key, value, depth=0))

        lines += [
            "",
            "## Provenance",
            "",
            f"- Commit `{self.provenance.code_commit}`"
            f"{' **(dirty tree — not reproducible)**' if self.provenance.code_dirty else ''}",
            f"- Config hash `{self.provenance.config_hash}`",
            f"- Data vintage {self.provenance.data_vintage.isoformat()}",
            f"- Universe {self.provenance.universe_source}",
            f"- Trials in family `{self.trial_family}`: **{self.trial_count}**",
            f"- Reproducibility hash `{self.repro_hash}`",
            "",
            "## Caveats",
            "",
        ]
        lines.extend(f"- {caveat}" for caveat in self.caveats)
        return "\n".join(lines) + "\n"


QUANTUM: Final = Decimal("0.00000001")
"""Eight decimal places: far beyond any figure reported here, and far coarser than
the last-bit differences that make two runs of identical arithmetic disagree."""


def _round(value: float) -> Decimal:
    """Pin a statistic to a fixed-precision Decimal so it hashes stably."""
    if not math.isfinite(value):
        raise ValueError(f"cannot record a non-finite statistic: {value}")
    return Decimal(repr(value)).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def _canonicalise(value: Any) -> Any:
    """Recursively pin floats in a nested statistics blob."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return _round(value)
    if isinstance(value, dict):
        return {key: _canonicalise(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_canonicalise(item) for item in value]
    return value


def _stat_lines(key: str, value: Any, *, depth: int) -> list[str]:
    """Render one statistic as markdown bullets, recursing into nested blobs.

    Recursion is not decoration. A statistics blob two levels deep -- a study's
    populations, each holding a dict of quantiles -- used to bottom out at
    ``str(dict)`` and put ``{'p50': Decimal('0.75')}`` in front of the reader,
    which is a Python repr rather than a number. Anything a card renders has to
    be readable by someone who has never seen the code that produced it.
    """
    indent = "    " * depth
    if isinstance(value, dict):
        lines = [f"{indent}- **{key}**" if depth == 0 else f"{indent}- {key}"]
        for inner_key, inner_value in sorted(value.items()):
            lines.extend(_stat_lines(str(inner_key), inner_value, depth=depth + 1))
        return lines
    label = f"**{key}**" if depth == 0 else key
    return [f"{indent}- {label}: {_render_scalar(value)}"]


def _render_scalar(value: Any) -> str:
    """Integers grouped for legibility, Decimals plain, everything else as written."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _as_plain(value: Any) -> Any:
    """Render Decimals as JSON numbers for the human-readable card."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _as_plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_plain(item) for item in value]
    return value
