import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { LoadError, PageHead } from "@/components/ui";
import { requireAdmin } from "@/lib/auth";
import { ago, int } from "@/lib/format";
import { VERSION, formatRefs, getPolicy, shortRelease } from "@/lib/policies";
import { PolicyEditor } from "./PolicyEditor";

export const metadata: Metadata = { title: "Policy version" };
export const dynamic = "force-dynamic";

const TONE: Record<string, string> = { active: "ok", draft: "warn" };
const when = (iso: string | null) => (iso ? `${new Date(iso).toLocaleString()} (${ago(iso)})` : "–");

export default async function PolicyVersionPage({ params }: { params: Promise<{ version: string }> }) {
  await requireAdmin();
  const { version } = await params;
  if (!VERSION.test(version)) notFound();

  let policy;
  try {
    policy = await getPolicy(Number(version));
  } catch (e) {
    return (
      <>
        <PageHead title={`Version ${version}`} />
        <LoadError title="Can't read this policy version" error={e} />
      </>
    );
  }
  if (!policy) notFound();

  const models = policy.body.models ?? [];
  const facts: [string, React.ReactNode][] = [
    ["Source", policy.source ?? policy.createdBy ?? "–"],
    ["Published", when(policy.createdAt)],
    ["Active since", when(policy.activatedAt)],
    ["Parent version", policy.parentVersion === null ? "–" : <Link key="p" href={`/routing/policy/${policy.parentVersion}`}>v{policy.parentVersion}</Link>],
    ["Gateway version", policy.appVersion ?? "–"],
    ["Release", <code key="r" title={policy.release ?? undefined}>{shortRelease(policy.release)}</code>],
    ["Branch", policy.gitBranch ?? "–"],
    ["Models", policy.modelCount === null ? "–" : int(policy.modelCount)],
    ["Content hash", <code key="h">{policy.contentHash ? policy.contentHash.slice(0, 16) : "–"}</code>],
  ];

  return (
    <>
      <PageHead title={`Version ${policy.version}`} sub={<Link href="/routing/policy">All versions</Link>}>
        <span className={`pill ${TONE[policy.status] ?? "mute"}`}>{policy.status}</span>
      </PageHead>

      <div className="card">
        <dl className="kv" style={{ margin: 0 }}>
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt className="k">{k}</dt>
              <dd className="v" style={{ margin: 0 }}>{v}</dd>
            </div>
          ))}
        </dl>
      </div>

      <PolicyEditor version={policy.version} status={policy.status} notes={policy.notes ?? ""} references={formatRefs(policy.externalRefs)} />

      <section className="tablewrap">
        <div className="panel-title">Models in this version</div>
        <table>
          <thead>
            <tr>
              <th>Id</th>
              <th>Provider</th>
              <th>Upstream model</th>
              <th>Family</th>
              <th className="n">Tier</th>
              <th className="n">Context</th>
              <th className="n">$ in / out per 1M</th>
              <th>Billing</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id} style={{ height: 40 }}>
                <td>{m.id}</td>
                <td>{m.provider}</td>
                <td>{m.model}</td>
                <td>{m.family}</td>
                <td className="n">{m.tier}</td>
                <td className="n">{m.capabilities?.max_context ? int(m.capabilities.max_context) : "–"}</td>
                <td className="n">
                  {m.price ? `${m.price.input_per_1m ?? 0} / ${m.price.output_per_1m ?? 0}` : "–"}
                </td>
                <td>{m.free ? "free tier" : "billed"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
