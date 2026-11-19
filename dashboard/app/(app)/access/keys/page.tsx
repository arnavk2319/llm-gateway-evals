import type { Metadata } from "next";
import { LoadError, PageHead } from "@/components/ui";
import { getKeyLimits, listKeys } from "@/lib/apikeys";
import { requireUser } from "@/lib/auth";
import { ago } from "@/lib/format";
import { CreateKey, RevokeButton } from "./KeyManager";

export const metadata: Metadata = { title: "API keys" };
export const dynamic = "force-dynamic";

export default async function KeysPage() {
  const user = await requireUser();
  const base = (process.env.GATEWAY_PUBLIC_URL ?? "https://YOUR-GATEWAY.example.com").replace(/\/+$/, "");

  let keys, limits;
  try {
    [keys, limits] = await Promise.all([listKeys(user.id), getKeyLimits()]);
  } catch (e) {
    return (
      <>
        <PageHead title="API keys" />
        <LoadError title="Can't read your keys" error={e} />
      </>
    );
  }
  const active = keys.filter((k) => k.status === "active").length;

  return (
    <>
      <PageHead title="API keys" sub={`Use a key as the bearer token or the OpenAI client's api_key. ${active} of ${limits.maxPerUser} active keys used.`} />

      {user.verified ? null : <p className="warn">You can create keys once your email address is confirmed. <a href="/account">Open your account</a>.</p>}
      <CreateKey baseUrl={base} defaultRpm={limits.defaultRpm} maxRpm={limits.maxRpm} canCreate={user.verified} />

      <section className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Key</th>
              <th>Status</th>
              <th className="n">Rate limit</th>
              <th>Models</th>
              <th>Expires</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 ? (
              <tr>
                <td colSpan={8} className="muted">
                  No keys yet. Create one to start sending requests.
                </td>
              </tr>
            ) : (
              keys.map((k) => (
                <tr key={k.id} style={{ height: 44 }}>
                  <td style={{ fontWeight: 600 }}>{k.name}</td>
                  <td>
                    <code className="muted">{k.prefix}…</code>
                  </td>
                  <td>
                    <span className={`pill ${k.status === "active" ? "ok" : "mute"}`}>{k.status}</span>
                  </td>
                  <td className="n">{k.rpm ? `${k.rpm}/min` : "default"}</td>
                  <td>
                    <code>{k.models?.length ? k.models.join(", ") : "all"}</code>
                  </td>
                  <td>{k.expiresAt ? new Date(k.expiresAt).toLocaleDateString() : "Never"}</td>
                  <td>{k.lastUsedAt ? ago(k.lastUsedAt) : "Never"}</td>
                  <td className="n">{k.status === "active" ? <RevokeButton id={k.id} name={k.name} /> : null}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      <section style={{ marginTop: 24 }}>
        <h2>Use a key</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          The gateway speaks the OpenAI API. Point any OpenAI SDK at it and pass your key as the API key. Revoking takes effect within a minute.
        </p>
        <pre>{`from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="gw_live_...")
client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "Hello"}])`}</pre>
        <pre>{`import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${base}/v1", apiKey: process.env.GATEWAY_KEY });`}</pre>
        <p className="muted">
          Use <code>model: &quot;auto&quot;</code> to let the gateway route, or name a model. Keep keys on your server, never in browser code.
        </p>
      </section>
    </>
  );
}
