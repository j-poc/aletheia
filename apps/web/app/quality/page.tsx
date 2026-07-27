import { api, ApiError, type Quality } from "@/lib/api";
import { ErrorPanel } from "../error-panel";

/** What is actually in the warehouse, and where it came from. */

export const dynamic = "force-dynamic";

export default async function Page() {
  let data: Quality | null = null;
  let error: string | null = null;
  try {
    data = await api<Quality>("/api/quality");
  } catch (caught) {
    error = caught instanceof ApiError ? caught.detail : String(caught);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Data quality and lineage</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-muted)]">
          Coverage is a claim like any other and belongs on the page. The data
          vintage is the newest filing date in the warehouse: re-running a study
          after it moves is a different study, because restatements filed in between
          did not exist when the first one ran.
        </p>
      </div>

      {error && <ErrorPanel title="Could not load coverage" detail={error} />}

      {data && (
        <>
          <div className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-5">
            <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Data vintage
            </p>
            <p className="mt-1 text-3xl font-semibold tabular">{data.data_vintage}</p>
          </div>

          <section>
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-[var(--color-muted)]">
              Rows held
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(data.row_counts).map(([table, count]) => (
                <div
                  key={table}
                  className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] px-4 py-3"
                >
                  <p className="font-mono text-xs text-[var(--color-muted)]">{table}</p>
                  <p className="mt-1 text-xl font-semibold tabular">
                    {count.toLocaleString()}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-5">
            <h2 className="text-sm font-medium uppercase tracking-wide text-[var(--color-muted)]">
              Revision coverage
            </h2>
            <p className="mt-3 text-sm leading-relaxed">
              <strong className="tabular">
                {data.revision_coverage.periods_with_a_changed_value.toLocaleString()}
              </strong>{" "}
              of{" "}
              <strong className="tabular">
                {data.revision_coverage.distinct_periods.toLocaleString()}
              </strong>{" "}
              distinct (company, concept, period) combinations carry a value that
              changed after it was first published
              {data.revision_coverage.distinct_periods > 0 && (
                <>
                  {" "}
                  —{" "}
                  <strong className="tabular">
                    {(
                      (100 * data.revision_coverage.periods_with_a_changed_value) /
                      data.revision_coverage.distinct_periods
                    ).toFixed(1)}
                    %
                  </strong>
                </>
              )}
              . A flat vendor panel shows only the final value for every one of them.
            </p>
          </section>

          <section>
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-[var(--color-muted)]">
              Ingest history
            </h2>
            <div className="overflow-x-auto rounded-lg border border-[var(--color-edge)]">
              <table className="w-full text-sm">
                <thead className="bg-[var(--color-panel)] text-left text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  <tr>
                    <th className="px-3 py-2 font-medium">Source</th>
                    <th className="px-3 py-2 text-right font-medium">Runs</th>
                    <th className="px-3 py-2 font-medium">Last started</th>
                  </tr>
                </thead>
                <tbody>
                  {data.ingest_runs.map((run) => (
                    <tr key={run.source} className="border-t border-[var(--color-edge)]">
                      <td className="px-3 py-2 font-mono text-xs">{run.source}</td>
                      <td className="px-3 py-2 text-right tabular">{run.runs}</td>
                      <td className="px-3 py-2 tabular text-xs text-[var(--color-muted)]">
                        {run.last_started}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
