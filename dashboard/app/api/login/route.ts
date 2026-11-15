import { NextResponse } from "next/server";
import { authConfig, COOKIE, safeEqual, safeNext, SESSION_TTL_S, signSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const cfg = authConfig();
  const form = await req.formData().catch(() => null);
  const next = safeNext(String(form?.get("next") ?? ""));
  const back = (error: string) => NextResponse.redirect(new URL(`/login?error=${error}`, req.url), 303);

  if (!cfg.ok) return back("unconfigured");
  if ("open" in cfg) return NextResponse.redirect(new URL(next, req.url), 303);

  // Slow every attempt down a little: this endpoint is the only thing guessing can target.
  await new Promise((r) => setTimeout(r, 400));
  const given = String(form?.get("password") ?? "");
  if (!safeEqual(given, cfg.password)) return back("invalid");

  const res = NextResponse.redirect(new URL(next, req.url), 303);
  res.cookies.set(COOKIE, await signSession(cfg.secret), {
    httpOnly: true,
    secure: new URL(req.url).protocol === "https:",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_TTL_S,
  });
  return res;
}
