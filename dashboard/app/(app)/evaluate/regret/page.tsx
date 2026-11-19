import type { Metadata } from "next";
import { ComingSoon } from "@/components/ui";

export const metadata: Metadata = { title: "Cost regret" };

export default function RegretPage() {
  return (
    <ComingSoon
      title="Cost regret"
      blurb="Where the router paid more than it needed to, or saved money at a quality cost."
      points={[
        "Compares each call's chosen model with the best alternative the policy knew about.",
        "Shows the dollars you would have saved, by role and task.",
        "Needs quality scores first, so it arrives with them.",
      ]}
    />
  );
}
