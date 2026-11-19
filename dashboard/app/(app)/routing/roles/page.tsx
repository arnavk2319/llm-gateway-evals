import type { Metadata } from "next";
import { GroupTable, LoadError, PageHead, RangeTabs } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { WINDOWS } from "@/lib/filters";
import { getDashboard } from "@/lib/queries";

export const metadata: Metadata = { title: "Roles and tasks" };
export const dynamic = "force-dynamic";

export default async function RolesPage({ searchParams }: { searchParams: Promise<{ hours?: string }> }) {
  const user = await requireUser();
  const { hours } = await searchParams;
  const h = WINDOWS.some((w) => w.h === Number(hours)) ? Number(hours) : 24;
  let data;
  try {
    data = await getDashboard(h, scopeOf(user));
  } catch (e) {
    return (
      <>
        <PageHead title="Roles and tasks" />
        <LoadError title="Can't reach the gateway" error={e} />
      </>
    );
  }
  return (
    <>
      <PageHead title="Roles and tasks" sub="Traffic by the x-node-role header and by the task the router detected.">
        <RangeTabs path="/routing/roles" hours={h} />
      </PageHead>
      <GroupTable title="By node role" rows={data.byRole} keyLabel="Role" />
      <div style={{ height: 14 }} />
      <GroupTable title="By task type" rows={data.byTask} keyLabel="Task" />
    </>
  );
}
