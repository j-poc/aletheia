"""How a fundamental value is looked up, and when it is deemed knowable.

Using restated data in a backtest is usually described as one mistake. It is
two, and they are separable:

* **The value channel.** You use the number as it stands today rather than as it
  was first published. Apple's FY2008 diluted EPS was 5.36 when it was filed and
  6.78 after the restatement -- a 26% difference on a headline input.
* **The timing channel.** You attach the number to the *period* it describes
  rather than to the date it was published. A commercial panel indexed by fiscal
  period invites exactly this: FY2008 sits in a row labelled 2008-09-27, and a
  careless join hands it to a simulation trading in October 2008, three months
  before the 10-K existed.

Most published replications suffer both at once, which makes the combined damage
easy to measure and the decomposition rare. Naming the three configurations here
lets one study run all of them over an identical universe, identical dates and an
identical price panel, so the difference between arms is attributable to the
data-vintage policy and nothing else.

Note which way the arms are expected to differ: the naive arm is *not* uniformly
better. Restated values are more accurate about what a firm eventually turned out
to have earned, so a signal computed from them can look either stronger or weaker
depending on whether the market was reacting to the reported number or to the
truth. That the sign is not known in advance is what makes the measurement worth
making.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

from aletheia.core.types import Cik
from aletheia.pit import PitFact, PitView


@dataclass(frozen=True, slots=True)
class Vintage:
    """A data-vintage policy: which value to read, and when to call it knowable."""

    name: str
    use_restated: bool
    """Read the value as it stands today instead of as first published."""
    date_at_period_end: bool
    """Treat the value as knowable at the end of the period it describes."""
    description: str

    def resolve(
        self,
        view: PitView,
        cik: Cik | int,
        concept: str,
        *,
        period_end: date,
        period_start: date | None = None,
        unit: str | None = None,
    ) -> PitFact:
        """Fetch ``concept`` under this policy.

        The returned fact always carries the ``knowledge_date`` this policy
        implies, so downstream code never has to remember which arm it is in.

        Arms that respect filing dates go through :meth:`PitView.first_reported`,
        which is guarded: if the period had not been filed by ``view.as_of`` it
        raises rather than returning a number. Arms that model the timing error
        must be able to see past that guard -- that is the error being modelled --
        so they read through ``unsafe_latest_restated``, which is unguarded by
        design and named to make every such call visible in a grep.

        For :data:`RESTATED_VALUES` the restated figure is the one standing
        *today*, not the one standing at ``view.as_of``. That is deliberate: it
        models the actual mistake, which is downloading a vendor panel now and
        running it over history, rather than a hypothetical 2015 researcher with
        a 2015 panel.
        """
        if self.date_at_period_end:
            naive = view.unsafe_latest_restated(
                cik, concept, period_end=period_end, period_start=period_start, unit=unit
            )
            return _replace_dates(naive, knowledge_date=period_end)

        first = view.first_reported(
            cik, concept, period_end=period_end, period_start=period_start, unit=unit
        )
        if not self.use_restated:
            return first
        restated = view.unsafe_latest_restated(
            cik,
            concept,
            period_end=period_end,
            # `first.pin`, not `first.period_start`: the latter is None for a
            # balance-sheet instant, and None means "do not filter", so this
            # widened to every period sharing the end date and raised
            # AmbiguousPeriod on exactly the queries it was meant to narrow.
            period_start=first.pin,
            unit=first.unit,
        )
        # Only the *value* comes from the restatement; the publication date stays
        # honest, which is what isolates the value channel from the timing channel.
        return _replace_dates(restated, knowledge_date=first.knowledge_date)


def _replace_dates(fact: PitFact, *, knowledge_date: date) -> PitFact:
    return PitFact(
        cik=fact.cik,
        taxonomy=fact.taxonomy,
        concept=fact.concept,
        unit=fact.unit,
        period_start=fact.period_start,
        period_end=fact.period_end,
        value=fact.value,
        accn=fact.accn,
        form=fact.form,
        filed_at=fact.filed_at,
        knowledge_date=knowledge_date,
        report_seq=fact.report_seq,
        source_uri=fact.source_uri,
        content_sha256=fact.content_sha256,
    )


FIRST_REPORTED: Final = Vintage(
    name="first_reported",
    use_restated=False,
    date_at_period_end=False,
    description=(
        "Values as originally published, dated when the filing became public. "
        "What a practitioner actually had. The control arm."
    ),
)

RESTATED_VALUES: Final = Vintage(
    name="restated_values",
    use_restated=True,
    date_at_period_end=False,
    description=(
        "Values as they stand today, but still dated when the period was first "
        "reported. Isolates the value channel: the only difference from the "
        "control is that revisions have been folded back in."
    ),
)

NAIVE_VENDOR: Final = Vintage(
    name="naive_vendor",
    use_restated=True,
    date_at_period_end=True,
    description=(
        "Values as they stand today, dated at period end. Both channels at once "
        "-- how a fiscal-period-indexed vendor panel behaves when joined to "
        "returns without a filing-date guard."
    ),
)

ALL_VINTAGES: Final = (FIRST_REPORTED, RESTATED_VALUES, NAIVE_VENDOR)
