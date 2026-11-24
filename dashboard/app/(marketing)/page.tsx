import type { Metadata } from "next";
import Link from "next/link";
import { APP_NAME, Logo } from "@/components/Logo";
import { QuickstartTabs } from "@/components/QuickstartTabs";
import { currentUser } from "@/lib/auth";
import "../styles/public.css";

export const metadata: Metadata = { title: { absolute: "Gateway: one endpoint, the right model for every call" } };
export const dynamic = "force-dynamic";

const icon = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "var(--sig)", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

const FEATURES = [
  {
    title: "Role-aware routing",
    text: "Reviewers avoid the model that wrote the code. Classifiers get the cheap, fast one.",
    svg: (
      <svg {...icon}>
        <line x1="6" x2="6" y1="3" y2="15" />
        <circle cx="18" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <path d="M18 9a9 9 0 0 1-9 9" />
      </svg>
    ),
  },
  {
    title: "Fallback without client code",
    text: "Timeouts, rate limits and 5xx errors move to the next candidate before your app notices.",
    svg: (
      <svg {...icon}>
        <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />
      </svg>
    ),
  },
  {
    title: "Shadow cost",
    text: "See what each call would have cost on the premium model, next to what it did cost.",
    svg: (
      <svg {...icon}>
        <path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" />
        <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" />
        <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" />
      </svg>
    ),
  },
  {
    title: "Traces and searchable logs",
    text: "Filter by model, role, error class or key. Open a run to see every span and attempt.",
    svg: (
      <svg {...icon}>
        <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
      </svg>
    ),
  },
  {
    title: "Per-user keys and limits",
    text: "Each person creates their own keys with a rate limit and an allowed model list. Revoke in one click.",
    svg: (
      <svg {...icon}>
        <path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z" />
      </svg>
    ),
  },
  {
    title: "Evals that improve routing",
    text: "Quality scores feed policy proposals that go live only after passing gates. Coming next.",
    svg: (
      <svg {...icon}>
        <circle cx="12" cy="12" r="10" />
        <path d="m9 12 2 2 4-4" />
      </svg>
    ),
  },
];

