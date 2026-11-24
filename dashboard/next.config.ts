import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // The old flat pages moved into nested menus; keep bookmarks and emailed links working.
  async redirects() {
    return [
      { source: "/logs", destination: "/traffic/logs", permanent: false },
      { source: "/logs/run/:runId", destination: "/traffic/runs/:runId", permanent: false },
      { source: "/keys", destination: "/access/keys", permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Cache-Control", value: "private, no-store" },
        ],
      },
    ];
  },
};

export default nextConfig;
