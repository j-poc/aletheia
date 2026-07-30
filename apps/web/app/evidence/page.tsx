import { api, ApiError, pct } from "@/lib/api";
import { ErrorPanel } from "../error-panel";

/**
 * Evidence cards.
 *
 * A performance figure alone is not a result. Each card carries the commit, the
 * data vintage, the trial count and the caveats, because those are what decide
 * whether the number means anything.
 */

export const dynamic = "force-dynamic";

type Arm = {
  label: string;
  n_periods: number;
  gross_annualised: number;
  net_annualised: number;
  net_arithmetic_annualised: number;
  net_stdev_annualised: number;
  annualised_sharpe: number;
  mean_turnover: number;
  n_excluded: number;
};

type Comparison = {
  name: string;
  metric: string;
  baseline_value: number;
  variant_value: number;
  difference: number;
  interpretation: string;
};

type Card = {
  study_id: string;
  hypothesis: string;
  verdict: string;
  trial_count: number;
  trial_family: string;
  repro_hash: string;
  generated_at: string;
  provenance: {
    code_commit: string;
    code_dirty: boolean;
    data_vintage: string;
    universe_source: string;
  };
  arms: Arm[];
  comparisons: Comparison[];
  caveats: string[];
};

export default async function Page() {
  let cards: Card[] = [];
  let note: string | null = null;
  let error: string | null = null;
  try {
    const payload = await api<{ cards: Card[]; note?: string }>("/api/evidence");
    cards = payload.cards;
    note = payload.note ?? null;
  } catch (caught) {
    error = caught instanceof ApiError ? caught.detail : String(caught);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Evidence cards</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-muted)]">
          No number leaves this system without the commit it was produced by, the
          data vintage it saw, the number of hypotheses tried in its family, its
          trading costs, and the author&apos;s caveats. The reproducibility hash
          covers everything except the timestamp, so two runs over the same
          warehouse must produce the same value.
        </p>
      </div>

      {error && <ErrorPanel title="Could not load evidence" detail={error} />}
      {note && (
        <p className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-5 text-sm text-[var(--color-muted)]">
          {note}. Run <code className="font-mono">make study</code> to produce one.
        </p>
      )}

      {cards.map((card) => (
        <article
          key={card.study_id}
          className="space-y-5 rounded-lg border border-[var(--color-edge)] bg-[var(--color-panel)] p-6"
        >
          <header>
            <h2 className="font-mono text-lg font-semibold">{card.study_id}</h2>
            <p className="mt-2 text-sm text-[var(--color-muted)]">{card.hypothesis}</p>
          </header>

          <p className="rounded-md border-l-2 border-white/40 bg-white/5 p-4 text-sm leading-relaxed">
            {card.verdict}
          </p>

          {/*
            Absent rendered as absent. A study with no return arms -- S002 is
            fundamentals-only by design, since the survivorship-free price panel
            is an unbought entitlement (D1) -- was rendering the full header row
            over an empty body: eight column titles, Sharpe among them, and
            nothing beneath. A reader sees a performance table that failed to
            load, or a strategy that earned nothing, and neither is what
            happened. The empty state has to say which.
          */}
          {card.arms.length === 0 ? (
            <p className="rounded-md border border-dashed border-[var(--color-edge)] p-4 text-sm text-[var(--color-muted)]">
              No return arms. This study is measured on fundamentals alone, so it
              reports no Sharpe, no turnover and no cost-adjusted return — those
              are absent, not zero, and not pending. A return-predictive study
              needs a survivorship-free price panel, which this build does not
              have.
            </p>
          ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="py-2 pr-3 font-medium">Arm</th>
                  <th className="py-2 pr-3 text-right font-medium">Periods</th>
                  <th className="py-2 pr-3 text-right font-medium">Gross p.a.</th>
                  <th className="py-2 pr-3 text-right font-medium">Net p.a.</th>
                  <th className="py-2 pr-3 text-right font-medium">Vol p.a.</th>
                  <th className="py-2 pr-3 text-right font-medium">Sharpe</th>
                  <th className="py-2 pr-3 text-right font-medium">Turnover</th>
                  <th className="py-2 text-right font-medium">Excluded</th>
                </tr>
              </thead>
              <tbody>
                {card.arms.map((arm) => (
                  <tr key={arm.label} className="border-t border-[var(--color-edge)]">
                    <td className="py-2 pr-3 font-mono text-xs">{arm.label}</td>
                    <td className="py-2 pr-3 text-right tabular">{arm.n_periods}</td>
                    <td className="py-2 pr-3 text-right tabular">
                      {pct(arm.gross_annualised)}
                    </td>
                    <td className="py-2 pr-3 text-right tabular">
                      {pct(arm.net_annualised)}
                    </td>
                    <td className="py-2 pr-3 text-right tabular">
                      {(arm.net_stdev_annualised * 100).toFixed(2)}%
                    </td>
                    <td className="py-2 pr-3 text-right tabular">
                      {arm.annualised_sharpe.toFixed(2)}
                    </td>
                    <td className="py-2 pr-3 text-right tabular">
                      {arm.mean_turnover.toFixed(2)}x
                    </td>
                    <td className="py-2 text-right tabular">
                      {arm.n_excluded.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-[var(--color-muted)]">
              Returns are geometric; volatility and Sharpe are on a matched
              arithmetic basis, since the Sharpe&apos;s numerator is an arithmetic
              mean.
            </p>
          </div>
          )}

          {card.comparisons.length > 0 && (
            <section>
              <h3 className="mb-2 text-xs uppercase tracking-wide text-[var(--color-muted)]">
                What the differences mean
              </h3>
              <ul className="space-y-3">
                {card.comparisons.map((comparison) => (
                  <li key={comparison.name} className="text-sm">
                    <span className="font-medium">{comparison.name}:</span>{" "}
                    <span className="tabular">{pct(comparison.baseline_value)}</span> →{" "}
                    <span className="tabular">{pct(comparison.variant_value)}</span>{" "}
                    <span className="tabular font-semibold text-[var(--color-restated)]">
                      ({pct(comparison.difference)})
                    </span>
                    <p className="mt-1 text-xs text-[var(--color-muted)]">
                      {comparison.interpretation}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="grid gap-x-8 gap-y-1 border-t border-[var(--color-edge)] pt-4 text-xs sm:grid-cols-2">
            <Row label="Commit" value={card.provenance.code_commit.slice(0, 12)} />
            <Row label="Data vintage" value={card.provenance.data_vintage} />
            <Row label="Universe" value={card.provenance.universe_source} />
            <Row
              label={`Trials in "${card.trial_family}"`}
              value={String(card.trial_count)}
            />
            <Row label="Repro hash" value={card.repro_hash.slice(0, 16)} />
            <Row
              label="Working tree"
              value={card.provenance.code_dirty ? "DIRTY — not reproducible" : "clean"}
            />
          </section>

          {card.caveats.length > 0 && (
            <section className="border-t border-[var(--color-edge)] pt-4">
              <h3 className="mb-2 text-xs uppercase tracking-wide text-[var(--color-muted)]">
                Caveats
              </h3>
              <ul className="space-y-2 text-xs leading-relaxed text-[var(--color-muted)]">
                {card.caveats.map((caveat) => (
                  <li key={caveat.slice(0, 40)}>— {caveat}</li>
                ))}
              </ul>
            </section>
          )}
        </article>
      ))}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-[var(--color-muted)]">{label}</span>
      <span className="font-mono tabular">{value}</span>
    </div>
  );
}
