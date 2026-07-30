import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

/**
 * Rendering a server component the way the server does.
 *
 * A Next.js page in this application is `async function Page(props)` returning a
 * tree of ordinary synchronous components. Awaiting it and handing the result to
 * `renderToStaticMarkup` produces the bytes the server would have sent. Nothing
 * here emulates a browser, and nothing here should be read as covering one.
 *
 * The fetch stub is deliberately strict rather than convenient. A test that
 * silently returns `{}` for a path the page did not ask for would pass while the
 * page called the wrong endpoint -- which is a defect this application has
 * already shipped once at the API layer, where a query grouped by the wrong key
 * and the page reported the answer without complaint (D25).
 */

export type Reply =
  | { status: number; body: unknown }
  /** The backend is not answering at all -- `fetch` rejects, it does not 500. */
  | { unreachable: true };

export type Backend = Record<string, Reply>;

export const BASE = "http://api.test";

/** Every URL the page requested, in order, with `BASE` stripped. */
export type Rendered = { html: string; text: string; requested: string[] };

class UnexpectedRequest extends Error {}

async function withBackend<T>(backend: Backend, body: () => Promise<T>): Promise<[T, string[]]> {
  const requested: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!url.startsWith(BASE)) throw new UnexpectedRequest(`request outside the API base: ${url}`);
    const path = url.slice(BASE.length);
    requested.push(path);
    const reply = match(backend, path);
    if (reply === undefined) {
      // Thrown, not returned as a 404: a missing stub is a mistake in the test,
      // and dressing it up as a backend response would let the page render its
      // error panel and the assertion pass for the wrong reason.
      throw new UnexpectedRequest(
        `no stub for ${path}\n  stubs: ${Object.keys(backend).join(", ") || "(none)"}`,
      );
    }
    if ("unreachable" in reply) throw new TypeError("fetch failed");
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof globalThis.fetch;
  try {
    return [await body(), requested];
  } finally {
    globalThis.fetch = realFetch;
  }
}

/**
 * Longest matching prefix, so a test states the endpoint it cares about and does
 * not have to reproduce the page's exact query string -- while a page that calls
 * a genuinely different endpoint still finds nothing and fails loudly.
 */
function match(backend: Backend, path: string): Reply | undefined {
  const keys = Object.keys(backend)
    .filter((key) => path === key || path.startsWith(key))
    .sort((a, b) => b.length - a.length);
  return keys.length > 0 ? backend[keys[0]] : undefined;
}

export async function render<P>(
  Page: (props: P) => Promise<ReactElement>,
  props: P,
  backend: Backend,
): Promise<Rendered> {
  const [element, requested] = await withBackend(backend, () => Page(props));
  const html = renderToStaticMarkup(element);
  return { html, text: toText(html), requested };
}

/**
 * The visible prose, with markup and entities resolved.
 *
 * Assertions are written against this rather than the raw HTML because the claims
 * under test are sentences a reader sees. Asserting on markup would pass a test
 * whose sentence is split across two `<strong>` elements and reads as nonsense on
 * the page.
 */
/**
 * The text of each `<td>`, so a table assertion is about the cell and not the page.
 *
 * Needed because prose elsewhere on a page can contain the same characters a cell
 * is being checked for. Asserting that the revisions page "contains an em-dash"
 * passed against a page rendering `+0.00%` in the cell under test, because the
 * summary line above the table joins the company name to the count with one.
 */
export function cells(html: string): string[] {
  return [...html.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)].map((match) => toText(match[1]));
}

export function toText(html: string): string {
  return html
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&rsquo;/g, "’")
    .replace(/&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}
