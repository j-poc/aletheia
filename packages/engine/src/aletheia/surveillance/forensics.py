"""Forensic scoring of filings, for triage.

**What this is.** An ordinal ranking that puts the filings most worth a human's
attention at the top of a list. Each flag is an event with a documented basis in
the accounting-forensics literature, and the score is a weighted count of the
flags that fired.

**What this is not, and will not pretend to be.** A calibrated probability of
anything. Calibration needs labelled outcomes -- filings followed by a known
restatement, enforcement action or collapse -- and no such labelled set exists in
this warehouse. Reporting "68% probability of fraud" from an uncalibrated weighted
sum would be false precision of the worst kind: it looks like a measurement and is
an opinion. So the output is a score with its components enumerated, plus a
:class:`Confidence` band that says how much the evidence supports paying attention
-- never a yes/no verdict, and never a percentage that has not been fit to
outcomes.

The flags are chosen because each is a *disclosure the company was required to
make*, not a pattern inferred from prices:

* **Item 4.02** -- the company itself stating that previously issued financials
  should no longer be relied upon. There is no stronger accounting red flag,
  because it is a confession.
* **Item 4.01** -- a change of certifying accountant. Auditor changes cluster
  ahead of restatements, especially when they follow a disagreement.
* **NT 10-K / NT 10-Q** -- a notification that a periodic report will be late. The
  filer is required to say why.
* **Filing lag versus the firm's own history** -- a company that has filed within
  60 days for a decade and suddenly takes 95 is telling you something, and the
  comparison is against itself rather than a peer-group average that would be
  dominated by size.
* **Amendment activity** -- an amended periodic report reopens a closed period.
* **Officer departures (Item 5.02) coinciding with any of the above** -- alone it
  is routine and carries almost no weight; alongside a restatement or an auditor
  change it is a different event, so it is scored only as a co-occurrence.

Everything here reads filing *metadata* -- form types, item codes, dates. No
document text is parsed, so nothing depends on an extraction step that could fail
silently.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from aletheia.pit import PitFiling

NON_RELIANCE_ITEM: Final = "4.02"
AUDITOR_CHANGE_ITEM: Final = "4.01"
OFFICER_DEPARTURE_ITEM: Final = "5.02"
LATE_FILING_FORMS: Final = ("NT 10-K", "NT 10-Q", "NT 20-F")
PERIODIC_FORMS: Final = ("10-K", "10-Q", "20-F")

MIN_HISTORY_FOR_LAG: Final = 4
"""Fewer than four prior periodic filings and 'unusual for this filer' has no
meaning, so the lag flag is withheld rather than computed on noise."""

LAG_SIGMA_THRESHOLD: Final = 2.0
"""How far beyond the filer's own habit counts as a break in it."""


class Flag(StrEnum):
    """One observed condition. The reason a filing surfaced."""

    NON_RELIANCE = "non-reliance on prior financials (8-K Item 4.02)"
    AUDITOR_CHANGE = "change of certifying accountant (8-K Item 4.01)"
    LATE_NOTIFICATION = "notification of late filing"
    UNUSUAL_FILING_LAG = "filed far later than this filer's own history"
    AMENDED_PERIODIC = "amended periodic report reopens a closed period"
    DEPARTURE_ALONGSIDE = "officer departure alongside an accounting event"


WEIGHTS: dict[Flag, float] = {
    Flag.NON_RELIANCE: 5.0,
    Flag.AUDITOR_CHANGE: 3.0,
    Flag.LATE_NOTIFICATION: 2.0,
    Flag.UNUSUAL_FILING_LAG: 1.5,
    Flag.AMENDED_PERIODIC: 1.0,
    Flag.DEPARTURE_ALONGSIDE: 1.0,
}
"""Ordinal weights, set by judgement, not fit to outcomes.

They encode one claim only: a company saying its own accounts cannot be relied
upon deserves attention before a company that filed nine days late. Two filings
with the same score are not equally likely to be anything -- they are equally
worth a look."""


class Confidence(StrEnum):
    """How much the evidence supports spending time on this filing.

    A band, not a probability. The distinction is the point: a band says "look at
    this first", a probability would claim a frequency nothing here has measured.
    """

    CONFESSED = "the company has itself disclosed an accounting problem"
    STRONG = "multiple independent flags, or one serious one"
    MODERATE = "one flag that is often benign in isolation"
    WEAK = "a single routine-looking signal"
    NONE = "nothing flagged"


@dataclass(frozen=True, slots=True)
class Finding:
    """One flag, with the evidence that produced it."""

    flag: Flag
    evidence: str
    weight: float


@dataclass(frozen=True, slots=True)
class Assessment:
    """A filing's triage score, with every component shown."""

    accn: str
    cik: int
    form: str
    knowledge_date: date
    findings: tuple[Finding, ...]

    @property
    def score(self) -> float:
        return sum(finding.weight for finding in self.findings)

    @property
    def flags(self) -> tuple[Flag, ...]:
        return tuple(finding.flag for finding in self.findings)

    @property
    def confidence(self) -> Confidence:
        if not self.findings:
            return Confidence.NONE
        if Flag.NON_RELIANCE in self.flags:
            return Confidence.CONFESSED
        if len(self.findings) > 1 or self.score >= WEIGHTS[Flag.AUDITOR_CHANGE]:
            return Confidence.STRONG
        if self.score >= WEIGHTS[Flag.LATE_NOTIFICATION]:
            return Confidence.MODERATE
        return Confidence.WEAK

    def explain(self) -> str:
        if not self.findings:
            return f"{self.form} {self.accn}: nothing flagged"
        reasons = "; ".join(f"{finding.evidence}" for finding in self.findings)
        return (
            f"{self.form} {self.accn} (CIK {self.cik}, {self.knowledge_date}) "
            f"score {self.score:.1f} [{self.confidence.name}] — {reasons}"
        )


