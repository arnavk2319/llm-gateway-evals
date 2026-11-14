import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "LLM Gateway",
  description: "Cost, latency, errors and routing for the LLM gateway",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="sitenav">
          <Link href="/">Overview</Link>
          <Link href="/logs">Logs</Link>
          <a href="/api/export/spans?format=csv">Export calls</a>
        </nav>
        {children}
      </body>
    </html>
  );
}
