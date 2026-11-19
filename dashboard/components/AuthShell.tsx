import Link from "next/link";
import type { ReactNode } from "react";
import { APP_NAME, Logo } from "./Logo";

/**
 * The sign-in frame: the form on the left, and on the right either a sample of what the product records
 * ("proof") or the three steps from sign-up to a first request ("steps").
 */
export function AuthShell({ side = "proof", children, foot }: { side?: "proof" | "steps"; children: ReactNode; foot?: ReactNode }) {
  return (
    <div className="auth">
      <section className="auth-left">
        <Link href="/" className="auth-brand">
          <Logo size={28} tile="var(--rail)" />
          {APP_NAME}
        </Link>
        <div className="auth-main">
          <div className="auth-form">{children}</div>
        </div>
        {foot ? <p className="auth-foot">{foot}</p> : null}
      </section>

      {side === "proof" ? (
        <aside className="auth-right" aria-label="What you get">
          <div className="proof">
            <p className="eyebrow">Last request through your gateway</p>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span className="mono" style={{ color: "var(--rail-ink)" }}>run_demo_4</span>
              <span className="pill ok">ok</span>
            </div>
            <dl>
              <div><dt>Model</dt><dd>mock/small</dd></div>
              <div><dt>Latency</dt><dd>19 ms</dd></div>
              <div><dt>Attempts</dt><dd>1</dd></div>
            </dl>
            <div className="meter" aria-hidden="true"><span /><span /><span /></div>
            <div className="mono" style={{ fontSize: 11.5, color: "var(--rail-ink)", marginTop: 8 }}>intake 2 ms, route 1 ms, call 16 ms</div>
          </div>
          <div>
            <h2>Every call, explained.</h2>
            <p>Cost, latency, fallbacks and the reason behind each route, kept for every request your apps make.</p>
          </div>
        </aside>
      ) : (
        <aside className="auth-right" aria-label="What happens next">
          <h2>From sign-up to first request in three steps.</h2>
          <ol className="steps-list">
            <li><span className="dot">1</span><div><b>Confirm your email</b><span>One link, valid for 24 hours.</span></div></li>
            <li><span className="dot">2</span><div><b>Create an API key</b><span>Name it, set a rate limit, optionally restrict the models. The secret is shown once.</span></div></li>
            <li><span className="dot">3</span><div><b>Send a request</b><span>Use your OpenAI client with the gateway as base URL, then watch it appear on the dashboard.</span></div></li>
          </ol>
          <div className="proof" style={{ padding: 0 }}>
            <pre className="mono" style={{ margin: 0, padding: "18px 20px", fontSize: 13, lineHeight: 1.7, color: "#e7eeec", background: "transparent", border: 0 }}>
              {`client = OpenAI(\n    base_url="https://your-gateway.example.com/v1",\n    api_key="gw_live_...",\n)`}
            </pre>
          </div>
        </aside>
      )}
    </div>
  );
}
