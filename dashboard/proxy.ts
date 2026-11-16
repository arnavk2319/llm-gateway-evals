import { type NextRequest, NextResponse } from "next/server";
import { authConfig, COOKIE, verifySession } from "@/lib/session";

// Reachable without a session. Everything else needs a signed cookie; pages and API routes then
// look the user up again, so a disabled account loses access immediately.
const PUBLIC = [
  "/login", "/signup", "/forgot", "/reset", "/verify",
  "/api/login", "/api/signup", "/api/forgot", "/api/reset", "/api/verify",
];

export async function proxy(req: NextRequest) {
  const cfg = authConfig();
  const { pathname } = req.nextUrl;

  if (!cfg.ok) {
    return new NextResponse("Dashboard is not configured: set DASHBOARD_SESSION_SECRET (32+ random characters).", {
      status: 503,
      headers: { "cache-control": "no-store" },
    });
  }
  if (PUBLIC.includes(pathname)) return NextResponse.next();
  if (await verifySession(cfg.secret, req.cookies.get(COOKIE)?.value)) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401, headers: { "cache-control": "no-store" } });
  }
  const url = new URL("/login", req.url);
  url.searchParams.set("next", pathname + req.nextUrl.search);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
