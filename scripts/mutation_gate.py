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
first, and what publication order the schema agrees on -- plus D16, the one
leak found outside it. That is where every observed defect in this system has
been. It is not a general mutation-testing sweep, and passing it says nothing
about code outside the listed files.

Why hand-written mutants rather than `mutmut` or `cosmic-ray`: those generate
mutants uniformly (flip a comparison, drop a statement) and most are trivially
caught, so the score is dominated by easy kills and the interesting cases are
diluted. Each mutant here reproduces a defect that actually shipped, cited by
decision record. The number that matters is 10/10, not a percentage.

**The working tree is never written to.** An earlier version mutated the real
files and restored them in a `finally`, which made the source of truth wrong for
the duration of every pytest run -- and this gate runs inside `make verify`,
which is exactly when something else (an editor, a linter, a reviewer, a parallel
session) is most likely to read those files. That happened: a background security
scan read a migration mid-run and reported a defect whose "suggested fix" was
byte-identical to the code on disk. The finding was noise; the window that
produced it was not. Every mutation now lands in a throwaway copy, and imports
are redirected there with ``PYTHONPATH``.

That redirection is itself checked before any mutant runs, because the dangerous
failure mode is silent: if the sandbox were not on the import path the tests
would exercise the real, unmutated code, every mutant would survive, and the gate
would report a suite that catches nothing. :func:`_unredirected_imports` asserts
``aletheia.__file__`` and ``trialkeeper.__file__`` both resolve inside the copy.

Run: ``make mutants`` (or ``uv run python scripts/mutation_gate.py``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENGINE = "packages/engine/src/aletheia"
TESTS = "packages/engine/tests/unit"
TRIALKEEPER = "packages/trialkeeper/src/trialkeeper"
TK_TESTS = "packages/trialkeeper/tests"

SANDBOX_TREES = (
    "packages/engine/src",
    "packages/engine/tests",
    "packages/trialkeeper/src",
    "packages/trialkeeper/tests",
)
"""Copied wholesale into the sandbox: the code under test and the tests themselves.

Both halves are needed. Copying only the sources would leave pytest collecting the
real test files, which import their fixtures and helpers by package-relative path
and would then straddle two trees.
"""

IMPORT_ROOTS = ("packages/engine/src", "packages/trialkeeper/src")
"""Prepended to ``PYTHONPATH`` so ``import aletheia`` finds the copy.

The editable installs in ``.venv`` are plain ``.pth`` path entries rather than a
meta-path finder, so an earlier ``sys.path`` entry shadows them. That is an
implementation detail of the installer and could change, which is why the
redirection is verified at runtime rather than assumed.
"""


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
    Mutant(
        label="purged CV back to backward-only purging (the leak review found)",
        decision="D16",
        path=f"{TRIALKEEPER}/cv.py",
        old=(
            "        low = max(0, int(position) - label_horizon)\n"
            "        high = min(indices.size, int(position) + label_horizon + 1)\n"
            "        purged[low:high] = True"
        ),
        new=(
            "        low = max(0, int(position) - label_horizon)\n"
            "        purged[low : int(position)] = True"
        ),
        tests=(f"{TK_TESTS}/test_pbo_and_cv.py",),
    ),
)


