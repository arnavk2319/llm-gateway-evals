import { currentUser, noStore, sameOrigin } from "./auth.ts";

const fail = (error: string, status: number) => Response.json({ error }, { status, headers: noStore });

/** Shared checks for admin-only mutations: same-origin, signed in, an admin, and (when sent) a JSON body. */
export async function adminGuard(req: Request, expectJson: boolean): Promise<Response | null> {
  if (!sameOrigin(req) || (expectJson && !(req.headers.get("content-type") ?? "").includes("application/json"))) {
    return fail("bad request", 400);
  }
  const user = await currentUser();
  if (!user) return fail("unauthorized", 401);
  if (user.role !== "admin") return fail("forbidden", 403);
  return null;
}

export { fail };
