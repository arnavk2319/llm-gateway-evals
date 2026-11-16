import { getKeyLimits, listKeys } from "@/lib/apikeys";
import { requireUser } from "@/lib/auth";
import { ago } from "@/lib/format";
import { CreateKey, RevokeButton } from "./KeyManager";

export const dynamic = "force-dynamic";

export default async function KeysPage() {
  const user = await requireUser();
  const base = (process.env.GATEWAY_PUBLIC_URL ?? "https://YOUR-GATEWAY.vercel.app").replace(/\/+$/, "");

  let keys, limits;
  try {
    [keys, limits] = await Promise.all([listKeys(user.id), getKeyLimits()]);
  } catch (e) {
    return (
      <main>
        <h1>API keys</h1>
        <div className="empty">
          <p>Can&apos;t read your keys. Is the gateway running and reachable?</p>
          <p className="muted">{e instanceof Error ? e.message : String(e)}</p>
        </div>
      </main>
    );
  }

  return (
    <main>
      <header className="top">
        <div>
          <h1>API keys</h1>
          <span className="muted">Signed in as {user.email}. Keys you create here work directly against the gateway.</span>
        </div>
      </header>

      {user.verified ? null : (
        <p className="warn">You can create keys once your email address is confirmed. <a href="/account">Open your account</a>.</p>
      )}
      <CreateKey baseUrl={base} defaultRpm={limits.defaultRpm} maxRpm={limits.maxRpm} />

      <section>
        <h2>Your keys</h2>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Key</th>
                <th>Status</th>
                <th>Models</th>
                <th className="n">Req/min</th>
                <th>Expires</th>
                <th>Last used</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.length === 0 ? (
                <tr>
                  <td colSpan={8} className="muted">No keys yet. Create one above.</td>
                </tr>
              ) : (
                keys.map((k) => (
                  <tr key={k.id}>
                    <td>{k.name}</td>
                    <td><code>{k.prefix}…</code></td>
                    <td className={k.status === "active" ? "ok" : "muted"}>{k.status}</td>
                    <td>{k.models?.length ? k.models.join(", ") : "all"}</td>
                    <td className="n">{k.rpm ?? "default"}</td>
                    <td>{k.expiresAt ? new Date(k.expiresAt).toLocaleDateString() : "never"}</td>
                    <td>{k.lastUsedAt ? ago(k.lastUsedAt) : "never"}</td>
                    <td>{k.status === "active" ? <RevokeButton id={k.id} name={k.name} /> : null}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>Use a key</h2>
        <p className="muted">
          The gateway speaks the OpenAI API. Point any OpenAI SDK at it and pass your key as the API key, no other change:
        </p>
        <pre>{`from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="gw_live_...")
client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "Hello"}])`}</pre>
        <pre>{`import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${base}/v1", apiKey: process.env.GATEWAY_KEY });`}</pre>
        <p className="muted">
          Use <code>model: &quot;auto&quot;</code> to let the gateway route, or name a model. Keep keys on your server, never in browser code.
          Revoking takes effect within a minute.
        </p>
      </section>
    </main>
  );
}
