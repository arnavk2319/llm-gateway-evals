import { currentUser, noStore, sameOrigin } from "@/lib/auth";
import { setDisabled } from "@/lib/users";

export const dynamic = "force-dynamic";

/** Admin only: disable or re-enable a user. A disabled user's sessions and API keys stop working. */
export async function POST(req: Request) {
  if (!sameOrigin(req) || !(req.headers.get("content-type") ?? "").includes("application/json")) {
    return Response.json({ error: "bad request" }, { status: 400, headers: noStore });
  }
  const user = await currentUser();
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401, headers: noStore });
  if (user.role !== "admin") return Response.json({ error: "forbidden" }, { status: 403, headers: noStore });
  const body = (await req.json().catch(() => null)) as { id?: unknown; disabled?: unknown } | null;
  if (typeof body?.id !== "string" || !/^usr_[a-f0-9]{12}$/.test(body.id) || typeof body.disabled !== "boolean") {
    return Response.json({ error: "bad request" }, { status: 400, headers: noStore });
  }
  const ok = await setDisabled(body.id, body.disabled).catch(() => false);
  return Response.json({ ok }, { status: ok ? 200 : 404, headers: noStore });
}
