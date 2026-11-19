import type { Metadata } from "next";
import Link from "next/link";
import { LoadError, PageHead } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { ago, int } from "@/lib/format";
import { listPolicies, shortRelease } from "@/lib/policies";
import { getDashboard } from "@/lib/queries";

export const metadata: Metadata = { title: "Policy versions" };
export const dynamic = "force-dynamic";

const TONE: Record<string, string> = { active: "ok", draft: "warn" };

export default async function PolicyPage() {
  const user = await requireUser();
  if (user.role === "admin") return <AdminVersions />;

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
    </>
  );
}

async function AdminVersions() {
  let versions;
  try {
    versions = await listPolicies();
  } catch (e) {
    return (
      <>
        <PageHead title="Policy versions" />
        <LoadError title="Can't read policy versions" error={e} />
      </>
    );
  }
  return (
    <>
      <PageHead
        title="Policy versions"
        sub="Every routing policy the gateway has published. A change to the policy file becomes a new version when the gateway next starts."
      />
      {versions.length === 0 ? (
        <div className="empty">No policy versions have been recorded yet.</div>
      ) : (
        <section className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Source</th>
                <th className="n">Models</th>
                <th>Gateway</th>
                <th>Release</th>
                <th>Branch</th>
                <th>Published</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version} style={{ height: 44 }}>
                  <td>
                    <Link href={`/routing/policy/${v.version}`}>
                      <strong>v{v.version}</strong>
                    </Link>
                  </td>
                  <td>
                    <span className={`pill ${TONE[v.status] ?? "mute"}`}>{v.status}</span>
                  </td>
                  <td>{v.source ?? v.createdBy ?? "–"}</td>
                  <td className="n">{v.modelCount === null ? "–" : int(v.modelCount)}</td>
                  <td>{v.appVersion ?? "–"}</td>
                  <td>
                    <code>{shortRelease(v.release)}</code>
                  </td>
                  <td>{v.gitBranch ?? "–"}</td>
                  <td>{ago(v.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
