/**
 * The dashboard's only connection to data: the gateway's internal API (/dashboard/v1).
 * The dashboard has no database access (docs-internal/adr/0001). Server-side only: GATEWAY_DASHBOARD_TOKEN
 * is a secret and must never reach the browser, so nothing here may be imported by a client component.
 */

export class GatewayError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "GatewayError";
    this.status = status;
  }
}

export type GatewayConfig = { url: string; token: string };

export function gatewayConfig(env: Record<string, string | undefined> = process.env): GatewayConfig | null {
  const url = (env.GATEWAY_URL ?? "").trim().replace(/\/+$/, "");
  const token = (env.GATEWAY_DASHBOARD_TOKEN ?? "").trim();
  return url && token ? { url, token } : null;
}

const TIMEOUT_MS = 15_000; // leaves room for a scaled-to-zero gateway or database to wake up

type Query = Record<string, string | number | null | undefined>;
type Options = { method?: "GET" | "POST" | "PATCH" | "DELETE"; query?: Query; body?: unknown };

/** Call the gateway. Throws GatewayError for any failure; `status` is 0 when it could not be reached. */
export async function gw<T>(path: string, { method = "GET", query, body }: Options = {}): Promise<T> {
  const cfg = gatewayConfig();
  if (!cfg) throw new GatewayError("Set GATEWAY_URL and GATEWAY_DASHBOARD_TOKEN (see dashboard/.env.example).", 0);

  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== null && v !== undefined && v !== "") qs.set(k, String(v));
  const url = `${cfg.url}/dashboard/v1${path}${qs.size ? `?${qs}` : ""}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: {
        authorization: `Bearer ${cfg.token}`,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new GatewayError("The gateway is unreachable. Check GATEWAY_URL and that the service is running.", 0);
  }

  if (!res.ok) {
    const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => undefined);
    // 422 details are lists of validation problems; show something readable rather than JSON.
    const message = typeof detail === "string" ? detail : `gateway answered ${res.status}`;
    throw new GatewayError(message, res.status);
  }
  return (await res.json()) as T;
}

/** For callers that treat one status as an answer (409 "limit", 404 "not found") rather than an error. */
export async function gwOrStatus<T>(
  path: string,
  options: Options,
  statuses: number[],
): Promise<{ ok: true; data: T } | { ok: false; status: number; message: string }> {
  try {
    return { ok: true, data: await gw<T>(path, options) };
  } catch (e) {
    if (e instanceof GatewayError && statuses.includes(e.status)) return { ok: false, status: e.status, message: e.message };
    throw e;
  }
}
