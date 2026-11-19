import { noStore } from "@/lib/auth";
import { adminGuard, fail } from "@/lib/adminRoute";
import { VERSION, activatePolicy } from "@/lib/policies";

export const dynamic = "force-dynamic";

/** Admin only: make a version the active routing policy (promote a draft, or roll back to an earlier version). */
export async function POST(req: Request, { params }: { params: Promise<{ version: string }> }) {
  const denied = await adminGuard(req, false);
  if (denied) return denied;
  const { version } = await params;
  if (!VERSION.test(version)) return fail("bad request", 400);
  const r = await activatePolicy(Number(version));
  if (!r.ok) return fail(r.status === 404 ? "No such version." : r.message, r.status);
  return Response.json(r.data, { headers: noStore });
}
