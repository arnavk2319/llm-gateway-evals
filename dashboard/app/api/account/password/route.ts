import { NextResponse } from "next/server";
import { validateCredentials } from "@/lib/accounts";
import { currentUser, sameOrigin, sessionCookie } from "@/lib/auth";
import { hashPassword, verifyPassword } from "@/lib/password";
import { changePassword, passwordHashOf } from "@/lib/users";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const user = await currentUser();
  if (!user) return NextResponse.redirect(new URL("/login?next=%2Faccount", req.url), 303);
  const form = await req.formData().catch(() => null);
  const current = String(form?.get("current") ?? "");
  const next = String(form?.get("password") ?? "");
  const back = (notice: string) => NextResponse.redirect(new URL(`/account?notice=${notice}`, req.url), 303);

  await new Promise((r) => setTimeout(r, 300));
  const stored = await passwordHashOf(user.id);
  if (!stored || !(await verifyPassword(current, stored))) return back("wrong-password");
  if (validateCredentials("a@b.co", next)) return back("weak-password");

  await changePassword(user.id, await hashPassword(next));
  const res = back("password-changed"); // every older session is now void; this browser gets a fresh one
  const c = await sessionCookie(user.id, req);
  res.cookies.set(c.name, c.value, c.options);
  return res;
}
