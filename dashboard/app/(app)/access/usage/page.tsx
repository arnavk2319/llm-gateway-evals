import type { Metadata } from "next";
import { ComingSoon } from "@/components/ui";

export const metadata: Metadata = { title: "Usage by key" };

export default function UsagePage() {
  return (
    <ComingSoon
      title="Usage by key"
      blurb="Requests, errors and cost for each API key."
      points={[
        "See which app or teammate is behind a spike.",
        "Compare each key's usage with its rate limit.",
        "Spot keys that have not been used and can be revoked.",
      ]}
    />
  );
}
