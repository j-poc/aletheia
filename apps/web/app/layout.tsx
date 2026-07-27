import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "ALETHEIA",
  description: "A point-in-time evidence engine for systematic equity research.",
};

const NAV = [
  { href: "/", label: "As-of viewer" },
  { href: "/revisions", label: "Revisions" },
  { href: "/feed", label: "Filing feed" },
  { href: "/evidence", label: "Evidence" },
  { href: "/quality", label: "Data quality" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-[var(--color-edge)]">
          <div className="mx-auto flex max-w-6xl flex-wrap items-baseline gap-x-6 gap-y-2 px-6 py-4">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              ALETHEIA
            </Link>
            <span className="text-xs text-[var(--color-muted)]">
              what was knowable, when
            </span>
            <nav className="ml-auto flex flex-wrap gap-5 text-sm">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="text-[var(--color-muted)] transition-colors hover:text-white"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
