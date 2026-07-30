import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * These pages are React Server Components: `async function Page()` returning a
 * tree of ordinary synchronous components. So the suite runs in `node`, not
 * jsdom -- awaiting the page and rendering the tree to static markup exercises
 * exactly what the server sends, with no browser emulation in between and no
 * pretence that client-side behaviour is being covered. What a browser does
 * afterwards is not tested here, and `docs/decisions.md` D26 says so.
 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.tsx", "tests/**/*.test.ts"],
    // `lib/api.ts` reads this once at module load. Pointing it at a host that
    // does not exist means a test whose fetch stub fails to intercept gets a
    // connection error, not a silent call to a real service on localhost --
    // where this repository has already been fooled once by a health check that
    // answered from a different project's API on a port it happened to share.
    env: { ALETHEIA_API: "http://api.test" },
    // A warning printed and swallowed is how a rendering defect survives a green
    // suite. React logs key/prop violations through console.error rather than
    // throwing, so the suite has to treat that channel as fatal itself.
    setupFiles: ["./tests/setup.ts"],
  },
});
