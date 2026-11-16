import { requireAdmin } from "@/lib/auth";
import { ago, int } from "@/lib/format";
import { listUsers } from "@/lib/users";
import { ToggleUser } from "./Toggle";

export const dynamic = "force-dynamic";

export default async function UsersPage() {
  await requireAdmin();
  let users;
  try {
    users = await listUsers();
  } catch (e) {
    return (
      <main>
        <h1>Users</h1>
        <div className="empty">{e instanceof Error ? e.message : String(e)}</div>
      </main>
    );
  }
  return (
    <main>
      <header className="top">
        <div>
          <h1>Users</h1>
          <span className="muted">Disabling a user stops their dashboard session and every API key they own.</span>
        </div>
      </header>
      <div className="tablewrap">
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
              <tr key={u.id}>
                <td>{u.email}</td>
                <td>{u.role}</td>
                <td>{new Date(u.createdAt).toLocaleDateString()}</td>
                <td>{u.lastLoginAt ? ago(u.lastLoginAt) : "never"}</td>
                <td className="n">{int(u.activeKeys)}</td>
                <td className="n">{int(u.calls24h)}</td>
                <td className={u.disabled ? "bad" : "ok"}>{u.disabled ? "disabled" : "active"}</td>
                <td>{u.role === "admin" ? null : <ToggleUser id={u.id} disabled={u.disabled} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
