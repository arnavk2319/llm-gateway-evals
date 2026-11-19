import type { Metadata } from "next";
import { GroupTable, LoadError, PageHead, RangeTabs } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { WINDOWS } from "@/lib/filters";
import { getDashboard } from "@/lib/queries";

export const metadata: Metadata = { title: "Models" };
export const dynamic = "force-dynamic";

export default async function ModelsPage({ searchParams }: { searchParams: Promise<{ hours?: string }> }) {
  const user = await requireUser();
  const { hours } = await searchParams;
  const h = WINDOWS.some((w) => w.h === Number(hours)) ? Number(hours) : 24;
  let data;
  try {
    data = await getDashboard(h, scopeOf(user));
  } catch (e) {
    return (
      <>
        <PageHead title="Models" />
        <LoadError title="Can't reach the gateway" error={e} />
      </>
    );
  }
  return (
    <>
      <PageHead title="Models" sub="How each model performed on the traffic the router sent it.">
        <RangeTabs path="/routing/models" hours={h} />
      </PageHead>
      <GroupTable title="By model" rows={data.byModel} keyLabel="Model" />
    </>
  );
}
