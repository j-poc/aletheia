"""Domain types.

Two classic EDGAR client bugs live here and are tested against: conflating the
bare and zero-padded CIK forms, and accepting a malformed accession number that
then silently fails to match anything.
"""

from __future__ import annotations

from datetime import date

import pytest

from aletheia.core.types import Accession, Cik, Fact
from tests._factories import first_report, make_filing


class TestCik:
    def test_pads_to_ten_digits_for_edgar_paths(self) -> None:
        assert Cik(320193).padded == "0000320193"

    def test_behaves_as_an_int_for_storage(self) -> None:
        assert Cik(320193) == 320193
        assert int(Cik("320193")) == 320193

    def test_accepts_a_zero_padded_string(self) -> None:
        assert Cik("0000320193") == 320193

    @pytest.mark.parametrize("bad", [0, -1, 10**10])
    def test_rejects_out_of_range(self, bad: int) -> None:
        with pytest.raises(ValueError, match="CIK out of range"):
            Cik(bad)


class TestAccession:
    def test_parses_the_dashed_form(self) -> None:
        assert Accession.parse("0001193125-09-214859").value == "0001193125-09-214859"

    def test_parses_the_bare_eighteen_digit_form(self) -> None:
        """EDGAR archive paths use the undashed form; the APIs use the dashed one."""
        assert Accession.parse("000119312509214859").value == "0001193125-09-214859"

    def test_exposes_the_bare_form_for_archive_urls(self) -> None:
        assert Accession("0001193125-09-214859").bare == "000119312509214859"

    @pytest.mark.parametrize("bad", ["", "123", "0001193125-9-214859", "0001193125-09-21485"])
    def test_rejects_malformed_input(self, bad: str) -> None:
        with pytest.raises(ValueError, match="malformed accession number"):
            Accession(bad)

    def test_is_hashable_and_orderable(self) -> None:
        pair = sorted({Accession("0001193125-10-012091"), Accession("0001193125-09-214859")})
        assert pair[0].value.endswith("214859")


class TestFact:
    def test_filing_lag_is_period_end_to_publication(self) -> None:
        fact = first_report()
        assert fact.period_end == date(2008, 9, 27)
        assert fact.filed_at == date(2009, 10, 27)
        assert fact.filing_lag_days == 395  # a restated prior year, filed with FY2009

    def test_identity_includes_the_accession(self) -> None:
        """Two filings of the same period are different facts, not duplicates.

        If accn were excluded, the restatement would collapse into the original
        and the entire revision dataset would vanish.
        """
        original = first_report()
        restated = Fact(**{**_as_dict(original), "accn": Accession("0001193125-10-012091")})
        assert original.identity() != restated.identity()

    def test_instantaneous_facts_have_no_period_start(self) -> None:
        from tests._factories import make_fact

        balance_sheet_item = make_fact(
            value="1000", filed_at=date(2010, 1, 25), accn="0001193125-10-012091",
            concept="Assets", unit="USD", period_start=None,
        )  # fmt: skip
        assert balance_sheet_item.is_instantaneous


class TestFiling:
    def test_detects_item_4_02_non_reliance(self) -> None:
        """The single most severe accounting red flag a filing can carry."""
        filing = make_filing(
            accn="0001193125-10-012091", filed_at=date(2010, 1, 25), form="8-K", items=("4.02",)
        )
        assert filing.has_non_reliance_item

    def test_detects_the_sub_item_variants(self) -> None:
        filing = make_filing(
            accn="0001193125-10-012091", filed_at=date(2010, 1, 25), form="8-K",
            items=("2.02", "4.02(b)"),
        )  # fmt: skip
        assert filing.has_non_reliance_item

    def test_ordinary_earnings_release_is_not_flagged(self) -> None:
        filing = make_filing(
            accn="0001193125-10-012091", filed_at=date(2010, 1, 25), form="8-K", items=("2.02",)
        )
        assert not filing.has_non_reliance_item


def _as_dict(fact: Fact) -> dict[str, object]:
    return {field: getattr(fact, field) for field in Fact.__slots__}
