"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function ToggleUser({ id, disabled }: { id: string; disabled: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await fetch("/api/admin/users", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id, disabled: !disabled }) });
        setBusy(false);
        router.refresh();
      }}
    >
      {disabled ? "Enable" : "Disable"}
    </button>
  );
}
