import { api, ApiError, edgarUrl, pct, pctChange, type Revision } from "@/lib/api";
import { ErrorPanel } from "../error-panel";

/** Every value this filer changed after publishing it. */

export const dynamic = "force-dynamic";

type Payload = {
  ticker: string;
  company: string;
  n_revisions: number;
  revisions: Revision[];
};

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ ticker?: string; min_change?: string }>;
}) {
  const params = await searchParams;
  const ticker = params.ticker ?? "AAPL";
  const minChange = params.min_change ?? "0.05";

  let data: Payload | null = null;
  let error: string | null = null;
  try {
    data = await api<Payload>(
      `/api/revisions/${encodeURIComponent(ticker)}?min_change=${minChange}`,
    );
  } catch (caught) {
    error = caught instanceof ApiError ? caught.detail : String(caught);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Revision explorer</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-muted)]">
          A restatement is the same fiscal period reported twice with different
          numbers. Both rows are kept — deduplicating them is exactly how a vendor
          panel loses the history. The lag column matters: a value revised a year
          later on a routine annual report is usually a re-presentation of a prior-
          year comparative, while one revised on a <code>10-K/A</code> weeks later
          is a genuine restatement.
        </p>
      </div>

      <form className="flex flex-wrap items-end gap-3 rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-4">
        <label className="block text-xs text-[var(--color-muted)]">
          Ticker
          <input
            name="ticker"
            defaultValue={ticker}
            className="mt-1 block w-28 rounded-md border border-[var(--color-edge)] bg-[var(--color-ink)] px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          />
        </label>
        <label className="block text-xs text-[var(--color-muted)]">
          Minimum relative change
          <input
            name="min_change"
            defaultValue={minChange}
            className="mt-1 block w-40 rounded-md border border-[var(--color-edge)] bg-[var(--color-ink)] px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-white px-4 py-2 text-sm font-medium text-black hover:opacity-85"
        >
          Show
        </button>
      </form>

      {error && <ErrorPanel title="Could not load revisions" detail={error} />}

      {data && (
        <>
          <p className="text-sm text-[var(--color-muted)]">
            <strong className="text-white">{data.company}</strong> —{" "}
            <strong className="text-white tabular">{data.n_revisions}</strong> value
            {data.n_revisions === 1 ? "" : "s"} changed after publication at a
            threshold of {pct(Number(minChange))}.
          </p>
          {data.revisions.length === 0 ? (
            <p className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-5 text-sm text-[var(--color-muted)]">
              Nothing at this threshold. That is an answer, not an error — lower it
              to see smaller revisions.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-[var(--color-edge)]">
              <table className="w-full text-sm">
                <thead className="bg-[var(--color-panel)] text-left text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  <tr>
                    <Th>Concept</Th>
                    <Th>Period</Th>
                    <Th align="right">First</Th>
                    <Th align="right">Revised to</Th>
                    <Th align="right">Change</Th>
                    <Th align="right">Lag</Th>
                    <Th>Revised by</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.revisions.map((row) => (
                    <tr
                      key={`${row.concept}-${row.period_end}-${row.new_accn}`}
                      className="border-t border-[var(--color-edge)]"
                    >
                      <Td className="font-mono text-xs">{row.concept}</Td>
                      <Td className="tabular">{row.period_end}</Td>
                      <Td align="right" className="tabular">
                        {row.prior_value}
                      </Td>
                      <Td align="right" className="tabular text-[var(--color-restated)]">
                        {row.new_value}
                      </Td>
                      <Td align="right" className="tabular">
                        {/* `pctChange`, not `pct`: every row here has
                            `value <> prior_value` by construction, so a cell
                            reading "+0.00%" could only ever mean "rounds to
                            zero" -- and it read that way on a genuine $3m
                            revision to Apple's $105.1bn long-term debt. */}
                        {row.relative_change === null ? "—" : pctChange(row.relative_change)}
                      </Td>
                      <Td align="right" className="tabular">
                        {row.days_to_revision}d
                      </Td>
                      <Td>
                        <a
                          href={edgarUrl(0, row.new_accn)}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-xs underline decoration-dotted underline-offset-2"
                        >
                          {row.new_form}
                        </a>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return <th className={`px-3 py-2 text-${align} font-medium`}>{children}</th>;
}

function Td({
  children,
  align = "left",
  className = "",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return <td className={`px-3 py-2 text-${align} ${className}`}>{children}</td>;
}