export default async function Landing() {
  const user = await currentUser();
  return (
    <>
      <header className="site-header">
        <div className="wrap">
          <Link href="/" className="site-brand">
            <Logo size={28} />
            {APP_NAME}
          </Link>
          <nav aria-label="Primary" className="site-nav">
            <a className="site-link" href="#how">How it works</a>
            <a className="site-link" href="#quickstart">Quickstart</a>
            <a className="site-link" href="#features">Features</a>
            <a className="site-link" href="https://github.com" target="_blank" rel="noreferrer">GitHub</a>
          </nav>
          <div className="site-cta">
            {user ? (
              <Link className="btn bright" href="/overview">Open dashboard</Link>
            ) : (
              <>
                <Link className="site-link" href="/login">Sign in</Link>
                <Link className="btn bright" href="/signup">Create account</Link>
              </>
            )}
          </div>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="wrap">
            <div style={{ flex: "1 1 460px", minWidth: 0 }}>
              <h1 className="display">One endpoint. The right model for every call.</h1>
              <p className="lead">
                An OpenAI-compatible gateway that routes each request by role and task, falls back when a provider fails, and records every decision so you can see what it cost and why.
              </p>
              <div className="actions">
                <Link className="btn pri lg" href={user ? "/overview" : "/signup"}>
                  {user ? "Open dashboard" : "Create an account"}
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M5 12h14m-7-7 7 7-7 7" />
                  </svg>
                </Link>
                <a className="btn lg" href="#quickstart">Read the quickstart</a>
              </div>
              <p className="hint">Change base_url and the key. Nothing else in your code moves.</p>
            </div>

            <figure className="trace">
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>One request, traced</span>
                <span className="mono muted">example</span>
              </div>
              <svg viewBox="0 0 520 260" width="100%" role="img" aria-label="A request enters the router, which picks one of three models and keeps a second as the fallback">
                <rect x="6" y="104" width="100" height="52" rx="10" fill="var(--surface2)" stroke="var(--line2)" />
                <text x="56" y="135" textAnchor="middle" fontSize="14" fontWeight="600" fill="var(--ink)" fontFamily="var(--font-ui)">Your app</text>
                <line x1="106" y1="130" x2="196" y2="130" stroke="var(--sig)" strokeWidth="2" className="flow" />
                <rect x="196" y="88" width="104" height="84" rx="12" fill="var(--rail)" />
                <circle cx="222" cy="130" r="5" fill="#4cc2ba" />
                <text x="236" y="125" fontSize="14" fontWeight="600" fill="#e7eeec" fontFamily="var(--font-ui)">Router</text>
                <text x="236" y="144" fontSize="11" fill="#9fb2ad" fontFamily="var(--font-mono)">policy v1</text>
                <path d="M300 130 C 350 130 350 38 392 38" stroke="var(--line2)" strokeWidth="2" fill="none" />
                <path d="M300 130 L 392 130" stroke="var(--sig)" strokeWidth="2.4" fill="none" className="flow" />
                <path d="M300 130 C 350 130 350 222 392 222" stroke="var(--patch)" strokeWidth="2" fill="none" strokeDasharray="3 5" />
                <rect x="392" y="16" width="122" height="44" rx="9" fill="var(--surface)" stroke="var(--line2)" />
                <text x="453" y="43" textAnchor="middle" fontSize="12" fill="var(--ink2)" fontFamily="var(--font-mono)">mock/large</text>
                <rect x="392" y="106" width="122" height="48" rx="9" fill="var(--sig-soft)" stroke="var(--sig)" strokeWidth="1.6" />
                <text x="453" y="127" textAnchor="middle" fontSize="12" fontWeight="500" fill="var(--sig-ink)" fontFamily="var(--font-mono)">groq/llama-3.1</text>
                <text x="453" y="144" textAnchor="middle" fontSize="11" fill="var(--sig-ink)" fontFamily="var(--font-ui)">chosen</text>
                <rect x="392" y="200" width="122" height="44" rx="9" fill="var(--patch-soft)" stroke="var(--patch)" />
                <text x="453" y="220" textAnchor="middle" fontSize="12" fill="var(--patch-ink)" fontFamily="var(--font-mono)">mock/medium</text>
                <text x="453" y="236" textAnchor="middle" fontSize="11" fill="var(--patch-ink)" fontFamily="var(--font-ui)">fallback</text>
              </svg>
              <dl className="facts">
                <div><dt>Why this model</dt><dd>task=chat, low complexity</dd></div>
                <div><dt>Latency</dt><dd>412 ms, first token 180</dd></div>
                <div><dt>Cost vs shadow</dt><dd>$0.000021 / $0.000094</dd></div>
              </dl>
            </figure>
          </div>
        </section>

        <section id="how" className="wrap section">
          <h2>From SDK call to explained decision in three steps</h2>
          <div className="steps">
            <div className="tile">
              <div className="step">Step 1</div>
              <h3>Send the request you already send</h3>
              <p>Use the OpenAI SDK with your gateway key. Add headers for the node role and run id if you want per-role routing.</p>
            </div>
            <div className="tile">
              <div className="step">Step 2</div>
              <h3>The router picks, retries, falls back</h3>
              <p>A policy ranks models by cost, latency and quality. A failed attempt moves to the next candidate inside the same request.</p>
            </div>
            <div className="tile">
              <div className="step">Step 3</div>
              <h3>Read the trace in the dashboard</h3>
              <p>Every call records tokens, cost, latency, attempts and the reason for the route. Filter, export, or open one run end to end.</p>
            </div>
          </div>
        </section>

        <section id="quickstart" className="wrap section">
          <QuickstartTabs />
        </section>

        <section id="features" className="wrap section">
          <h2>Built for the apps that call it</h2>
          <div className="features">
            {FEATURES.map((f) => (
              <div className="tile" key={f.title}>
                {f.svg}
                <h3>{f.title}</h3>
                <p>{f.text}</p>
              </div>
            ))}
          </div>
        </section>

        <div className="wrap">
          <section className="cta">
            <div>
              <h2>Point your SDK at one URL.</h2>
              <p>Create an account, make a key, and send a request in a couple of minutes.</p>
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <Link className="btn bright lg" href={user ? "/overview" : "/signup"}>{user ? "Open dashboard" : "Create an account"}</Link>
              {user ? null : <Link className="btn ghost-dark lg" href="/login">Sign in</Link>}
            </div>
          </section>
        </div>
      </main>

      <footer className="site-footer">
        <div className="wrap">
          <span>{APP_NAME}: an OpenAI-compatible router with traces and evals.</span>
          <nav aria-label="Footer">
            <a className="site-link" href="#quickstart">Quickstart</a>
            <a className="site-link" href="#features">Features</a>
            <a className="site-link" href="https://github.com" target="_blank" rel="noreferrer">GitHub</a>
            {user ? null : <Link className="site-link" href="/login">Sign in</Link>}
          </nav>
        </div>
      </footer>
    </>
  );
}
