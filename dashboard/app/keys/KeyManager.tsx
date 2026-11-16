"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type Created = { key: string; id: string; name: string; rpm: number };

export function CreateKey({ baseUrl, defaultRpm, maxRpm }: { baseUrl: string; defaultRpm: number; maxRpm: number }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [copied, setCopied] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/keys", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body.error ?? "Could not create the key.");
      } else {
        setCreated(body);
        setCopied(false);
        form.reset();
        router.refresh();
      }
    } catch {
      setError("Network error. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const curl = created
    ? `curl ${baseUrl}/v1/chat/completions \\\n  -H "Authorization: Bearer ${created.key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"auto","messages":[{"role":"user","content":"Hello"}]}'`
    : "";

  return (
    <section>
      <h2>Create a key</h2>
      <form onSubmit={submit} className="filters" style={{ alignItems: "flex-end" }}>
        <label className="field">Name<input name="name" placeholder="my-app" required maxLength={60} style={{ width: 170 }} /></label>
        <label className="field">Expires in days<input name="expiresDays" type="number" min={1} max={365} placeholder="never" style={{ width: 130 }} /></label>
        <label className="field">Requests / minute<input name="rpm" type="number" min={1} max={maxRpm} placeholder={`${defaultRpm} (max ${maxRpm})`} style={{ width: 150 }} /></label>
        <label className="field">Allowed models<input name="models" placeholder="all (or comma separated ids)" style={{ width: 260 }} /></label>
        <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create key"}</button>
      </form>
      {error ? <p role="alert" className="bad">{error}</p> : null}

      {created ? (
        <div className="card" role="status">
          <strong>Copy your key for “{created.name}” now.</strong> It is shown once and cannot be recovered. Lose it and you create a new one.
          <pre className="secret">{created.key}</pre>
          <button
            type="button"
            onClick={() => navigator.clipboard.writeText(created.key).then(() => setCopied(true), () => setCopied(false))}
          >
            {copied ? "Copied" : "Copy key"}
          </button>{" "}
          <button type="button" className="btn" onClick={() => setCreated(null)}>I have saved it</button>
          <p className="muted" style={{ marginBottom: 4 }}>Try it:</p>
          <pre>{curl}</pre>
        </div>
      ) : null}
    </section>
  );
}

export function RevokeButton({ id, name }: { id: string; name: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn"
      disabled={busy}
      onClick={async () => {
        if (!confirm(`Revoke “${name}”? Apps using it will stop working.`)) return;
        setBusy(true);
        await fetch("/api/keys/revoke", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id }) });
        setBusy(false);
        router.refresh();
      }}
    >
      Revoke
    </button>
  );
}
