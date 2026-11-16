import { createKey } from "@/lib/apikeys";
import { currentUser, noStore, sameOrigin } from "@/lib/auth";
import { parseNewKey } from "@/lib/keys";

export const dynamic = "force-dynamic";

/** Create an API key for the signed-in user. The secret is in this response and nowhere else. */
export async function POST(req: Request) {
  if (!sameOrigin(req) || !(req.headers.get("content-type") ?? "").includes("application/json")) {
    return Response.json({ error: "bad request" }, { status: 400, headers: noStore });
  }
  const user = await currentUser();
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401, headers: noStore });

  if (!user.verified) {
    return Response.json({ error: "Confirm your email address first. See the Account page." }, { status: 403, headers: noStore });
  }

  const parsed = parseNewKey(await req.json().catch(() => null));
  if (!parsed.ok) return Response.json({ error: parsed.error }, { status: 400, headers: noStore });

  try {
    const created = await createKey(user.id, parsed.value);
    if (!created.ok) return Response.json({ error: created.error }, { status: created.status, headers: noStore });
    return Response.json(created.key, { status: 201, headers: noStore });
  } catch {
    return Response.json({ error: "Could not create the key. Try again." }, { status: 503, headers: noStore });
  }
}
