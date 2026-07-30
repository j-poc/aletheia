/**
 * Two-sided positive controls for the web application, mirroring scripts/mutation_gate.py.
 *
 * The Python gate exists because a green suite says the tests pass, not that they
 * would have failed. That is doubly true here: `apps/web` had no tests at all
 * until D26, so the first suite written for it passed on the first run, and a
 * suite that has never been observed failing is indistinguishable from a suite
 * that asserts nothing. Every mutant below breaks one behaviour on purpose; the
 * named test files must FAIL, then the file is restored and they must PASS.
 *
 * Scope, stated honestly. These mutants cover the derivations that turn API
 * booleans into sentences a reader believes, plus the failure/empty distinction
 * every page depends on. That is where the defects have been: the comments in
 * `app/page.tsx` record three separate occasions when this page printed a
 * confident false claim about Apple's FY2008 EPS or AAR Corp's revenue, and none
 * of them had a regression test until now. It is not a general mutation sweep of
 * the front end, and it says nothing about styling, layout, client-side
 * behaviour, or anything a browser does after the markup arrives.
 *
 * Unlike the Python gate, most entries here reproduce a defect that actually
 * shipped -- they are recovered from the source comments that document the fix.
 * The ones that did not ship are marked in their labels.
 *
 * The working tree is never written to. Mutation happens in a throwaway copy and
 * vitest runs with that copy as its root; the copied `vitest.config.ts` resolves
 * its `@` alias relative to itself, so imports follow. That redirection is the
 * one silent failure mode -- if imports resolved back to the real tree, every
 * mutant would survive and the gate would report a suite that catches nothing --
 * so a probe verifies it under a real vitest process before any mutant runs.
 *
 * Run: `make mutants-web` (or `node scripts/web_mutation_gate.mjs`).
 */

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WEB = path.join(ROOT, "apps", "web");

/** Copied wholesale: the code under test, the tests, and the config that binds them. */
const SANDBOX_ENTRIES = ["app", "lib", "tests", "vitest.config.ts", "tsconfig.json", "package.json"];

const PAGE = "app/page.tsx";
const API = "lib/api.ts";
const QUALITY = "app/quality/page.tsx";
const FEED = "app/feed/page.tsx";
const REVISIONS = "app/revisions/page.tsx";
const EVIDENCE = "app/evidence/page.tsx";

const HARNESS = "tests/harness.tsx";

const ASOF_TESTS = ["tests/asof.test.tsx"];
const PAGE_TESTS = ["tests/pages.test.tsx"];
const API_TESTS = ["tests/api.test.ts"];
const HARNESS_TESTS = ["tests/harness.test.tsx"];

/**
 * @typedef {{label: string, decision: string, path: string, old: string, new: string,
 *            tests: string[]}} Mutant
 */

