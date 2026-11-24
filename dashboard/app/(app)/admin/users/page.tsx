import type { Metadata } from "next";
import { LoadError, PageHead } from "@/components/ui";
import { requireAdmin } from "@/lib/auth";
import { ago, int } from "@/lib/format";
import { listUsers } from "@/lib/users";
import { ToggleUser } from "./Toggle";

export const metadata: Metadata = { title: "Users" };
export const dynamic = "force-dynamic";

export default async function UsersPage() {
  await requireAdmin();
  let users;
  try {
    users = await listUsers();
  } catch (e) {
    return (
      <>
        <PageHead title="Users" />
        <LoadError title="Can't read users" error={e} />
      </>
    );
  }
  return (
    <>
      <PageHead title="Users" sub="Disabling a user stops their dashboard session and every API key they own." />
      <section className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Joined</th>
              <th>Last sign-in</th>
              <th className="n">Active keys</th>
              <th className="n">Calls (24h)</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} style={{ height: 44 }}>
                <td>{u.email}</td>
                <td>
                  <span className={`pill ${u.role === "admin" ? "warn" : "mute"}`}>{u.role}</span>
                </td>
                <td>{new Date(u.createdAt).toLocaleDateString()}</td>
                <td>{u.lastLoginAt ? ago(u.lastLoginAt) : "Never"}</td>
                <td className="n">{int(u.activeKeys)}</td>
                <td className="n">{int(u.calls24h)}</td>
                <td>
                  <span className={`pill ${u.disabled ? "bad" : "ok"}`}>{u.disabled ? "disabled" : "active"}</span>
                </td>
                <td className="n">{u.role === "admin" ? null : <ToggleUser id={u.id} disabled={u.disabled} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
