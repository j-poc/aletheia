"""Two-sided positive controls: break each fix on purpose, prove a test catches it.

A green suite says the tests pass. It does not say they would have failed. Those
are different claims, and only the second one is evidence -- a test that passes
against both the correct implementation and the broken one measures nothing, and
looks exactly like a test that works.

Every entry below reverts one fix to the precise form the bug had, or to a
degenerate constant that would make a branch unreachable. The named tests must
FAIL. Then the file is restored and they must PASS. Both halves are required: a
poison that is never caught means the test is decorative, and a restore that does
not go green means the harness itself is broken and its "caught" result cannot be
trusted either.

Scope, stated honestly: these mutants cover the point-in-time correctness surface
that decisions D11-D15 are about -- what "restated" means, which report came
first, and what publication order the schema agrees on. That is where every
observed defect in this system has been. It is not a general mutation-testing
sweep, and passing it says nothing about code outside the listed files.

Why hand-written mutants rather than `mutmut` or `cosmic-ray`: those generate
mutants uniformly (flip a comparison, drop a statement) and most are trivially
caught, so the score is dominated by easy kills and the interesting cases are
diluted. Each mutant here reproduces a defect that actually shipped, cited by
decision record. The number that matters is 9/9, not a percentage.

Run: ``make mutants`` (or ``uv run python scripts/mutation_gate.py``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENGINE = "packages/engine/src/aletheia"
TESTS = "packages/engine/tests/unit"


@dataclass(frozen=True)
class Mutant:
    """One shipped defect, reintroduced verbatim."""

    label: str
    decision: str
    """The decision record this defect is documented in."""
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        label="CLI marker back to report_seq (the original bug)",
        decision="D14",
        path=f"{ENGINE}/cli/main.py",
        old="            if fact.differs_from_first_report:",
        new="            if not fact.is_first_report:",
        tests=(f"{TESTS}/test_cli.py",),
    ),
    Mutant(
        label="CLI marker never says restated",
        decision="D14",
        path=f"{ENGINE}/cli/main.py",
        old="            if fact.differs_from_first_report:",
        new="            if False:",
        tests=(f"{TESTS}/test_cli.py",),
    ),
    Mutant(
        label="value_ever_changed <- endpoint comparison (the original bug)",
        decision="D14",
        path=f"{ENGINE}/api/app.py",
        old='"value_ever_changed": restated.value_ever_changed,',
        new='"value_ever_changed": restated.value != first.value,',
        tests=(f"{TESTS}/test_api.py",),
    ),
    Mutant(
        label="value_ever_changed <- always True",
        decision="D14",
        path=f"{ENGINE}/api/app.py",
        old='"value_ever_changed": restated.value_ever_changed,',
        new='"value_ever_changed": True,',
        tests=(f"{TESTS}/test_api.py",),
    ),
    Mutant(
        label="uses_restated_input back to report_seq (the original bug)",
        decision="D14",
        path=f"{ENGINE}/features/accruals.py",
        old="return any(item.differs_from_first_report for item in self.inputs)",
        new="return any(item.report_seq > 1 for item in self.inputs)",
        tests=(f"{TESTS}/test_accruals.py",),
    ),
    Mutant(
        label="differs_from_first_report <- always false, in the view itself",
        decision="D14",
        path=f"{ENGINE}/store/migrations/005_fact_value_chain.sql",
        old="(f.value IS DISTINCT FROM first_value(f.value) OVER w_ordered)",
        new="(f.value IS NOT DISTINCT FROM f.value AND false)",
        tests=(f"{TESTS}/test_cli.py", f"{TESTS}/test_accruals.py", f"{TESTS}/test_pit.py"),
    ),
    Mutant(
        label="period_distinct_values <- cumulative count (the ordered-window trap)",
        decision="D14",
        path=f"{ENGINE}/store/migrations/005_fact_value_chain.sql",
        old="count(DISTINCT f.value) OVER w_partition AS period_distinct_values",
        new="count(DISTINCT f.value) OVER w_ordered AS period_distinct_values",
        tests=(f"{TESTS}/test_api.py", f"{TESTS}/test_pit.py"),
    ),
    Mutant(
        label="revision view back to filed_at ordering (migration 001's stale window)",
        decision="D15",
        path=f"{ENGINE}/store/migrations/006_revisions_on_pit.sql",
        old="    ORDER BY knowledge_date, accn",
        new="    ORDER BY filed_at, accn",
        tests=(f"{TESTS}/test_store.py",),
    ),
    Mutant(
        label="revisions() back to its own inline window over v_facts_pit",
        decision="D15",
        path=f"{ENGINE}/pit/view.py",
        old="              FROM v_fact_revisions",
        new=(
            "              FROM (SELECT *, LAG(value) OVER w AS prior_value,"
            " LAG(knowledge_date) OVER w AS prior_knowledge_date FROM v_facts_pit"
            " WINDOW w AS (PARTITION BY cik, taxonomy, concept, unit, period_start,"
            " period_end ORDER BY filed_at, accn))"
        ),
        tests=(f"{TESTS}/test_store.py", f"{TESTS}/test_pit.py"),
    ),
)


def _tracked_and_dirty(paths: tuple[str, ...]) -> list[str]:
    """Which of ``paths`` git reports as modified."""
    result = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain", "--", *paths],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Not a git checkout (a tarball, say). The finally-block restore still
        # runs; the caller just loses `git checkout` as a recovery path.
        return []
    return [line[3:] for line in result.stdout.splitlines() if line.strip()]


def _run(tests: tuple[str, ...]) -> bool:
    """True when the named tests pass."""
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *tests,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    targets = tuple(sorted({mutant.path for mutant in MUTANTS}))
    for missing in (p for p in targets if not (ROOT / p).exists()):
        print(f"FAIL  target file does not exist: {missing}")
        return 1

    # This harness rewrites tracked source files in place, so every target is
    # copied to disk first. In-memory restore in a `finally` handles the normal
    # path, but "runs in finally" is no guarantee against SIGKILL, and this gate
    # runs inside `make verify` -- which is exactly when the tree is expected to
    # be dirty, because verifying is what you do *before* committing. An earlier
    # version refused on a dirty tree for safety; that made the safety check
    # block its own gate on the normal mid-work state, which is worse than the
    # hazard it prevented. The backup is the real protection, and unlike
    # `git checkout` it also covers a tree that was never committed.
    backup_dir = Path(tempfile.mkdtemp(prefix="aletheia-mutants-"))
    for path in targets:
        copy = backup_dir / path.replace("/", "__")
        copy.write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")

    dirty = _tracked_and_dirty(targets)
    if dirty:
        print("NOTE  these target files carry uncommitted changes:")
        for path in dirty:
            print(f"          {path}")
        print(f"      Originals copied to {backup_dir} for the duration of the run.")
        print()

    survivors: list[Mutant] = []
    for mutant in MUTANTS:
        target = ROOT / mutant.path
        original = target.read_text(encoding="utf-8")
        if mutant.old not in original:
            print(f"FAIL  {mutant.label}\n        anchor no longer present in {mutant.path}")
            print("        The code moved. Update the mutant, or it is silently testing nothing.")
            survivors.append(mutant)
            continue

        target.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        try:
            caught = not _run(mutant.tests)
        finally:
            target.write_text(original, encoding="utf-8")
        # The restore half is not ceremony: if the suite does not go green again,
        # the environment is broken and the "caught" result above proves nothing.
        healed = _run(mutant.tests)

        ok = caught and healed
        if not ok:
            survivors.append(mutant)
        print(
            f"{'PASS' if ok else 'FAIL'}  [{mutant.decision}] {mutant.label}\n"
            f"        mutated -> {'FAILED (caught)' if caught else 'PASSED (SURVIVED)'}"
            f"   restored -> {'PASSED' if healed else 'FAILED (harness broken)'}"
        )

    # Restoration is asserted, not assumed. Every target must match the copy
    # taken before the first mutation; if one does not, the backup directory is
    # kept and named rather than cleaned up, because it is now the only surviving
    # copy of that file.
    print()
    unrestored = [
        path
        for path in targets
        if (ROOT / path).read_text(encoding="utf-8")
        != (backup_dir / path.replace("/", "__")).read_text(encoding="utf-8")
    ]
    if unrestored:
        print("FAIL  these files did not come back byte-identical:")
        for path in unrestored:
            print(f"          {path}")
        print(f"      Originals are in {backup_dir} -- restore them from there.")
        return 1
    shutil.rmtree(backup_dir, ignore_errors=True)

    if survivors:
        print(f"{len(survivors)} of {len(MUTANTS)} mutant(s) survived:")
        for mutant in survivors:
            print(f"    [{mutant.decision}] {mutant.label}")
        return 1
    print(f"all {len(MUTANTS)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
