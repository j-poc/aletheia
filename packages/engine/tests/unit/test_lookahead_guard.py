"""The lookahead guard, tested adversarially.

Filtering by knowledge date is necessary but not sufficient. The realistic
failure is not "someone forgot the filter" — it is "the filter is subtly wrong",
and a wrong filter cannot catch itself. So there are two more layers, and this
file attacks both:

* an **import boundary** that makes the warehouse unreachable from research code;
* a **runtime canary** that re-checks every row on the way out.

Each is tested two-sided: the honest case must pass, and a deliberately
constructed violation must fail. A guard that has never been seen to fire is not
known to work.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import aletheia
from aletheia.core.errors import LookaheadViolation
from aletheia.pit import as_of
from aletheia.pit.view import PitView
from aletheia.store.db import Warehouse
from tests._factories import FY2008_END, first_report, restatement

PACKAGE_ROOT = Path(aletheia.__file__).parent

# Packages that must never touch the warehouse directly. Everything they read
# has to arrive through aletheia.pit, which applies the knowledge-date filter.
RESEARCH_PACKAGES = ("features", "research", "book")
FORBIDDEN_IMPORTS = ("aletheia.store", "duckdb")

AAPL = 320193


class TestImportBoundary:
    def test_research_packages_cannot_reach_the_warehouse(self) -> None:
        """If this fails, some module can read raw rows with no knowledge date."""
        offences = [
            f"{path.relative_to(PACKAGE_ROOT)} imports {module}"
            for path in _research_modules()
            for module in _imported_modules(path)
            if module.startswith(FORBIDDEN_IMPORTS)
        ]
        assert offences == [], (
            "research code must read through aletheia.pit, never the store:\n  "
            + "\n  ".join(offences)
        )

    def test_the_boundary_check_can_actually_fail(self, tmp_path: Path) -> None:
        """Positive control.

        A guard that only ever reports "clean" is indistinguishable from a broken
        one, so the same detector is pointed at a file that genuinely violates the
        rule and must flag it.
        """
        planted = tmp_path / "leaky.py"
        planted.write_text("from aletheia.store.db import Warehouse\n", encoding="utf-8")
        found = [m for m in _imported_modules(planted) if m.startswith(FORBIDDEN_IMPORTS)]
        assert found == ["aletheia.store.db"]

    def test_the_detector_sees_plain_import_statements_too(self, tmp_path: Path) -> None:
        planted = tmp_path / "leaky2.py"
        planted.write_text("import duckdb\n", encoding="utf-8")
        assert "duckdb" in _imported_modules(planted)

    def test_pit_itself_is_allowed_to_reach_the_store(self) -> None:
        """Control: the door is supposed to touch both sides."""
        modules = list(_imported_modules(PACKAGE_ROOT / "pit" / "view.py"))
        assert any(module.startswith("aletheia.store") for module in modules)


class TestRuntimeCanary:
    def test_honest_queries_pass_the_canary(self, warehouse: Warehouse) -> None:
        """Control: the guard must not fire on correct data."""
        warehouse.write_facts([first_report(), restatement()])
        value = as_of(warehouse, date(2009, 12, 1)).value(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert value == Decimal("5.36")

    def test_a_row_that_slips_past_the_filter_is_caught_on_the_way_out(
        self, warehouse: Warehouse, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The attack: simulate a WHERE clause that fails to exclude the future.

        The query layer is subverted so it returns the restatement — published
        2010-01-25 — to a view as of 2009-12-01. This is precisely what a wrong
        predicate would do, and the filter cannot detect it because the filter is
        the broken part. The canary must.
        """
        warehouse.write_facts([first_report(), restatement()])
        view = as_of(warehouse, date(2009, 12, 1))
        original_execute = warehouse.execute

        def leaky_execute(sql: str, params: Sequence[object] | None = None) -> object:
            # Neuter the knowledge-date bound while still consuming its parameter,
            # so the rest of the query — and the argument count — is untouched.
            # This is what an off-by-one or inverted predicate looks like from the
            # outside: the right shape, the wrong rows.
            return original_execute(
                sql.replace("knowledge_date <= ?", "(? IS NOT NULL OR TRUE)"), params
            )

        monkeypatch.setattr(warehouse, "execute", leaky_execute)

        with pytest.raises(LookaheadViolation) as caught:
            view.value(AAPL, "EarningsPerShareDiluted", period_end=FY2008_END)
        assert caught.value.offending_filed_at == date(2010, 1, 25)
        assert caught.value.as_of == date(2009, 12, 1)

    def test_the_violation_names_the_dates_involved(self, warehouse: Warehouse) -> None:
        view = PitView(warehouse, date(2009, 12, 1))
        with pytest.raises(LookaheadViolation, match="became knowable on 2010-01-25"):
            view._assert_knowable(date(2010, 1, 25), "EarningsPerShareDiluted")  # noqa: SLF001

    def test_the_canary_permits_the_boundary_date(self, warehouse: Warehouse) -> None:
        """Knowable *on* the as-of date is knowable, not future."""
        view = PitView(warehouse, date(2010, 1, 25))
        view._assert_knowable(date(2010, 1, 25), "same-day filing")  # noqa: SLF001

    def test_the_unsafe_accessor_deliberately_bypasses_the_canary(
        self, warehouse: Warehouse
    ) -> None:
        """Documented escape hatch: it must work, and it must be hard to reach.

        Its purpose is measuring the bias a vendor panel introduces. The naming is
        the safeguard — nobody writes `unsafe_latest_restated` by accident.
        """
        warehouse.write_facts([first_report(), restatement()])
        fact = as_of(warehouse, date(2009, 12, 1)).unsafe_latest_restated(
            AAPL, "EarningsPerShareDiluted", period_end=FY2008_END
        )
        assert fact.value == Decimal("6.78")
        assert fact.knowledge_date == date(2010, 1, 25)


def _research_modules() -> Iterator[Path]:
    for package in RESEARCH_PACKAGES:
        yield from sorted((PACKAGE_ROOT / package).rglob("*.py"))


def _imported_modules(path: Path) -> Iterator[str]:
    """Every module name a file imports, from both import forms."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
