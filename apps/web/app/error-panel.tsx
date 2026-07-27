/**
 * A failure the reader can act on.
 *
 * The alternative -- an empty table -- reads as "there is nothing here", which is
 * a different and much worse claim than "this could not be loaded".
 */
export function ErrorPanel({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-restated)]/40 bg-[var(--color-restated)]/5 p-5">
      <p className="font-medium text-[var(--color-restated)]">{title}</p>
      <p className="mt-1 text-sm text-[var(--color-muted)]">{detail}</p>
    </div>
  );
}
