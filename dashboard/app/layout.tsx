import type { Metadata } from "next";
import Link from "next/link";
import { currentUser } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "LLM Gateway",
  description: "Cost, latency, errors and routing for the LLM gateway",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const user = await currentUser();
  return (
    <html lang="en">
      <body>
        <nav className="sitenav">
          {user ? (
            <>
              <Link href="/">Overview</Link>
              <Link href="/logs">Logs</Link>
              <Link href="/keys">API keys</Link>
              <Link href="/account">Account</Link>
              {user.role === "admin" ? <Link href="/admin/users">Users</Link> : null}
              <a href="/api/export/spans?format=csv">Export calls</a>
              <span className="muted" style={{ marginLeft: "auto" }}>{user.email}</span>
              <form method="post" action="/api/logout">
                <button type="submit" className="btn">Sign out</button>
              </form>
            </>
          ) : (
            <>
              <Link href="/login" style={{ marginLeft: "auto" }}>Sign in</Link>
              <Link href="/signup">Sign up</Link>
            </>
          )}
        </nav>
        {user && !user.verified ? (
          <div role="status" className="banner">
            Confirm your email address to create API keys. <Link href="/account">Open your account</Link>
          </div>
        ) : null}
        {children}
      </body>
    </html>
  );
}