/** @type {Mutant[]} */
const MUTANTS = [
  {
    label: "the restated branch keyed off is_restated again (the shipped bug)",
    decision: "D26",
    path: PAGE,
    old: "            {data.value_changed ? (",
    new: "            {data.is_restated ? (",
    tests: ASOF_TESTS,
  },
  {
    label: "revised-and-revised-back falls through to 'never moved' (the AAR Corp bug)",
    decision: "D26",
    path: PAGE,
    old: "            ) : data.value_ever_changed ? (",
    new: "            ) : false ? (",
    tests: ASOF_TESTS,
  },
  {
    label: "a re-presentation is reported as a restatement, crying wolf on 90.6% of refilings",
    decision: "D26",
    path: PAGE,
    old: "            ) : data.is_restated ? (",
    new: "            ) : true ? (",
    tests: ASOF_TESTS,
  },
  {
    label: "'the left column shows it too' printed above two different numbers (shipped)",
    decision: "D26",
    path: PAGE,
    old: "                ) : data.known_is_current ? (",
    new: "                ) : true ? (",
    tests: ASOF_TESTS,
  },
  {
    label: "the lookahead sentence is shown even once the restatement is old news",
    decision: "D26",
    path: PAGE,
    old: "                {!data.already_restated_by_then ? (",
    new: "                {true ? (",
    tests: ASOF_TESTS,
  },
  {
    label: "the warning colour fires on a new accession rather than a changed value",
    decision: "D26",
    path: PAGE,
    old: "              tone={data.value_ever_changed ? \"restated\" : \"known\"}",
    new: "              tone={data.is_restated ? \"restated\" : \"known\"}",
    tests: ASOF_TESTS,
  },
  {
    label: "an undefined relative change is rendered as a number anyway",
    decision: "D26",
    path: PAGE,
    old: "                {data.relative_drift !== null && (",
    new: "                {true && (",
    tests: ASOF_TESTS,
  },
  {
    label: "a period is labelled by its end date alone, which does not identify it",
    decision: "D26",
    path: PAGE,
    old: "  return `${fact.period_start} to ${fact.period_end} (${days}d)`;",
    new: "  return `period ending ${fact.period_end}`;",
    tests: ASOF_TESTS,
  },
  {
    label: "the first publication is counted as a refiling",
    decision: "D26",
    path: PAGE,
    old: '          value={fact.report_seq === 1 ? "first publication" : `refiling #${fact.report_seq - 1}`}',
    new: '          value={`refiling #${fact.report_seq}`}',
    tests: ASOF_TESTS,
  },
  {
    label: "a blank period start is sent as an empty parameter instead of omitted",
    decision: "D26",
    path: PAGE,
    old: "    ...(params.period_start ? { period_start: params.period_start } : {}),",
    new: "    period_start: params.period_start,",
    tests: ASOF_TESTS,
  },
  {
    label: "the ticker is pasted into the path unescaped",
    decision: "D26",
    path: PAGE,
    old: "    data = await api<AsOf>(`/api/asof/${encodeURIComponent(params.ticker)}?${query}`);",
    new: "    data = await api<AsOf>(`/api/asof/${params.ticker}?${query}`);",
    tests: ASOF_TESTS,
  },
  {
    // Did not ship. It is here because it is the failure this whole page is
    // designed against: the error is swallowed, the page renders empty, and
    // "could not load" becomes indistinguishable from "there is nothing here".
    label: "an API failure renders as an empty page rather than an error (not shipped)",
    decision: "D26",
    path: PAGE,
    old: "      {error && <ErrorPanel title=\"Could not answer that\" detail={error} />}",
    new: "      {false && <ErrorPanel title=\"Could not answer that\" detail={error} />}",
    tests: ASOF_TESTS,
  },
  {
    label: "a dead backend is reported as an empty response instead of an error",
    decision: "D26",
    path: API,
    old: "    throw new ApiError(0, `cannot reach the API at ${BASE}. Is \\`make api\\` running?`);",
    new: "    return {} as T;",
    tests: [...API_TESTS, ...ASOF_TESTS, ...PAGE_TESTS],
  },
  {
    label: "a non-2xx response is treated as success",
    decision: "D26",
    path: API,
    old: "  if (!response.ok) {",
    new: "  if (false) {",
    tests: [...API_TESTS, ...PAGE_TESTS],
  },
  {
    label: "the API's own explanation of a 4xx is replaced by the generic status text",
    decision: "D26",
    path: API,
    old: "    throw new ApiError(response.status, body.detail ?? response.statusText);",
    new: "    throw new ApiError(response.status, response.statusText);",
    tests: API_TESTS,
  },
  {
    label: "a non-JSON error body throws a SyntaxError instead of a usable status",
    decision: "D26",
    path: API,
    old: "    const body = (await response.json().catch(() => ({}))) as { detail?: string };",
    new: "    const body = (await response.json()) as { detail?: string };",
    tests: API_TESTS,
  },
  {
    label: "the point-in-time viewer is allowed to serve a cached answer",
    decision: "D26",
    path: API,
    old: '    response = await fetch(`${BASE}${path}`, { cache: "no-store" });',
    new: "    response = await fetch(`${BASE}${path}`);",
    tests: API_TESTS,
  },
  {
    label: "the EDGAR link keeps the dashes in the directory segment, 404ing every citation",
    decision: "D26",
    path: API,
    old: "  return `https://www.sec.gov/Archives/edgar/data/${cik}/${accn.replace(/-/g, \"\")}/${accn}-index.htm`;",
    new: "  return `https://www.sec.gov/Archives/edgar/data/${cik}/${accn}/${accn}-index.htm`;",
    tests: [...API_TESTS, ...ASOF_TESTS],
  },
  {
    label: "a gain loses its explicit plus sign, so direction reads from context alone",
    decision: "D26",
    path: API,
    old: '  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;',
    new: "  return `${(value * 100).toFixed(2)}%`;",
    tests: API_TESTS,
  },
  {
    label: "an absent change formats as +0.00% instead of failing loudly",
    decision: "D26",
    path: API,
    old: "  if (!Number.isFinite(value)) {",
    new: "  if (false) {",
    tests: API_TESTS,
  },
  {
    label: "a real change too small to render rounds to +0.00%, in a table of changes",
    decision: "D26",
    path: API,
    old: '  if (value !== 0 && /^[+-]?0\\.00%$/.test(rendered)) {',
    new: "  if (false) {",
    tests: [...API_TESTS, ...PAGE_TESTS],
  },
  {
    label: "an exact zero is dressed up as a change too small to show",
    decision: "D26",
    path: API,
    old: "  if (value !== 0 && /^[+-]?0\\.00%$/.test(rendered)) {",
    new: "  if (/^[+-]?0\\.00%$/.test(rendered)) {",
    tests: API_TESTS,
  },
  {
    label: "the revisions table formats with pct(), losing the sub-rounding guard",
    decision: "D26",
    path: REVISIONS,
    old: "{row.relative_change === null ? \"—\" : pctChange(row.relative_change)}",
    new: "{row.relative_change === null ? \"—\" : pct(row.relative_change)}",
    tests: PAGE_TESTS,
  },
  {
    label: "the coverage percentage is printed on an empty warehouse, as NaN",
    decision: "D26",
    path: QUALITY,
    old: "              {data.revision_coverage.distinct_periods > 0 && (",
    new: "              {true && (",
    tests: PAGE_TESTS,
  },
  {
    label: "row counts lose their digit grouping",
    decision: "D26",
    path: QUALITY,
    old: "                    {count.toLocaleString()}",
    new: "                    {String(count)}",
    tests: PAGE_TESTS,
  },
  {
    label: "coverage is reported as a share of changed rather than of distinct periods",
    decision: "D26",
    path: QUALITY,
    old: "                      (100 * data.revision_coverage.periods_with_a_changed_value) /\n                      data.revision_coverage.distinct_periods",
    new: "                      (100 * data.revision_coverage.distinct_periods) /\n                      data.revision_coverage.periods_with_a_changed_value",
    tests: PAGE_TESTS,
  },
  {
    label: "an empty revision result is presented as a failure rather than an answer",
    decision: "D26",
    path: REVISIONS,
    old: "          {data.revisions.length === 0 ? (",
    new: "          {false ? (",
    tests: PAGE_TESTS,
  },
  {
    label: "an undefined relative change is rendered as a number in the revisions table",
    decision: "D26",
    path: REVISIONS,
    old: "{row.relative_change === null ? \"—\" : pctChange(row.relative_change)}",
    new: "{pctChange(row.relative_change as number)}",
    tests: PAGE_TESTS,
  },
  {
    label: "the reader's threshold is ignored and the default is queried instead",
    decision: "D26",
    path: REVISIONS,
    old: "      `/api/revisions/${encodeURIComponent(ticker)}?min_change=${minChange}`,",
    new: "      `/api/revisions/${encodeURIComponent(ticker)}?min_change=0.05`,",
    tests: PAGE_TESTS,
  },
  {
    label: "a quiet day in the feed is presented as a failure rather than an answer",
    decision: "D26",
    path: FEED,
    old: "          {data.items.length === 0 ? (",
    new: "          {false ? (",
    tests: PAGE_TESTS,
  },
  {
    label: "the feed queries today regardless of the date the reader picked",
    decision: "D26",
    path: FEED,
    old: "    data = await api<Feed>(`/api/feed${params.day ? `?day=${params.day}` : \"\"}`);",
    new: "    data = await api<Feed>(`/api/feed`);",
    tests: PAGE_TESTS,
  },
  {
    label: "a dirty working tree is reported as clean, so an irreproducible run looks reproducible",
    decision: "D26",
    path: EVIDENCE,
    old: '              value={card.provenance.code_dirty ? "DIRTY — not reproducible" : "clean"}',
    new: '              value={"clean"}',
    tests: PAGE_TESTS,
  },
  {
    label: "the author's caveats are dropped from the card, leaving only the verdict",
    decision: "D26",
    path: EVIDENCE,
    old: "          {card.caveats.length > 0 && (",
    new: "          {false && (",
    tests: PAGE_TESTS,
  },
  {
    label: "'no study has been run' is suppressed, so an empty warehouse renders as a blank page",
    decision: "D26",
    path: EVIDENCE,
    old: "      {note && (",
    new: "      {false && (",
    tests: PAGE_TESTS,
  },
  // The harness itself. D26 claimed strict stubbing as a design property while
  // nothing exercised it, and checking the claim showed it did not hold -- the
  // page's own catch swallowed the throw. These three keep the fix observable.
  {
    label: "a missing stub is answered with an empty object, the failure D26 claims to prevent",
    decision: "D26",
    path: HARNESS,
    old: "      unexpected.push(path);",
    new: '      return new Response("{}", { headers: { "content-type": "application/json" } });',
    tests: HARNESS_TESTS,
  },
  {
    label: "a missing stub the page catches is reported as nothing at all (the shipped hole)",
    decision: "D26",
    path: HARNESS,
    old: "  if (unexpected.length > 0) throw missing();\n  return [result, requested];",
    new: "  return [result, requested];",
    tests: HARNESS_TESTS,
  },
  {
    label: "a missing stub the page does not catch surfaces as a bare 'fetch failed'",
    decision: "D26",
    path: HARNESS,
    old: "    if (unexpected.length > 0) throw missing();\n    throw caught;",
    new: "    throw caught;",
    tests: HARNESS_TESTS,
  },
];

