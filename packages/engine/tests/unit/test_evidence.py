"""Evidence cards, and the reproducibility hash the determinism gate rests on.

Also covers the kernel's tie-breaking, because that is what the hash is most
sensitive to and the least obvious to a reader: two firms with identical signal
values must always fall on the same side of a quantile boundary, whatever order
the caller happened to build the panel in.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from aletheia.pit import PitPrice
from aletheia.research.evidence import ArmSummary, Comparison, EvidenceCard, Provenance
from aletheia.research.kernel import SignalObservation, run_quantile_sort

SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
VALUES = {
    # Three-way ties at each end, two distinct values in the middle.
    "AAA": -0.1,
    "BBB": -0.1,
    "CCC": -0.1,
    "DDD": 0.0,
    "EEE": 0.01,
    "FFF": 0.1,
    "GGG": 0.1,
    "HHH": 0.1,
}

START = date(2015, 1, 1)
FORMATION = date(2016, 1, 4)
NEXT_FORMATION = date(2016, 7, 4)
CAPITAL = 20_000_000.0


def _bars(symbol: str, *, start: date, end: date) -> list[PitPrice]:
    drift = 1.0 + (sum(ord(char) for char in symbol) % 9) / 100.0
    bars: list[PitPrice] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            close = 100.0 * drift ** ((day - START).days / 365.25)
            bars.append(
                PitPrice(
                    symbol=symbol,
                    bar_date=day,
                    open=close,
                    high=close * 1.004,
                    low=close * 0.996,
                    close=close,
                    adj_close=close,
                    volume=500_000.0,
                    tradable_from=day,
                )
            )
        day += timedelta(days=1)
    return bars


def _run(order: list[str], values: dict[str, float]) -> object:
    panel = [
        SignalObservation(symbol=symbol, cik=index, value=values[symbol], knowledge_date=FORMATION)
        for index, symbol in enumerate(order)
    ]
    return run_quantile_sort(
        label="ties",
        panels={FORMATION: panel, NEXT_FORMATION: panel},
        load_prices=_bars,
        # Four quantiles over eight names gives buckets of two, so each side must
        # choose two of the three tied names. With two quantiles the bucket would
        # be three and every tied name would be traded -- no choice, nothing tested.
        n_quantiles=4,
        capital_usd=CAPITAL,
    )


class TestTieBreaking:
    """Ties at a quantile boundary must not depend on panel construction order.

    A stable sort on the value alone inherits input order, and a panel assembled
    by iterating a ``set`` is ordered differently in every process -- so the
    portfolio, and therefore the reported return, would change between runs.
    """

    def test_the_same_names_are_traded_whatever_the_input_order(self) -> None:
        forward = _run(SYMBOLS, VALUES)
        reversed_order = _run(list(reversed(SYMBOLS)), VALUES)
        shuffled = _run(["DDD", "AAA", "FFF", "CCC", "HHH", "EEE", "BBB", "GGG"], VALUES)

        def traded(result: object) -> set[tuple[str, float]]:
            return {
                (position.symbol, position.weight)
                for position in result.periods[0].positions  # type: ignore[attr-defined]
            }

        assert traded(forward) == traded(reversed_order) == traded(shuffled)

    def test_the_tie_actually_straddles_the_boundary(self) -> None:
        """Guards the test above from becoming vacuous.

        If the fixture ever stopped forcing a choice among tied names -- by
        widening the bucket, say -- the ordering test would pass without testing
        anything. Two of three tied names must be traded per side.
        """
        period = _run(SYMBOLS, VALUES).periods[0]  # type: ignore[attr-defined]
        longs = {position.symbol for position in period.positions if position.weight > 0}
        assert len(longs) == 2, "the bucket must be narrower than the tied group"
        assert longs < {"AAA", "BBB", "CCC"}

    def test_the_return_is_identical_across_orderings(self) -> None:
        forward = _run(SYMBOLS, VALUES).periods[0]  # type: ignore[attr-defined]
        backward = _run(list(reversed(SYMBOLS)), VALUES).periods[0]  # type: ignore[attr-defined]
        assert forward.net_return == backward.net_return


def _card(**overrides: object) -> EvidenceCard:
    result = _run(SYMBOLS, VALUES)
    defaults: dict[str, object] = {
        "study_id": "T001",
        "hypothesis": "a test hypothesis",
        "verdict": "no verdict",
        "provenance": Provenance(
            code_commit="abc123",
            code_dirty=False,
            config_hash="cfg",
            data_vintage=date(2016, 7, 4),
            universe_source="synthetic",
            row_counts={"facts": 10},
        ),
        "arms": (ArmSummary.of(result, periods_per_year=2.0),),  # type: ignore[arg-type]
        "comparisons": (),
        "trial_count": 3,
        "trial_family": "test",
        "caveats": ("a caveat",),
        "generated_at": datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    }
    return EvidenceCard(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestReproHash:
    def test_the_generation_timestamp_does_not_change_the_hash(self) -> None:
        """The one field that legitimately varies between runs is excluded."""
        early = _card(generated_at=datetime(2020, 1, 1, tzinfo=UTC))
        late = _card(generated_at=datetime(2030, 1, 1, tzinfo=UTC))
        assert early.repro_hash == late.repro_hash

    def test_a_changed_result_does_change_the_hash(self) -> None:
        """Control: a hash that never moves would certify nothing."""
        baseline = _card()
        altered = _card(verdict="a different verdict")
        assert baseline.repro_hash != altered.repro_hash

    def test_a_changed_commit_changes_the_hash(self) -> None:
        altered = _card(
            provenance=Provenance(
                code_commit="def456",
                code_dirty=False,
                config_hash="cfg",
                data_vintage=date(2016, 7, 4),
                universe_source="synthetic",
                row_counts={"facts": 10},
            )
        )
        assert _card().repro_hash != altered.repro_hash

    def test_the_hash_is_stable_across_repeated_construction(self) -> None:
        assert _card().repro_hash == _card().repro_hash


class TestCardContent:
    def test_gross_and_net_are_both_reported(self) -> None:
        """Quoting gross alone is how a strategy that dies on costs gets published."""
        card = _card()
        rendered = card.to_markdown()
        assert "Gross p.a." in rendered
        assert "Net p.a." in rendered

    def test_a_dirty_tree_is_stated_in_the_rendered_card(self) -> None:
        """A commit hash from a dirty tree is a lie unless it is labelled."""
        card = _card(
            provenance=Provenance(
                code_commit="abc123",
                code_dirty=True,
                config_hash="cfg",
                data_vintage=date(2016, 7, 4),
                universe_source="synthetic",
            )
        )
        assert "not reproducible" in card.to_markdown()

    def test_caveats_travel_inside_the_card(self) -> None:
        assert "a caveat" in _card().to_markdown()

    def test_the_trial_count_is_on_the_face_of_the_card(self) -> None:
        assert "**3**" in _card().to_markdown()

    def test_json_round_trips_and_carries_the_hash(self) -> None:
        import json

        card = _card()
        payload = json.loads(card.to_json())
        assert payload["repro_hash"] == card.repro_hash
        assert payload["generated_at"].startswith("2026-07-27")
        assert isinstance(payload["arms"][0]["net_annualised"], float)

    def test_comparisons_render_their_interpretation(self) -> None:
        card = _card(
            comparisons=(
                Comparison(
                    name="Value channel",
                    baseline="a",
                    variant="b",
                    metric="net annualised return",
                    baseline_value=0.05,
                    variant_value=0.08,
                    interpretation="what it means",
                ),
            )
        )
        rendered = card.to_markdown()
        assert "Value channel" in rendered
        assert "what it means" in rendered

    def test_a_comparison_reports_its_difference_and_relative_change(self) -> None:
        comparison = Comparison(
            name="c",
            baseline="a",
            variant="b",
            metric="m",
            baseline_value=0.04,
            variant_value=0.06,
            interpretation="",
        )
        assert comparison.difference == pytest.approx(0.02)
        assert comparison.relative == pytest.approx(0.5)

    def test_a_zero_baseline_reports_no_relative_change_rather_than_infinity(self) -> None:
        comparison = Comparison(
            name="c",
            baseline="a",
            variant="b",
            metric="m",
            baseline_value=0.0,
            variant_value=0.06,
            interpretation="",
        )
        assert comparison.relative is None


class TestArmSummary:
    def test_annualising_the_sharpe_scales_both_moments(self) -> None:
        """The v1 failure mode: annualising the mean but not the deviation."""
        result = _run(SYMBOLS, VALUES)
        arm = ArmSummary.of(result, periods_per_year=12.0)  # type: ignore[arg-type]
        assert arm.annualised_sharpe == pytest.approx(arm.sharpe_per_period * 12.0**0.5)

    def test_an_empty_arm_is_refused_rather_than_summarised_as_zero(self) -> None:
        from aletheia.research.kernel import BacktestResult

        empty = BacktestResult(
            label="empty", periods=(), n_quantiles=5, execution_lag_days=1, capital_usd=1.0
        )
        with pytest.raises(ValueError, match="no periods"):
            ArmSummary.of(empty, periods_per_year=12.0)
