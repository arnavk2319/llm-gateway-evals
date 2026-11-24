"use client";

import { type ReactNode, useState } from "react";

type Lang = "py" | "js" | "sh";

const TABS: { key: Lang; label: string; file: string }[] = [
  { key: "py", label: "Python", file: "app.py" },
  { key: "js", label: "Node", file: "app.mjs" },
  { key: "sh", label: "curl", file: "terminal" },
];

const K = ({ children }: { children: ReactNode }) => <span className="k">{children}</span>;
const S = ({ children }: { children: ReactNode }) => <span className="s">{children}</span>;
const C = ({ children }: { children: ReactNode }) => <span className="c">{children}</span>;

/** Quickstart snippets as one control: heading text and the code box share the tab state. */
export function QuickstartTabs() {
  const [lang, setLang] = useState<Lang>("py");
  const file = TABS.find((t) => t.key === lang)?.file;

  return (
    <div className="quick">
      <div>
        <h2>Two lines change</h2>
        <p style={{ margin: "16px 0 0", color: "var(--ink2)", maxWidth: 420 }}>
          Create a key in the dashboard, then point your client at the gateway. Streaming, tools and JSON mode keep working.
        </p>
        <div className="tabs" role="tablist" aria-label="Language">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={lang === t.key} className={`tab ${lang === t.key ? "on" : ""}`} onClick={() => setLang(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="codebox">
        <div className="bar">
          <i />
          <i />
          <i />
          <span style={{ marginLeft: 8 }}>{file}</span>
        </div>
        {lang === "py" ? (
          <pre>
            <K>from</K> openai <K>import</K> OpenAI{"\n\n"}client = OpenAI({"\n"}    base_url=<S>&quot;https://your-gateway.example.com/v1&quot;</S>,{"\n"}    api_key=<S>&quot;gw_live_...&quot;</S>,{"\n"}){"\n\n"}reply = client.chat.completions.create({"\n"}    model=<S>&quot;auto&quot;</S>,   <C># let the router choose</C>
            {"\n"}    messages=[{"{"}<S>&quot;role&quot;</S>: <S>&quot;user&quot;</S>, <S>&quot;content&quot;</S>: <S>&quot;Summarize this diff&quot;</S>{"}"}],{"\n"}    extra_headers={"{"}<S>&quot;x-node-role&quot;</S>: <S>&quot;summarizer&quot;</S>{"}"},{"\n"})
          </pre>
        ) : null}
        {lang === "js" ? (
          <pre>
            <K>import</K> OpenAI <K>from</K> <S>&quot;openai&quot;</S>;{"\n\n"}
            <K>const</K> client = <K>new</K> OpenAI({"{"}{"\n"}  baseURL: <S>&quot;https://your-gateway.example.com/v1&quot;</S>,{"\n"}  apiKey: process.env.GATEWAY_KEY,{"\n"}{"}"});{"\n\n"}
            <K>const</K> reply = <K>await</K> client.chat.completions.create({"\n"}  {"{"} model: <S>&quot;auto&quot;</S>, messages: [{"{"} role: <S>&quot;user&quot;</S>, content: <S>&quot;Summarize this diff&quot;</S> {"}"}] {"}"},{"\n"}  {"{"} headers: {"{"} <S>&quot;x-node-role&quot;</S>: <S>&quot;summarizer&quot;</S> {"}"} {"}"},{"\n"});
          </pre>
        ) : null}
        {lang === "sh" ? (
          <pre>
            curl https://your-gateway.example.com/v1/chat/completions \{"\n"}  -H <S>&quot;Authorization: Bearer $GATEWAY_KEY&quot;</S> \{"\n"}  -H <S>&quot;x-node-role: summarizer&quot;</S> \{"\n"}  -H <S>&quot;Content-Type: application/json&quot;</S> \{"\n"}  -d <S>{`'{"model":"auto","messages":[{"role":"user","content":"Summarize this diff"}]}'`}</S>
          </pre>
        ) : null}
      </div>
    </div>
  );
}
