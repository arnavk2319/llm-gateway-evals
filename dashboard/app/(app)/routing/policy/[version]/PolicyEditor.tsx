"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type Props = { version: number; status: string; notes: string; references: string };

/** Notes and external references for a version, plus promote / roll back and (drafts only) delete. */
export function PolicyEditor({ version, status, notes, references }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function call(url: string, init: RequestInit, onOk: () => void) {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(url, init);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) setMessage({ ok: false, text: body.error ?? "That did not work." });
      else onOk();
    } catch {
      setMessage({ ok: false, text: "Network error. Try again." });
    } finally {
      setBusy(false);
    }
  }

  function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    void call(
      `/api/admin/policies/${version}`,
      { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ notes: data.get("notes"), references: data.get("references") }) },
      () => {
        setMessage({ ok: true, text: "Saved." });
        router.refresh();
      },
    );
  }

  function activate() {
    const verb = status === "draft" ? "Make this draft the active policy" : "Roll back to this version";
    if (!window.confirm(`${verb}? Routing switches to version ${version} within about 30 seconds.`)) return;
    void call(`/api/admin/policies/${version}/activate`, { method: "POST" }, () => router.refresh());
  }

  function remove() {
    if (!window.confirm(`Delete draft version ${version}? This cannot be undone.`)) return;
    void call(`/api/admin/policies/${version}`, { method: "DELETE" }, () => router.push("/routing/policy"));
  }

  return (
    <form onSubmit={save} className="card">
      <h2 style={{ margin: "0 0 12px" }}>Notes and references</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "14px 16px" }}>
        <label className="field">
          Notes
          <textarea className="input" name="notes" rows={5} maxLength={1000} defaultValue={notes} placeholder="Why this version exists" />
        </label>
        <label className="field">
          External references
          <textarea className="input" name="references" rows={5} defaultValue={references} placeholder={"release_version=0.2.0\nticket=OPS-12"} spellCheck={false} />
          <span className="hint">One key=value per line. Keys: letters, numbers, . _ - Up to 20.</span>
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14, alignItems: "center" }}>
        <button type="submit" className="btn pri" disabled={busy}>
          Save
        </button>
        {status !== "active" ? (
          <button type="button" className="btn" disabled={busy} onClick={activate}>
            {status === "draft" ? "Make active" : "Roll back to this version"}
          </button>
        ) : null}
        {status === "draft" ? (
          <button type="button" className="btn danger" disabled={busy} onClick={remove}>
            Delete draft
          </button>
        ) : null}
        {message ? (
          <span role={message.ok ? "status" : "alert"} className="muted" style={{ color: message.ok ? undefined : "var(--bad, #b42318)" }}>
            {message.text}
          </span>
        ) : null}
      </div>
    </form>
  );
}
