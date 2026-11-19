"""Manage who is an admin. An address can only become an admin if its digest is on the allowlist.

    export GATEWAY_ADMIN_HASH_KEY=$(openssl rand -hex 32)     # keep this secret; the gateway needs the same value
    uv run python scripts/admin.py hash you@example.com       # prints the digest to put in GATEWAY_ADMIN_EMAIL_HASHES

    DATABASE_URL=postgresql://... GATEWAY_ADMIN_HASH_KEY=... GATEWAY_ADMIN_EMAIL_HASHES=... \\
        uv run python scripts/admin.py promote you@example.com
    DATABASE_URL=...  uv run python scripts/admin.py demote you@example.com
    DATABASE_URL=...  uv run python scripts/admin.py list

`promote` refuses unless the address's digest is on the allowlist, the account exists and is active, and you
type the address back to confirm. `demote` refuses to remove the last admin unless --allow-last is given.
Removing a digest from GATEWAY_ADMIN_EMAIL_HASHES (or changing the key) also ends someone's admin access, with
no database change: an account is an admin only while its stored role and the allowlist agree (see app/admins.py).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.config import ENV_FILES

for _f in ENV_FILES:
    load_dotenv(_f)

import psycopg  # noqa: E402

from app.admins import AdminAllowlist, normalize  # noqa: E402


def allowlist() -> AdminAllowlist:
    return AdminAllowlist(os.environ.get("GATEWAY_ADMIN_HASH_KEY"), os.environ.get("GATEWAY_ADMIN_EMAIL_HASHES"))


def connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")
    return psycopg.connect(dsn)


def promote(conn: psycopg.Connection, admins: AdminAllowlist, email: str, confirm: str | None) -> str:
    email = normalize(email)
    if not admins.enabled:
        raise SystemExit("Set GATEWAY_ADMIN_HASH_KEY (32+ characters) and GATEWAY_ADMIN_EMAIL_HASHES first.")
    if not admins.allows(email):
        raise SystemExit("Refused: this address is not on the admin allowlist. Add its digest (`hash`) to GATEWAY_ADMIN_EMAIL_HASHES.")
    row = conn.execute("SELECT id, role, disabled FROM users WHERE lower(email) = %s", (email,)).fetchone()
    if row is None:
        raise SystemExit("Refused: no account with that address. They must sign up first.")
    if row[2]:
        raise SystemExit("Refused: that account is disabled.")
    if row[1] == "admin":
        return "already an admin"
    if confirm is None or normalize(confirm) != email:
        raise SystemExit("Refused: the confirmation did not match the address.")
    conn.execute(
        "UPDATE users SET role = 'admin', email_verified_at = coalesce(email_verified_at, now()) WHERE id = %s", (row[0],)
    )
    conn.commit()
    return "promoted (and marked verified, since you vouched for the address)"


def demote(conn: psycopg.Connection, email: str, confirm: str | None, allow_last: bool) -> str:
    email = normalize(email)
    row = conn.execute("SELECT id, role FROM users WHERE lower(email) = %s", (email,)).fetchone()
    if row is None:
        raise SystemExit("Refused: no account with that address.")
    if row[1] != "admin":
        return "not an admin"
    if confirm is None or normalize(confirm) != email:
        raise SystemExit("Refused: the confirmation did not match the address.")
    count = conn.execute("SELECT count(*) FROM users WHERE role = 'admin'").fetchone()
    if count is not None and count[0] <= 1 and not allow_last:
        raise SystemExit("Refused: that is the last admin. Pass --allow-last if you really mean it.")
    conn.execute("UPDATE users SET role = 'user' WHERE id = %s", (row[0],))
    conn.commit()
    return "demoted"


def listing(conn: psycopg.Connection, admins: AdminAllowlist) -> list[tuple[str, bool, bool]]:
    rows = conn.execute("SELECT email, disabled FROM users WHERE role = 'admin' ORDER BY created_at").fetchall()
    return [(r[0], admins.allows(r[0]), r[1]) for r in rows]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash").add_argument("email")
    sub.add_parser("promote").add_argument("email")
    d = sub.add_parser("demote")
    d.add_argument("email")
    d.add_argument("--allow-last", action="store_true")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    admins = allowlist()

    if args.cmd == "hash":
        try:
            print(admins.hash(args.email))
        except ValueError as e:
            raise SystemExit(str(e)) from None
        return
    with connect() as conn:
        if args.cmd == "list":
            for email, listed, disabled in listing(conn, admins):
                print(f"{email}  allowlisted={'yes' if listed else 'NO (not an admin until added)'}  disabled={disabled}")
            return
        confirm = input(f"Type the address again to confirm ({args.email}): ") if sys.stdin.isatty() else None
        if args.cmd == "promote":
            print(promote(conn, admins, args.email, confirm))
        else:
            print(demote(conn, args.email, confirm, args.allow_last))


if __name__ == "__main__":
    main()
