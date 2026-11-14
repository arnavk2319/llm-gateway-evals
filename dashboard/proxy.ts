import { type NextRequest, NextResponse } from "next/server";
import { authConfig, COOKIE, verifySession } from "@/lib/session";

const PUBLIC = ["/login", "/api/login"];

export async function proxy(req: NextRequest) {
  const cfg = authConfig();
  const { pathname } = req.nextUrl;

  if (!cfg.ok) {
    return new NextResponse(
      "Dashboard is not configured: set DASHBOARD_PASSWORD (12+ characters) and DASHBOARD_SESSION_SECRET (32+ characters).",
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  if ("open" in cfg || PUBLIC.includes(pathname)) return NextResponse.next();

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
