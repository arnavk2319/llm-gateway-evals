import type { Metadata } from "next";
import { ComingSoon } from "@/components/ui";

export const metadata: Metadata = { title: "Quality scores" };

export default function QualityPage() {
  return (
    <ComingSoon
      title="Quality scores"
      blurb="Scores for how good each model's answers are, by role and task."
      points={[
        "Evaluators score a sample of real calls and golden-set runs.",
        "Scores sit next to cost and latency so you can see what a cheaper model gives up.",
        "They feed the policy proposals that the router tests before going live.",
      ]}
    />
  );
}
