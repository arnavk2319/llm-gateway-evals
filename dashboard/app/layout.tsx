import type { Metadata } from "next";
import { familjen, hanken, plex } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Gateway", template: "%s · Gateway" },
  description: "An OpenAI-compatible gateway that routes each call by role and task, with traces, cost and logs.",
};

// Light is the default theme. A saved choice is applied before first paint so there is no flash.
const THEME_SCRIPT = `try{var t=localStorage.getItem("gw-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${familjen.variable} ${hanken.variable} ${plex.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
