"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type Created = { key: string; id: string; name: string; rpm: number };

/** "New key" opens a form; the secret appears once in a strip with a copy button. */
export function CreateKey({ baseUrl, defaultRpm, maxRpm, canCreate }: { baseUrl: string; defaultRpm: number; maxRpm: number; canCreate: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
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
        setOpen(false);
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
    <div>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <span />
        <button type="button" className="btn pri" onClick={() => setOpen(!open)} aria-expanded={open} disabled={!canCreate} title={canCreate ? undefined : "Confirm your email address first"}>
          {open ? "Close" : "New key"}
        </button>
      </div>

      {created ? (
        <div className="card" role="status" style={{ borderColor: "var(--sig)", background: "var(--sig-soft)" }}>
          <strong style={{ color: "var(--sig-ink)" }}>Copy your key for “{created.name}” now. It is shown once and cannot be recovered.</strong>
          <pre className="secret">{created.key}</pre>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="btn pri" onClick={() => navigator.clipboard.writeText(created.key).then(() => setCopied(true), () => setCopied(false))}>
              {copied ? "Copied" : "Copy key"}
            </button>
            <button type="button" className="btn" onClick={() => setCreated(null)}>
              I have saved it
            </button>
          </div>
          <p className="muted" style={{ marginBottom: 4 }}>Try it:</p>
          <pre>{curl}</pre>
        </div>
      ) : null}

      {open ? (
        <form onSubmit={submit} className="card">
          <h2 style={{ margin: "0 0 12px" }}>New key</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: "14px 16px" }}>
            <label className="field">
              Name
              <input className="input" name="name" placeholder="my-app" required maxLength={60} autoFocus />
              <span className="hint">Letters, numbers, spaces, . _ -</span>
            </label>
            <label className="field">
              Expires after (days)
              <input className="input" name="expiresDays" type="number" min={1} max={365} placeholder="Never" />
              <span className="hint">1 to 365, or leave empty</span>
            </label>
            <label className="field">
              Requests per minute
              <input className="input" name="rpm" type="number" min={1} max={maxRpm} placeholder={String(defaultRpm)} />
              <span className="hint">Default {defaultRpm}, at most {maxRpm}</span>
            </label>
            <label className="field">
              Allowed models
              <input className="input mono" name="models" placeholder="All models" />
              <span className="hint">Comma separated model ids</span>
            </label>
          </div>
          {error ? (
            <p role="alert" className="bad" style={{ marginBottom: 0 }}>
              {error}
            </p>
          ) : null}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 14 }}>
            <button type="button" className="btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="btn pri" disabled={busy}>
              {busy ? "Creating…" : "Create key"}
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

export function RevokeButton({ id, name }: { id: string; name: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn danger"
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