const require = createRequire(path.join(WEB, "noop.js"));
const VITEST_BIN = path.join(path.dirname(require.resolve("vitest/package.json")), "vitest.mjs");

/**
 * Every file this gate edits, deduplicated -- hashed before and after to prove
 * none was written. The two literals are not mutant targets: `vitest.config.ts`
 * is rewritten by the console.error precondition and `tests/setup.ts` is the file
 * that precondition is about, so leaving them out would have left the "the
 * working tree is never written to" claim uncovered exactly where a new write was
 * just introduced.
 */
const TARGETS = [
  ...new Set([...MUTANTS.map((mutant) => mutant.path), "vitest.config.ts", "tests/setup.ts"]),
].sort();

function digest(relative) {
  return createHash("sha256").update(fs.readFileSync(path.join(WEB, relative))).digest("hex");
}

function fillSandbox(sandbox) {
  for (const entry of SANDBOX_ENTRIES) {
    fs.cpSync(path.join(WEB, entry), path.join(sandbox, entry), { recursive: true });
  }
  // Symlinked, not copied: pnpm's store is hundreds of megabytes of nested
  // symlinks, and copying it would dominate the runtime of the gate while
  // changing nothing about what the mutants prove.
  fs.symlinkSync(path.join(WEB, "node_modules"), path.join(sandbox, "node_modules"), "dir");
}