def assess(
    filing: PitFiling,
    *,
    filer_history: Sequence[PitFiling] = (),
) -> Assessment:
    """Score one filing against its own filer's history.

    ``filer_history`` is the same filer's *earlier* periodic filings. Passing later
    ones would let a habit established in 2020 judge a filing from 2015, so the
    caller must supply history that was already public -- and the function checks
    rather than trusting it.
    """
    findings: list[Finding] = []
    items = set(filing.items)

    if any(item.startswith(NON_RELIANCE_ITEM) for item in items):
        findings.append(
            Finding(
                flag=Flag.NON_RELIANCE,
                evidence=f"8-K Item {NON_RELIANCE_ITEM} filed {filing.knowledge_date}",
                weight=WEIGHTS[Flag.NON_RELIANCE],
            )
        )
    if any(item.startswith(AUDITOR_CHANGE_ITEM) for item in items):
        findings.append(
            Finding(
                flag=Flag.AUDITOR_CHANGE,
                evidence=f"8-K Item {AUDITOR_CHANGE_ITEM} filed {filing.knowledge_date}",
                weight=WEIGHTS[Flag.AUDITOR_CHANGE],
            )
        )
    if filing.form in LATE_FILING_FORMS:
        findings.append(
            Finding(
                flag=Flag.LATE_NOTIFICATION,
                evidence=f"{filing.form} filed {filing.knowledge_date}",
                weight=WEIGHTS[Flag.LATE_NOTIFICATION],
            )
        )
    if filing.form.endswith("/A") and filing.form[:-2] in PERIODIC_FORMS:
        findings.append(
            Finding(
                flag=Flag.AMENDED_PERIODIC,
                evidence=f"{filing.form} amends a previously filed period",
                weight=WEIGHTS[Flag.AMENDED_PERIODIC],
            )
        )

    lag_finding = _unusual_lag(filing, filer_history)
    if lag_finding is not None:
        findings.append(lag_finding)

    # Scored only as a co-occurrence: an officer leaving is one of the most common
    # 8-K items there is, and weighting it alone would bury the real signals.
    if any(item.startswith(OFFICER_DEPARTURE_ITEM) for item in items) and findings:
        findings.append(
            Finding(
                flag=Flag.DEPARTURE_ALONGSIDE,
                evidence=f"8-K Item {OFFICER_DEPARTURE_ITEM} alongside {findings[0].flag.name}",
                weight=WEIGHTS[Flag.DEPARTURE_ALONGSIDE],
            )
        )

    return Assessment(
        accn=filing.accn.value,
        cik=int(filing.cik),
        form=filing.form,
        knowledge_date=filing.knowledge_date,
        findings=tuple(findings),
    )


def _unusual_lag(filing: PitFiling, history: Sequence[PitFiling]) -> Finding | None:
    """Did this filer take far longer than it usually does?

    Compared against the filer's own distribution rather than a peer average: a
    cross-sectional comparison is dominated by size and accelerated-filer status,
    so it flags small companies for being small.
    """
    if filing.form not in PERIODIC_FORMS or filing.period_of_report is None:
        return None
    prior_lags = [
        (past.knowledge_date - past.period_of_report).days
        for past in history
        if past.period_of_report is not None
        and past.form == filing.form
        and past.knowledge_date < filing.knowledge_date
    ]
    if len(prior_lags) < MIN_HISTORY_FOR_LAG:
        return None

    lag = (filing.knowledge_date - filing.period_of_report).days
    mean = statistics.fmean(prior_lags)
    deviation = statistics.stdev(prior_lags)
    if deviation <= 0:
        # A filer that has always taken exactly the same number of days: any
        # departure at all is the break, but one day is not worth a flag.
        return (
            Finding(
                flag=Flag.UNUSUAL_FILING_LAG,
                evidence=f"{lag}d versus an unvarying {mean:.0f}d over {len(prior_lags)} filings",
                weight=WEIGHTS[Flag.UNUSUAL_FILING_LAG],
            )
            if lag > mean + 5
            else None
        )
    if lag <= mean + LAG_SIGMA_THRESHOLD * deviation:
        return None
    return Finding(
        flag=Flag.UNUSUAL_FILING_LAG,
        evidence=(
            f"{lag}d versus {mean:.0f}d ± {deviation:.0f}d over "
            f"{len(prior_lags)} prior {filing.form} filings"
        ),
        weight=WEIGHTS[Flag.UNUSUAL_FILING_LAG],
    )


def rank(assessments: Sequence[Assessment]) -> list[Assessment]:
    """Highest score first; ties broken by accession so the order is total.

    A ranked feed whose order changed between runs would be unusable for triage --
    and, since accession numbers are unique, sorting on one makes the order depend
    on the data alone.
    """
    return sorted(
        (item for item in assessments if item.findings),
        key=lambda item: (-item.score, item.accn),
    )
