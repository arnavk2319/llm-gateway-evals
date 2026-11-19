"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { crumbsFor, GROUPS, isActive, OVERVIEW } from "@/lib/nav";
import { APP_NAME, Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

type Props = { email: string; admin: boolean; env: string | null; banner?: ReactNode; children: ReactNode };

const Chevron = () => (
  <svg className="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m9 18 6-6-6-6" />
  </svg>
);

/** The signed-in shell: dark green rail with nested groups, a top bar with breadcrumb and search, and the page. */
export function AppFrame({ email, admin, env, banner, children }: Props) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const crumbs = crumbsFor(pathname);
  const groups = GROUPS.filter((g) => !g.adminOnly || admin);

  return (
    <div className="shell" data-menu={open ? "open" : "closed"}>
      <aside className="rail" aria-label="Sections" id="rail">
        <Link href={OVERVIEW.href} className="rail-brand">
          <Logo size={26} />
          <span>{APP_NAME}</span>
          {env ? <span className="pill warn rail-env">{env}</span> : null}
        </Link>

        <nav className="rail-nav">
          <Link href={OVERVIEW.href} className={`rail-link ${isActive(pathname, OVERVIEW.href) ? "on" : ""}`} aria-current={isActive(pathname, OVERVIEW.href) ? "page" : undefined}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect width="7" height="9" x="3" y="3" rx="1" />
              <rect width="7" height="5" x="14" y="3" rx="1" />
              <rect width="7" height="9" x="14" y="12" rx="1" />
              <rect width="7" height="5" x="3" y="16" rx="1" />
            </svg>
            {OVERVIEW.label}
          </Link>

          {groups.map((g) => {
            const groupActive = g.items.some((i) => isActive(pathname, i.href));
            return (
              <details key={g.id} open={groupActive || g.id !== "evaluate"} className="rail-group">
                <summary>
                  {g.label}
                  <Chevron />
                </summary>
                {g.items.map((i) => {
                  const on = isActive(pathname, i.href);
                  return (
                    <Link key={i.href} href={i.href} className={`rail-link sub ${on ? "on" : ""}`} aria-current={on ? "page" : undefined}>
                      {i.label}
                      {i.soon ? <span className="soon">Soon</span> : null}
                    </Link>
                  );
                })}
              </details>
            );
          })}
        </nav>

        <div className="rail-foot">
          <a className="rail-link" href="https://github.com" target="_blank" rel="noreferrer">
            Documentation
          </a>
          <Link href="/account" className={`rail-link account ${pathname === "/account" ? "on" : ""}`}>
            <span className="avatar" aria-hidden="true">
              {email.slice(0, 1).toUpperCase()}
            </span>
            <span className="who">
              <span>Account</span>
              <span className="mail">{email}</span>
            </span>
          </Link>
          <form method="post" action="/api/logout">
            <button type="submit" className="rail-link signout">
              Sign out
            </button>
          </form>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button type="button" className="iconbtn menu-btn" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="rail" aria-label="Menu">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <nav aria-label="Breadcrumb" className="crumbs">
            {crumbs.map((c, i) => (
              <span key={`${c}-${i}`} className={i === crumbs.length - 1 ? "last" : ""}>
                {i > 0 ? <Chevron /> : null}
                {c}
              </span>
            ))}
          </nav>
          <form action="/traffic/logs" method="get" role="search" className="search">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="8" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <input ref={search} name="q" placeholder="Search logs" aria-label="Search logs" />
            <kbd>Ctrl K</kbd>
          </form>
          <ThemeToggle />
        </header>
        {banner}
        <div className="page">{children}</div>
      </div>
    </div>
  );
}