function runVitest(sandbox, tests, extraEnv = {}) {
  return spawnSync(
    process.execPath,
    [VITEST_BIN, "run", "--root", sandbox, "--reporter", "dot", ...tests],
    { cwd: sandbox, env: { ...process.env, ...extraEnv }, encoding: "utf8" },
  );
}

const PROBE_PATH = "tests/zz_import_redirection_probe.test.ts";
const CANARY = "SANDBOX_CANARY_c7f1";
const PROBE_SOURCE = `/** Written into the sandbox by scripts/web_mutation_gate.mjs; never committed. */
import { expect, it } from "vitest";
import { __canary as apiCanary } from "@/lib/api";
import { __canary as pageCanary } from "@/app/page";

it("imports the sandbox copy rather than the working tree", () => {
  expect([apiCanary, pageCanary]).toEqual(["${CANARY}", "${CANARY}"]);
});
`;

/**
 * Empty when a change made in the sandbox reaches the suite; otherwise what went wrong.
 *
 * The whole gate rests on this. If the alias resolved back to the real tree,
 * vitest would exercise the unmutated code, every mutant would survive, and the
 * output would be a report that the test suite catches nothing -- alarming but
 * wrong, and the true cause would not appear anywhere in it.
 *
 * The check is a canary rather than a resolved path because a path is not the
 * question. `import.meta.resolve` answers from Node's resolver and never sees
 * Vite's alias at all, so it can only report a failure that is not real. What
 * matters is whether an edit to a sandbox file changes what the suite imports,
 * so that is what is measured: an export that exists only in the copy. If
 * resolution went anywhere else the export is missing and the probe fails, which
 * makes this two-sided by construction -- the assertion cannot pass against the
 * working tree, because the working tree does not contain the string.
 */
