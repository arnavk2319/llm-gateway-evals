import { NextResponse } from "next/server";
import { COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const res = NextResponse.redirect(new URL("/login", req.url), 303);
  res.cookies.set(COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  return res;
}
