import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LLM Gateway",
  description: "Cost, latency, errors and routing for the LLM gateway",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