function unredirectedImports(sandbox) {
  const canaried = [API, PAGE].map((relative) => {
    const file = path.join(sandbox, relative);
    const original = fs.readFileSync(file, "utf8");
    fs.writeFileSync(file, `${original}\nexport const __canary = "${CANARY}";\n`);
    return { file, original };
  });
  fs.writeFileSync(path.join(sandbox, PROBE_PATH), PROBE_SOURCE);
  let result;
  try {
    result = runVitest(sandbox, [PROBE_PATH]);
  } finally {
    for (const { file, original } of canaried) fs.writeFileSync(file, original);
    fs.rmSync(path.join(sandbox, PROBE_PATH), { force: true });
  }
  if (result.status !== 0) {
    const tail = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim().split("\n").slice(-4);
    return [`sandbox edits did not reach the suite: ${tail.join(" | ") || "no output"}`];
  }
  return [];
}

const CONSOLE_PROBE_PATH = "tests/zz_console_error_probe.test.ts";
const SETUP_ANCHOR = '    setupFiles: ["./tests/setup.ts"],';
const CONSOLE_PROBE_SOURCE = `/** Written into the sandbox by scripts/web_mutation_gate.mjs; never committed. */
import { it } from "vitest";

it("writes to console.error the way a React warning does", () => {
  console.error("Warning: gate probe -- this is what an unnoticed React defect looks like");
});
`;

/**
 * Empty when `tests/setup.ts` actually fails a test that writes to `console.error`.
 *
 * D26 claimed this as a property of the suite. It could not be true or false of
 * any test in it: no test writes to `console.error`, so the trap had never fired,
 * and a suite green under an armed trap is indistinguishable from one green under
 * a trap that does nothing. It also cannot be checked from inside the suite it
 * guards -- a test that triggered the trap would fail itself, which is the whole
 * design.
 *
 * So it is checked from outside, two-sided like the canary above: the same probe
 * runs twice, once with the setup file loaded and once with the `setupFiles` line
 * removed from the sandbox config. FAIL then PASS is the only acceptable result.
 * PASS in the first arm means the trap is disarmed; FAIL in the second means the
 * probe was failing for some other reason and proves nothing about the trap.
 */
