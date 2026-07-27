"""Forensic triage scoring.

Two things are being defended. First, that each flag fires on the condition it
names and stays silent otherwise -- every positive case is paired with a negative
control, since a scorer that flagged everything would rank nothing. Second, that
the filing-lag comparison is against the filer's own history and cannot be
computed from filings that were not yet public.
"""

from __future__ import annotations

from datetime import date

import pytest

from aletheia.core.types import Accession, Cik
from aletheia.pit import PitFiling
from aletheia.surveillance.forensics import (
    Confidence,
    Flag,
    assess,
    rank,
)

CIK = Cik(320193)


def _filing(
    *,
    form: str = "8-K",
    items: tuple[str, ...] = (),
    knowledge_date: date = date(2016, 5, 10),
    period_of_report: date | None = None,
    accn: str = "0000000000-16-000001",
) -> PitFiling:
    return PitFiling(
        accn=Accession(accn),
        cik=CIK,
        form=form,
        filed_at=knowledge_date,
        disseminated_at=knowledge_date,
        knowledge_date=knowledge_date,
        period_of_report=period_of_report,
        items=items,
    )


def _history(lags: list[int], *, form: str = "10-Q") -> list[PitFiling]:
    """Prior filings of the same form, each with a stated lag from period end."""
    filings = []
    for index, lag in enumerate(lags):
        period_end = date(2010 + index, 3, 31)
        filings.append(
            _filing(
                form=form,
                period_of_report=period_end,
                knowledge_date=date.fromordinal(period_end.toordinal() + lag),
                accn=f"0000000000-1{index}-000009",
            )
        )
    return filings


class TestConfessedProblems:
    def test_item_402_is_the_strongest_flag(self) -> None:
        result = assess(_filing(items=("4.02", "9.01")))
        assert Flag.NON_RELIANCE in result.flags
        assert result.confidence is Confidence.CONFESSED

    def test_an_ordinary_8k_is_not_flagged(self) -> None:
        """Negative control. Item 2.02 is a routine earnings release."""
        result = assess(_filing(items=("2.02", "9.01")))
        assert result.findings == ()
        assert result.confidence is Confidence.NONE
        assert "nothing flagged" in result.explain()

    def test_an_auditor_change_is_flagged_below_a_non_reliance(self) -> None:
        auditor = assess(_filing(items=("4.01",)))
        non_reliance = assess(_filing(items=("4.02",)))
        assert Flag.AUDITOR_CHANGE in auditor.flags
        assert auditor.score < non_reliance.score

    def test_sub_items_are_matched_by_prefix(self) -> None:
        """EDGAR writes 4.02 as '4.02' or with a trailing qualifier."""
        assert Flag.NON_RELIANCE in assess(_filing(items=("4.02(a)",))).flags


class TestFormBasedFlags:
    def test_a_late_notification_is_flagged(self) -> None:
        assert Flag.LATE_NOTIFICATION in assess(_filing(form="NT 10-K")).flags

    def test_an_ordinary_periodic_report_is_not(self) -> None:
        assert assess(_filing(form="10-K", period_of_report=date(2015, 12, 31))).findings == ()

    def test_an_amended_periodic_report_is_flagged(self) -> None:
        assert Flag.AMENDED_PERIODIC in assess(_filing(form="10-K/A")).flags

    def test_an_amended_8k_is_not_treated_as_a_reopened_period(self) -> None:
        """8-K/A amends an event disclosure, not a reporting period."""
        assert Flag.AMENDED_PERIODIC not in assess(_filing(form="8-K/A")).flags


