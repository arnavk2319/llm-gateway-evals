import { revokeKey } from "@/lib/apikeys";
import { currentUser, noStore, sameOrigin } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!sameOrigin(req) || !(req.headers.get("content-type") ?? "").includes("application/json")) {
    return Response.json({ error: "bad request" }, { status: 400, headers: noStore });
  }
  const user = await currentUser();
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401, headers: noStore });
  const body = (await req.json().catch(() => null)) as { id?: unknown } | null;
  if (typeof body?.id !== "string" || !/^key_[a-f0-9]{12}$/.test(body.id)) {
    return Response.json({ error: "bad request" }, { status: 400, headers: noStore });
  }
  const ok = await revokeKey(user.id, body.id).catch(() => false);
  return Response.json({ ok }, { status: ok ? 200 : 404, headers: noStore });
}
