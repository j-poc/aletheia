import { afterEach, beforeEach, expect } from "vitest";

/**
 * React reports most rendering defects -- a missing key, an invalid prop, a
 * hydration mismatch -- by writing to `console.error` and continuing. A suite
 * that leaves that channel alone is green in exactly the situation the warning
 * exists to flag, so every test here fails on one.
 *
 * The list of allowed messages is empty on purpose. If a legitimate warning ever
 * needs an exemption it goes in here by exact text, one line, so the exemption is
 * as visible as the failure it suppresses.
 */
const ALLOWED: readonly RegExp[] = [];

let captured: string[] = [];
let original: typeof console.error;

beforeEach(() => {
  captured = [];
  original = console.error;
  console.error = (...args: unknown[]) => {
    const message = args.map(String).join(" ");
    if (!ALLOWED.some((pattern) => pattern.test(message))) captured.push(message);
    original(...args);
  };
});

afterEach(() => {
  console.error = original;
  expect(captured, "React wrote to console.error during this test").toEqual([]);
});
