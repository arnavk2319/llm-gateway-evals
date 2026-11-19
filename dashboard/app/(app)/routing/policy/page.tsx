import type { Metadata } from "next";
import { LoadError, PageHead } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { ago } from "@/lib/format";
import { getDashboard } from "@/lib/queries";

export const metadata: Metadata = { title: "Policy versions" };
export const dynamic = "force-dynamic";

export default async function PolicyPage() {
  const user = await requireUser();
  let policy;
  try {
    policy = (await getDashboard(24, scopeOf(user))).policy;
  } catch (e) {
    return (
      <>
        <PageHead title="Policy versions" />
        <LoadError title="Can't reach the gateway" error={e} />
      </>
    );
  }
  return (
    <>
      <PageHead title="Policy versions" sub="The routing policy ranks candidate models by cost, latency and quality." />
      <div className="card">
        {policy ? (
          <>
            <span className="pill ok">Active</span>
            <p style={{ margin: "10px 0 0", fontSize: 18, fontFamily: "var(--font-display)", fontWeight: 600 }}>Version {policy.version}</p>
            <p className="muted" style={{ margin: "2px 0 0" }}>
              Published {ago(policy.createdAt)}
            </p>
          </>
        ) : (
          <p className="muted" style={{ margin: 0 }}>No active routing policy has been recorded yet.</p>
        )}
      </div>
      <p className="muted">Earlier versions, their eval results and a way to roll back arrive with the control plane.</p>
    </>
  );
}
