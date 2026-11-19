import { noStore } from "@/lib/auth";
import { adminGuard, fail } from "@/lib/adminRoute";
import { MAX_NOTES, VERSION, deletePolicy, parseRefs, updatePolicyMeta } from "@/lib/policies";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ version: string }> };

/** Admin only: edit a version's notes and external references. The routing content itself never changes. */
export async function PATCH(req: Request, { params }: Ctx) {
  const denied = await adminGuard(req, true);
  if (denied) return denied;
  const { version } = await params;
  if (!VERSION.test(version)) return fail("bad request", 400);
  const body = (await req.json().catch(() => null)) as { notes?: unknown; references?: unknown } | null;
  if (typeof body?.notes !== "string" || typeof body.references !== "string") return fail("bad request", 400);
  const notes = body.notes.trim();
  if (notes.length > MAX_NOTES) return fail(`Notes are at most ${MAX_NOTES} characters.`, 400);
  const refs = parseRefs(body.references);
  if (!refs.ok) return fail(refs.error, 400);
  const r = await updatePolicyMeta(Number(version), { notes: notes || null, externalRefs: refs.refs });
  if (!r.ok) return fail(r.status === 404 ? "No such version." : r.message, r.status);
  return Response.json(r.data, { headers: noStore });
}

/** Admin only: delete a draft. A published version stays, so logged routing decisions keep their explanation. */
export async function DELETE(req: Request, { params }: Ctx) {
  const denied = await adminGuard(req, false);
  if (denied) return denied;
  const { version } = await params;
  if (!VERSION.test(version)) return fail("bad request", 400);
  const r = await deletePolicy(Number(version));
  if (!r.ok) return fail(r.status === 404 ? "No such version." : r.message, r.status);
  return Response.json({ ok: true }, { headers: noStore });
}