class TestFilingLag:
    def test_a_filer_breaking_its_own_habit_is_flagged(self) -> None:
        history = _history([40, 41, 39, 40, 42])
        late = _filing(
            form="10-Q",
            period_of_report=date(2016, 3, 31),
            knowledge_date=date(2016, 7, 15),  # 106 days
        )
        result = assess(late, filer_history=history)
        assert Flag.UNUSUAL_FILING_LAG in result.flags
        assert "106d versus 40d" in result.explain()

    def test_a_filer_keeping_its_habit_is_not_flagged(self) -> None:
        """Negative control: the same history, an on-time filing."""
        history = _history([40, 41, 39, 40, 42])
        on_time = _filing(
            form="10-Q",
            period_of_report=date(2016, 3, 31),
            knowledge_date=date(2016, 5, 11),  # 41 days
        )
        assert assess(on_time, filer_history=history).findings == ()

    def test_a_habitually_slow_filer_is_judged_against_itself(self) -> None:
        """The reason the comparison is not cross-sectional.

        A company that always takes 85 days is not flagged for taking 86. A
        peer-group comparison would flag it every quarter for being small.
        """
        history = _history([85, 86, 84, 85, 87])
        usual = _filing(
            form="10-Q",
            period_of_report=date(2016, 3, 31),
            knowledge_date=date(2016, 6, 25),  # 86 days
        )
        assert assess(usual, filer_history=history).findings == ()

    def test_too_little_history_withholds_the_flag(self) -> None:
        """'Unusual for this filer' is meaningless with three observations."""
        result = assess(
            _filing(
                form="10-Q",
                period_of_report=date(2016, 3, 31),
                knowledge_date=date(2016, 8, 1),
            ),
            filer_history=_history([40, 41, 39]),
        )
        assert Flag.UNUSUAL_FILING_LAG not in result.flags

    def test_later_filings_cannot_inform_the_comparison(self) -> None:
        """History from the future is not history.

        A habit established after the filing must not be used to judge it. Here the
        only 'prior' filings are dated later, so there is no usable history and the
        flag is withheld rather than computed from them.
        """
        future = [
            _filing(
                form="10-Q",
                period_of_report=date(2020 + index, 3, 31),
                knowledge_date=date(2020 + index, 5, 10),
                accn=f"0000000000-2{index}-000009",
            )
            for index in range(6)
        ]
        result = assess(
            _filing(
                form="10-Q",
                period_of_report=date(2016, 3, 31),
                knowledge_date=date(2016, 9, 1),
            ),
            filer_history=future,
        )
        assert Flag.UNUSUAL_FILING_LAG not in result.flags

    def test_a_different_form_does_not_set_the_baseline(self) -> None:
        """A 10-K's lag says nothing about whether a 10-Q is late."""
        result = assess(
            _filing(
                form="10-Q",
                period_of_report=date(2016, 3, 31),
                knowledge_date=date(2016, 5, 11),
            ),
            filer_history=_history([60, 61, 59, 60, 62], form="10-K"),
        )
        assert Flag.UNUSUAL_FILING_LAG not in result.flags

    def test_a_filing_without_a_period_is_skipped(self) -> None:
        result = assess(
            _filing(form="10-Q", period_of_report=None), filer_history=_history([40, 41, 39, 40])
        )
        assert Flag.UNUSUAL_FILING_LAG not in result.flags


class TestOfficerDepartures:
    def test_a_departure_alone_is_not_flagged(self) -> None:
        """One of the commonest 8-K items there is. Weighting it alone buries the rest."""
        assert assess(_filing(items=("5.02",))).findings == ()

    def test_a_departure_alongside_a_restatement_adds_weight(self) -> None:
        alone = assess(_filing(items=("4.02",)))
        together = assess(_filing(items=("4.02", "5.02")))
        assert Flag.DEPARTURE_ALONGSIDE in together.flags
        assert together.score > alone.score


class TestConfidenceBands:
    def test_a_confession_outranks_everything(self) -> None:
        assert assess(_filing(items=("4.02",))).confidence is Confidence.CONFESSED

    def test_multiple_flags_read_strong(self) -> None:
        result = assess(_filing(form="10-K/A", items=("4.01",)))
        assert result.confidence is Confidence.STRONG

    def test_a_single_light_flag_does_not_read_strong(self) -> None:
        assert assess(_filing(form="10-K/A")).confidence is Confidence.WEAK

    def test_bands_are_never_reported_as_probabilities(self) -> None:
        """The output must not look like a calibrated frequency.

        No labelled outcome set exists here, so a percentage would be an opinion
        wearing a measurement's clothes.
        """
        text = assess(_filing(items=("4.02",))).explain()
        assert "%" not in text


class TestRanking:
    def test_the_worst_filing_comes_first(self) -> None:
        ordered = rank(
            [
                assess(_filing(form="10-K/A", accn="0000000000-16-000003")),
                assess(_filing(items=("4.02",), accn="0000000000-16-000001")),
                assess(_filing(items=("4.01",), accn="0000000000-16-000002")),
            ]
        )
        assert [item.accn for item in ordered] == [
            "0000000000-16-000001",
            "0000000000-16-000002",
            "0000000000-16-000003",
        ]

    def test_unflagged_filings_are_dropped_from_the_feed(self) -> None:
        ordered = rank([assess(_filing(items=("2.02",))), assess(_filing(items=("4.02",)))])
        assert len(ordered) == 1

    def test_ties_break_deterministically(self) -> None:
        """A feed whose order changed between runs would be useless for triage."""
        first = rank(
            [
                assess(_filing(items=("4.01",), accn="0000000000-16-000011")),
                assess(_filing(items=("4.01",), accn="0000000000-16-000010")),
            ]
        )
        second = rank(
            [
                assess(_filing(items=("4.01",), accn="0000000000-16-000010")),
                assess(_filing(items=("4.01",), accn="0000000000-16-000011")),
            ]
        )
        assert [item.accn for item in first] == [item.accn for item in second]

    def test_an_empty_day_produces_an_empty_feed(self) -> None:
        assert rank([]) == []


class TestScoring:
    def test_the_score_is_the_sum_of_its_shown_components(self) -> None:
        """Nothing contributes to the score without appearing in the findings."""
        result = assess(_filing(form="10-K/A", items=("4.02", "5.02")))
        assert result.score == pytest.approx(sum(f.weight for f in result.findings))
        assert len(result.findings) == 3
