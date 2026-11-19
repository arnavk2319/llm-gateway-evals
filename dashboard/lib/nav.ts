/** The signed-in app's navigation: one overview page and nested groups. Used by the rail and the breadcrumb. */
export type NavItem = { href: string; label: string; soon?: boolean };
export type NavGroup = { id: string; label: string; items: NavItem[]; adminOnly?: boolean };

export const OVERVIEW: NavItem = { href: "/overview", label: "Overview" };

export const GROUPS: NavGroup[] = [
  {
    id: "traffic",
    label: "Traffic",
    items: [
      { href: "/traffic/requests", label: "Requests" },
      { href: "/traffic/runs", label: "Runs and traces" },
      { href: "/traffic/logs", label: "Logs" },
    ],
  },
  {
    id: "routing",
    label: "Routing",
    items: [
      { href: "/routing/models", label: "Models" },
      { href: "/routing/roles", label: "Roles and tasks" },
      { href: "/routing/policy", label: "Policy versions" },
    ],
  },
  {
    id: "evaluate",
    label: "Evaluate",
    items: [
      { href: "/evaluate/quality", label: "Quality scores", soon: true },
      { href: "/evaluate/regret", label: "Cost regret", soon: true },
    ],
  },
  {
    id: "access",
    label: "Access",
    items: [
      { href: "/access/keys", label: "API keys" },
      { href: "/access/usage", label: "Usage by key", soon: true },
    ],
  },
  { id: "admin", label: "Admin", adminOnly: true, items: [{ href: "/admin/users", label: "Users" }] },
];

export const isActive = (pathname: string, href: string) => pathname === href || pathname.startsWith(href + "/");

/** Breadcrumb labels for a path: group, page, and for a detail page its id. */
export function crumbsFor(pathname: string): string[] {
  if (pathname === OVERVIEW.href) return [OVERVIEW.label];
  if (pathname === "/account") return ["Account"];
  for (const g of GROUPS) {
    for (const it of g.items) {
      if (pathname === it.href) return [g.label, it.label];
      if (pathname.startsWith(it.href + "/")) {
        let rest = pathname.slice(it.href.length + 1);
        try {
          rest = decodeURIComponent(rest);
        } catch {
          /* keep the raw segment */
        }
        return [g.label, it.label, rest];
      }
    }
  }
  return [];
}
