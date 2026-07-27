import { api, ApiError, edgarUrl, pct, type AsOf } from "@/lib/api";
import { ErrorPanel } from "./error-panel";

/**
 * The as-of viewer.
 *
 * Everything else in this application is supporting material. This page asks one
 * question at one date and shows, beside the answer, the number a conventional
 * fundamentals panel would have handed you instead -- along with the accession
 * number proving the second one did not exist yet.
 */

export const dynamic = "force-dynamic";

type Params = {
  searchParams: Promise<{
    ticker?: string;
    date?: string;
    concept?: string;
    period_end?: string;
    period_start?: string;
  }>;
};

const DEFAULTS = {
  ticker: "AAPL",
  date: "2009-12-01",
  concept: "EarningsPerShareDiluted",
  period_end: "2008-09-27",
  // An end date does not identify a period -- a fiscal year and its fourth
  // quarter share one. Blank asks the API to decide, which it refuses to do
  // when the date is genuinely shared, returning a 400 that names the
  // candidates. This field is how the caller answers it.
  period_start: "",
};

export default async function Page({ searchParams }: Params) {
  const params = { ...DEFAULTS, ...(await searchParams) };
  const query = new URLSearchParams({
    knowledge_date: params.date,
    concept: params.concept,
    ...(params.period_end ? { period_end: params.period_end } : {}),
    ...(params.period_start ? { period_start: params.period_start } : {}),
  });

  let data: AsOf | null = null;
  let error: string | null = null;
  try {
    data = await api<AsOf>(`/api/asof/${encodeURIComponent(params.ticker)}?${query}`);
  } catch (caught) {
    error = caught instanceof ApiError ? caught.detail : String(caught);
  }

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">
          What was knowable on a given date
        </h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-muted)]">
          Pick a company, a reported figure and a date. The left column is what had
          actually been filed by then. The right column is what a fundamentals
          vendor returns today for that same period — the number a backtest would
          silently use. When they differ, every result built on the right column is
          trading on information that did not exist.
        </p>
      </section>

      <form className="flex flex-wrap items-end gap-3 rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-4">
        <Field label="Ticker" name="ticker" defaultValue={params.ticker} width="w-28" />
        <Field label="Concept" name="concept" defaultValue={params.concept} width="w-72" />
        <Field
          label="Period end"
          name="period_end"
          defaultValue={params.period_end}
          type="date"
          width="w-44"
        />
        <Field
          label="Period start"
          name="period_start"
          defaultValue={params.period_start}
          placeholder="blank, a date, or instant"
          width="w-52"
        />
        <Field
          label="Knowledge date"
          name="date"
          defaultValue={params.date}
          type="date"
          width="w-44"
        />
        <button
          type="submit"
          className="rounded-md bg-white px-4 py-2 text-sm font-medium text-black transition-opacity hover:opacity-85"
        >
          Ask
        </button>
      </form>

      {error && <ErrorPanel title="Could not answer that" detail={error} />}

      {data && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <ValueCard
              heading={`As known on ${data.knowledge_date}`}
              caption={
                data.already_restated_by_then
                  ? "The restatement had already been published by this date, so this is it."
                  : "The most recent figure filed by this date. What a practitioner actually had."
              }
              fact={data.as_known}
              cik={data.cik}
              tone={data.already_restated_by_then ? "restated" : "known"}
            />
            <ValueCard
              heading="As it stands today"
              caption="What a vendor panel returns for this period. Lookahead if used before it was filed."
              fact={data.as_it_stands_today}
              cik={data.cik}
              tone={data.is_restated ? "restated" : "known"}
            />
          </div>

          <section className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-5">
            {data.is_restated ? (
              <p className="text-sm leading-relaxed">
                <span className="font-medium text-[var(--color-restated)]">
                  This period was restated.
                </span>{" "}
                {data.company} first reported{" "}
                <strong className="tabular">{data.as_first_reported.value}</strong> on{" "}
                {data.as_first_reported.knowledge_date}, and it now stands at{" "}
                <strong className="tabular">{data.as_it_stands_today.value}</strong> as of{" "}
                {data.as_it_stands_today.knowledge_date}
                {data.relative_drift !== null && (
                  <>
                    {" "}
                    — a change of{" "}
                    <strong className="tabular">{pct(data.relative_drift)}</strong>
                  </>
                )}
                .{" "}
                {data.already_restated_by_then
                  ? "By the knowledge date above, the restatement was already public — so the left column shows it too. Move the date earlier to see what came before."
                  : `A simulation dated ${data.knowledge_date} that used the restated figure would be reading ${monthsBetween(
                      data.as_first_reported.knowledge_date,
                      data.as_it_stands_today.knowledge_date,
                    )} months into the future.`}
              </p>
            ) : (
              <p className="text-sm leading-relaxed text-[var(--color-muted)]">
                This period was never restated, so both columns agree. That is the
                common case — and the reason the difference is easy to miss until it
                matters.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}

/**
 * Both dates, because the end date alone does not say which period this is.
 * A fiscal year and its fourth quarter end on the same day and report different
 * numbers, so a card labelled only "period ending 2015-09-26" is ambiguous about
 * the very thing the number means.
 */
function periodLabel(fact: AsOf["as_known"]): string {
  if (!fact.period_start) return `instant, ${fact.period_end}`;
  const days = Math.round(
    (Date.parse(fact.period_end) - Date.parse(fact.period_start)) / 86_400_000,
  );
  return `${fact.period_start} to ${fact.period_end} (${days}d)`;
}

function monthsBetween(from: string, to: string): number {
  const days = (Date.parse(to) - Date.parse(from)) / 86_400_000;
  return Math.max(1, Math.round(days / 30.44));
}

function Field({
  label,
  name,
  defaultValue,
  type = "text",
  placeholder,
  width,
}: {
  label: string;
  name: string;
  defaultValue: string;
  type?: string;
  placeholder?: string;
  width: string;
}) {
  return (
    <label className="block text-xs text-[var(--color-muted)]">
      {label}
      <input
        name={name}
        type={type}
        defaultValue={defaultValue}
        placeholder={placeholder}
        className={`mt-1 block ${width} rounded-md border border-[var(--color-edge)] bg-[var(--color-ink)] px-3 py-2 text-sm text-white outline-none focus:border-white/40`}
      />
    </label>
  );
}

function ValueCard({
  heading,
  caption,
  fact,
  cik,
  tone,
}: {
  heading: string;
  caption: string;
  fact: AsOf["as_known"];
  cik: number;
  tone: "known" | "restated";
}) {
  const accent =
    tone === "restated" ? "var(--color-restated)" : "var(--color-known)";
  return (
    <div
      className="rounded-lg border bg-[var(--color-panel)] p-5"
      style={{ borderColor: `color-mix(in srgb, ${accent} 35%, transparent)` }}
    >
      <p className="text-xs uppercase tracking-wide" style={{ color: accent }}>
        {heading}
      </p>
      <p className="mt-3 text-4xl font-semibold tabular">{fact.value}</p>
      <p className="mt-1 text-xs text-[var(--color-muted)]">{fact.unit}</p>
      <p className="mt-3 text-xs text-[var(--color-muted)]">{caption}</p>
      <dl className="mt-4 space-y-1 border-t border-[var(--color-edge)] pt-3 text-xs">
        <Row label="Period" value={periodLabel(fact)} />
        <Row label="Became public" value={fact.knowledge_date} />
        <Row label="Form" value={fact.form} />
        <Row
          label="Report"
          value={fact.report_seq === 1 ? "first publication" : `restatement #${fact.report_seq - 1}`}
        />
        <div className="flex justify-between gap-4">
          <dt className="text-[var(--color-muted)]">Accession</dt>
          <dd>
            <a
              href={edgarUrl(cik, fact.accn)}
              target="_blank"
              rel="noreferrer"
              className="font-mono underline decoration-dotted underline-offset-2"
            >
              {fact.accn}
            </a>
          </dd>
        </div>
      </dl>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-[var(--color-muted)]">{label}</dt>
      <dd className="tabular">{value}</dd>
    </div>
  );
}
