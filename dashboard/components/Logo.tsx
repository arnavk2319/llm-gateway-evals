/** The routing mark: one input, a router, two outputs (the chosen route and the fallback). */
export function Logo({ size = 28, tile = "#1b2e2a" }: { size?: number; tile?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="7" fill={tile} />
      <circle cx="8" cy="14" r="2.6" fill="#4cc2ba" />
      <path d="M10.6 14h4.4c2 0 2.6-5 5-5" stroke="#4cc2ba" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <path d="M15 14c2 0 2.6 5 5 5" stroke="#4cc2ba" strokeWidth="1.8" fill="none" strokeLinecap="round" opacity=".45" />
      <circle cx="21" cy="9" r="1.9" fill="#f28c28" />
      <circle cx="21" cy="19" r="1.9" fill="#4cc2ba" opacity=".55" />
    </svg>
  );
}

export const APP_NAME = "Gateway";