function consoleErrorTrapUnarmed(sandbox) {
  const config = path.join(sandbox, "vitest.config.ts");
  const original = fs.readFileSync(config, "utf8");
  fs.writeFileSync(path.join(sandbox, CONSOLE_PROBE_PATH), CONSOLE_PROBE_SOURCE);
  try {
    if (!original.includes(SETUP_ANCHOR)) {
      return ["vitest.config.ts no longer declares setupFiles at the expected line"];
    }
    const armed = runVitest(sandbox, [CONSOLE_PROBE_PATH]);
    fs.writeFileSync(config, original.replace(SETUP_ANCHOR, ""));
    const unarmed = runVitest(sandbox, [CONSOLE_PROBE_PATH]);
    const problems = [];
    if (armed.status === 0) {
      problems.push("a test writing to console.error PASSED with tests/setup.ts loaded");
    }
    if (unarmed.status !== 0) {
      problems.push("the same probe FAILED with setup removed, so its failure was not the trap");
    }
    return problems;
  } finally {
    fs.writeFileSync(config, original);
    fs.rmSync(path.join(sandbox, CONSOLE_PROBE_PATH), { force: true });
  }
}

function main() {
  if (!fs.existsSync(VITEST_BIN)) {
    console.log(`FAIL  vitest not installed at ${VITEST_BIN}`);
    console.log("      Run `pnpm install` in apps/web first.");
    return 1;
  }

  // Taken from the real tree before anything happens and compared again at the
  // end. Under the sandbox design this is expected to be trivially true, which is
  // the point: it is the only thing that would notice a future edit
  // reintroducing an in-place write.
  const before = Object.fromEntries(TARGETS.map((relative) => [relative, digest(relative)]));

  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), "aletheia-web-mutants-"));
  console.log(`sandbox -> ${sandbox}   (the working tree is not written to)`);

  const survivors = [];
  try {
    fillSandbox(sandbox);

    const stray = unredirectedImports(sandbox);
    if (stray.length > 0) {
      console.log("FAIL  imports do not resolve to the sandbox, so mutants would test nothing:");
      for (const line of stray) console.log(`          ${line}`);
      return 1;
    }

    const unarmed = consoleErrorTrapUnarmed(sandbox);
    if (unarmed.length > 0) {
      console.log("FAIL  console.error is not fatal, so React's own warnings would go unnoticed:");
      for (const line of unarmed) console.log(`          ${line}`);
      return 1;
    }
    console.log();

    for (const mutant of MUTANTS) {
      const target = path.join(sandbox, mutant.path);
      const original = fs.readFileSync(target, "utf8");
      if (!original.includes(mutant.old)) {
        console.log(`FAIL  ${mutant.label}\n        anchor no longer present in ${mutant.path}`);
        console.log("        The code moved. Update the mutant, or it is silently testing nothing.");
        survivors.push(mutant);
        continue;
      }

      fs.writeFileSync(target, original.replace(mutant.old, mutant.new));
      let caught;
      try {
        caught = runVitest(sandbox, mutant.tests).status !== 0;
      } finally {
        fs.writeFileSync(target, original);
      }
      // Not ceremony: if the suite does not go green again the environment is
      // broken and the "caught" result above proves nothing.
      const healed = runVitest(sandbox, mutant.tests).status === 0;

      const ok = caught && healed;
      if (!ok) survivors.push(mutant);
      console.log(
        `${ok ? "PASS" : "FAIL"}  [${mutant.decision}] ${mutant.label}\n` +
          `        mutated -> ${caught ? "FAILED (caught)" : "PASSED (SURVIVED)"}` +
          `   restored -> ${healed ? "PASSED" : "FAILED (harness broken)"}`,
      );
    }
  } finally {
    fs.rmSync(sandbox, { recursive: true, force: true });
  }

  console.log();
  const touched = TARGETS.filter((relative) => digest(relative) !== before[relative]);
  if (touched.length > 0) {
    console.log("FAIL  the working tree was modified, which this harness must never do:");
    for (const relative of touched) console.log(`          ${relative}`);
    console.log("      Restore from git; the sandbox has been deleted.");
    return 1;
  }

  if (survivors.length > 0) {
    console.log(`${survivors.length} of ${MUTANTS.length} mutant(s) survived:`);
    for (const mutant of survivors) console.log(`    [${mutant.decision}] ${mutant.label}`);
    return 1;
  }
  console.log(`all ${MUTANTS.length} mutants caught`);
  return 0;
}

process.exit(main());