def _build_sandbox() -> Path:
    """A throwaway copy of the source and test trees, mutated in place of the real ones."""
    sandbox = Path(tempfile.mkdtemp(prefix="aletheia-mutants-"))
    for relative in SANDBOX_TREES:
        shutil.copytree(
            ROOT / relative,
            sandbox / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return sandbox


def _sandbox_env(sandbox: Path) -> dict[str, str]:
    """The subprocess environment with imports pointed at ``sandbox``."""
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH", "")
    entries = [str(sandbox / relative) for relative in IMPORT_ROOTS]
    if inherited:
        entries.append(inherited)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def _unredirected_imports(sandbox: Path, env: dict[str, str]) -> list[str]:
    """Empty when both packages import from ``sandbox``; otherwise what went wrong.

    The whole gate rests on this. If the redirection fails, pytest exercises the
    real unmutated code, every mutant survives, and the output is a report that
    the test suite catches nothing -- alarming but wrong, and the true cause
    would not be visible anywhere in it.
    """
    probe = "import aletheia, trialkeeper; print(aletheia.__file__); print(trialkeeper.__file__)"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()
        return [f"import probe failed: {tail[-1] if tail else 'no output'}"]
    resolved = result.stdout.splitlines()
    if len(resolved) != 2:
        return [f"import probe printed {len(resolved)} path(s), expected 2"]
    return [path for path in resolved if not path.startswith(f"{sandbox}{os.sep}")]


def _run(tests: tuple[str, ...], sandbox: Path, env: dict[str, str]) -> bool:
    """True when the named tests -- collected from the sandbox -- pass.

    ``-c`` and ``--rootdir`` are pinned to the real repo because the test paths
    now live under ``/tmp``: without them pytest would look for its config beside
    those paths, find none, and silently drop ``--strict-markers``,
    ``filterwarnings = ["error"]`` and the marker declarations. The tests would
    still run, under quietly different rules.
    """
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--rootdir",
            str(ROOT),
            *[str(sandbox / test) for test in tests],
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _digests(paths: tuple[str, ...]) -> dict[str, str]:
    """sha256 of each path in the real tree."""
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main() -> int:
    targets = tuple(sorted({mutant.path for mutant in MUTANTS}))
    for missing in (p for p in targets if not (ROOT / p).exists()):
        print(f"FAIL  target file does not exist: {missing}")
        return 1

    # Taken from the real tree before anything else happens, and compared again at
    # the end. Under the sandbox design nothing should ever write to these files,
    # so this check is expected to be trivially true -- which is the point. It is
    # cheap, and it is the only thing that would notice if a future edit
    # reintroduced an in-place write.
    before = _digests(targets)

    sandbox = _build_sandbox()
    print(f"sandbox -> {sandbox}   (the working tree is not written to)")

    env = _sandbox_env(sandbox)
    stray = _unredirected_imports(sandbox, env)
    if stray:
        print("FAIL  imports do not resolve to the sandbox, so mutants would test nothing:")
        for line in stray:
            print(f"          {line}")
        shutil.rmtree(sandbox, ignore_errors=True)
        return 1
    print()

    survivors: list[Mutant] = []
    try:
        for mutant in MUTANTS:
            target = sandbox / mutant.path
            original = target.read_text(encoding="utf-8")
            if mutant.old not in original:
                print(f"FAIL  {mutant.label}\n        anchor no longer present in {mutant.path}")
                print(
                    "        The code moved. Update the mutant, or it is silently testing nothing."
                )
                survivors.append(mutant)
                continue

            target.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
            try:
                caught = not _run(mutant.tests, sandbox, env)
            finally:
                target.write_text(original, encoding="utf-8")
            # The restore half is not ceremony: if the suite does not go green
            # again, the environment is broken and the "caught" result above
            # proves nothing.
            healed = _run(mutant.tests, sandbox, env)

            ok = caught and healed
            if not ok:
                survivors.append(mutant)
            print(
                f"{'PASS' if ok else 'FAIL'}  [{mutant.decision}] {mutant.label}\n"
                f"        mutated -> {'FAILED (caught)' if caught else 'PASSED (SURVIVED)'}"
                f"   restored -> {'PASSED' if healed else 'FAILED (harness broken)'}"
            )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    touched = [path for path, digest in before.items() if _digests((path,))[path] != digest]
    if touched:
        print("FAIL  the working tree was modified, which this harness must never do:")
        for path in touched:
            print(f"          {path}")
        print("      Restore from git; the sandbox has been deleted.")
        return 1

    if survivors:
        print(f"{len(survivors)} of {len(MUTANTS)} mutant(s) survived:")
        for mutant in survivors:
            print(f"    [{mutant.decision}] {mutant.label}")
        return 1
    print(f"all {len(MUTANTS)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
