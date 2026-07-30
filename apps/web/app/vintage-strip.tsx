import { api, ApiError, type Freshness, type Quality } from "@/lib/api";

/**
 * The freshness of the whole surface, on every page.
 *
 * Not decoration, and not on the data-quality page alone. Every page here answers
 * "as it stands today", and *today* is the newest filing in the warehouse rather
 * than the date on the reader's wall. A warehouse three months behind keeps
 * answering, in the same colours, with the same confidence -- nothing throws,
 * nothing exits non-zero, and the surface has quietly turned "I do not know" into
 * "I checked". The page where that misleads most is the as-of viewer, which is
 * the one page a reader arrives on and the one that says "today" in a heading.
 *
 * Four states, and they must be distinguishable **without reading a number**:
 * shape and word first, colour second, the date last. A viewer who has to
 * subtract the vintage from today's date in their head has not been told
 * anything -- that is the arithmetic the server already did.
 */

/**
 * Three independent signals per state: a word, a shape, and a colour.
 *
 * `partial` and `stale` share a colour on purpose -- they are the same severity
 * of "do not rely on this yet" -- so the shape is what separates them: a square
 * for an incomplete warehouse, a circle for an out-of-date one. Colour alone
 * would leave a reader with colour-vision deficiency, or a monochrome screen
 * share, unable to tell any of these apart, and this strip exists precisely for
 * the reader who is not looking carefully.
 */
const STATES: Record<
  Freshness["state"],
  { label: string; shape: string; dot: string; text: string; edge: string; wash: string }
> = {
  fresh: {
    label: "Current",
    shape: "rounded-full",
    dot: "bg-[var(--color-ok)]",
    text: "text-[var(--color-ok)]",
    edge: "border-[var(--color-edge)]",
    wash: "bg-[var(--color-panel)]",
  },
  partial: {
    label: "Incomplete",
    shape: "rounded-none",
    dot: "bg-[var(--color-warn)]",
    text: "text-[var(--color-warn)]",
    edge: "border-[var(--color-warn)]",
    wash: "bg-[var(--color-panel)]",
  },
  stale: {
    label: "Out of date",
    shape: "rounded-full",
    dot: "bg-[var(--color-warn)]",
    text: "text-[var(--color-warn)]",
    edge: "border-[var(--color-warn)]",
    wash: "bg-[var(--color-panel)]",
  },
  broken: {
    label: "Not trustworthy",
    shape: "rounded-full",
    dot: "bg-[var(--color-bad)]",
    text: "text-[var(--color-bad)]",
    edge: "border-[var(--color-bad)]",
    wash: "bg-[var(--color-bad-wash)]",
  },
};

export async function VintageStrip() {
  let freshness: Freshness | null = null;
  let unreachable: string | null = null;
  try {
    freshness = (await api<Quality>("/api/quality")).freshness;
  } catch (caught) {
    unreachable = caught instanceof ApiError ? caught.detail : String(caught);
  }

  // The API not answering is itself one of the four states, and the loudest one.
  // Rendering nothing here -- the tempting `return null` -- would leave the pages
  // below looking exactly as they do when everything is fine.
  const state: Freshness["state"] = freshness?.state ?? "broken";
  const tone = STATES[state];
  const reason =
    freshness?.reason ??
    `${unreachable ?? "the API did not answer"}. Nothing below is coming from the warehouse.`;

  // Deliberately absent when everything is current. A banner that is always there
  // is furniture, and furniture is not read -- which would defeat the point of the
  // three states that matter. The date still appears in full on the data-quality
  // page, where a reader goes to look it up on purpose.
  if (state === "fresh") return null;

  return (
    <div role="status" className={`border-b ${tone.edge} ${tone.wash} px-6 py-2 text-sm`}>
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`inline-block h-2 w-2 shrink-0 ${tone.shape} ${tone.dot}`} />
        <strong className={tone.text}>{tone.label}</strong>
        <span className="text-[var(--color-muted)]">{reason}</span>
      </div>
    </div>
  );
}
