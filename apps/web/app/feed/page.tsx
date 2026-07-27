import { api, ApiError, edgarUrl, type Feed } from "@/lib/api";
import { ErrorPanel } from "../error-panel";

/** Filings ranked by how much they deserve a human's attention. */

export const dynamic = "force-dynamic";

const TONE: Record<string, string> = {
  CONFESSED: "var(--color-restated)",
  STRONG: "#facc15",
  MODERATE: "#60a5fa",
  WEAK: "var(--color-muted)",
};

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ day?: string }>;
}) {
  const params = await searchParams;

  let data: Feed | null = null;
  let error: string | null = null;
  try {
    data = await api<Feed>(`/api/feed${params.day ? `?day=${params.day}` : ""}`);
  } catch (caught) {
    error = caught instanceof ApiError ? caught.detail : String(caught);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Filing feed</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-muted)]">
          Ranked by an ordinal concern score, <strong>not</strong> a probability.
          Calibrating a probability needs labelled outcomes — filings followed by a
          known restatement or enforcement action — and no such set exists here.
          Every flag is a disclosure the company was required to make, so each row
          says why it surfaced rather than asking you to trust a number.
        </p>
      </div>

      <form className="flex items-end gap-3 rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-4">
        <label className="block text-xs text-[var(--color-muted)]">
          Date
          <input
            name="day"
            type="date"
            defaultValue={params.day ?? data?.date ?? ""}
            className="mt-1 block w-44 rounded-md border border-[var(--color-edge)] bg-[var(--color-ink)] px-3 py-2 text-sm text-white outline-none focus:border-white/40"
          />
        </label>
        <button
          type="submit"
          className="rounded-md bg-white px-4 py-2 text-sm font-medium text-black hover:opacity-85"
        >
          Show
        </button>
      </form>

      {error && <ErrorPanel title="Could not load the feed" detail={error} />}

      {data && (
        <>
          <p className="text-sm text-[var(--color-muted)]">
            <strong className="text-white tabular">{data.n_filings}</strong> filing
            {data.n_filings === 1 ? "" : "s"} became public on {data.date};{" "}
            <strong className="text-white tabular">{data.n_flagged}</strong> flagged.
          </p>

          {data.items.length === 0 ? (
            <p className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-5 text-sm text-[var(--color-muted)]">
              Nothing flagged on this date. Weekends, holidays and ordinary days all
              look like this — an empty feed is an answer, not a failure.
            </p>
          ) : (
            <ul className="space-y-3">
              {data.items.map((item) => (
                <li
                  key={item.accn}
                  className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-4"
                >
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span
                      className="rounded px-2 py-0.5 text-xs font-medium"
                      style={{
                        color: TONE[item.confidence] ?? "var(--color-muted)",
                        background: `color-mix(in srgb, ${TONE[item.confidence] ?? "var(--color-muted)"} 12%, transparent)`,
                      }}
                    >
                      {item.confidence}
                    </span>
                    <span className="font-medium">{item.form}</span>
                    <span className="text-xs text-[var(--color-muted)]">
                      CIK {item.cik} · {item.knowledge_date}
                    </span>
                    <span className="ml-auto text-sm tabular text-[var(--color-muted)]">
                      score {item.score.toFixed(1)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs italic text-[var(--color-muted)]">
                    {item.confidence_meaning}
                  </p>
                  <ul className="mt-3 space-y-1 text-sm">
                    {item.findings.map((finding) => (
                      <li key={finding.flag} className="flex gap-2">
                        <span className="text-[var(--color-muted)]">→</span>
                        <span>{finding.evidence}</span>
                      </li>
                    ))}
                  </ul>
                  <a
                    href={edgarUrl(item.cik, item.accn)}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-3 inline-block font-mono text-xs underline decoration-dotted underline-offset-2"
                  >
                    {item.accn}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
