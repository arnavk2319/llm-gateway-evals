"""Who may be an admin: a keyed-hash allowlist, enforced by the gateway.

The first group needs no database. The second needs Postgres and is skipped unless TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import importlib.util
import os
import secrets
from pathlib import Path

import psycopg
import pytest
from conftest import make_gateway

from app.admins import MIN_KEY_LENGTH, AdminAllowlist, admin_hash
from app.sinks import PostgresSink

KEY = "k" * 40
TOKEN = "t" * 40
AUTH = {"authorization": f"Bearer {TOKEN}"}
PASSWORD_HASH = "scrypt$" + "x" * 40
PG = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


def allow(*emails: str, key: str = KEY) -> AdminAllowlist:
    return AdminAllowlist(key, ",".join(admin_hash(e, key) for e in emails))


# ---- no database needed -----------------------------------------------------------------------------


def test_the_digest_is_a_keyed_hmac_that_ignores_case_and_spaces():
    assert admin_hash("Boss@Example.com ", KEY) == admin_hash("boss@example.com", KEY)
    assert admin_hash("boss@example.com", KEY) != admin_hash("boss@example.com", "j" * 40)  # a different key gives a different digest
    assert len(admin_hash("boss@example.com", KEY)) == 64
    # Pinned (and equal to `openssl dgst -sha256 -hmac`) so a change of algorithm, which would lock every admin out, is deliberate.
    assert admin_hash("boss@example.com", KEY) == "88f2e2d5412e892bfa47fbb9b8d6f0692ebb9096ed08aba34f0c3f6a73bb2f8f"


def test_only_listed_addresses_are_allowed():
    a = allow("boss@example.com", "second@example.com")
    assert a.allows("BOSS@example.com") and a.allows("second@example.com")
    assert not a.allows("someone@example.com") and not a.allows("")


def test_without_a_key_or_digests_nobody_is_an_admin():
    digest = admin_hash("boss@example.com", KEY)
    for a in (AdminAllowlist(None, None), AdminAllowlist(KEY, None), AdminAllowlist(None, digest),
              AdminAllowlist("short", digest), AdminAllowlist(KEY, "not-a-digest, 123")):
        assert not a.enabled and not a.allows("boss@example.com")
    assert MIN_KEY_LENGTH == 32


def test_the_digest_cannot_be_made_with_a_short_key():
    with pytest.raises(ValueError, match="at least"):
        AdminAllowlist("short", None).hash("boss@example.com")


def test_a_stored_admin_is_only_an_admin_while_still_on_the_list():
    a = allow("boss@example.com")
    assert a.effective_role("admin", "boss@example.com") == "admin"
    assert a.effective_role("admin", "removed@example.com") == "user"  # taken off the list
    assert a.effective_role("user", "boss@example.com") == "user"  # the list never grants by itself
    assert AdminAllowlist(None, None).effective_role("admin", "boss@example.com") == "user"  # fail closed
    assert allow("boss@example.com", key="z" * 40).effective_role("admin", "boss@example.com") == "admin"
    rotated = AdminAllowlist("n" * 40, admin_hash("boss@example.com", KEY))  # key changed, old digests no longer match
    assert rotated.effective_role("admin", "boss@example.com") == "user"


# ---- needs Postgres ---------------------------------------------------------------------------------


@pytest.fixture
async def api():
    dsn = os.environ["TEST_DATABASE_URL"]
    tag = secrets.token_hex(3)
    boss, other = f"boss-{tag}@example.com", f"other-{tag}@example.com"
    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    async with make_gateway(
        sink_obj=sink, sink="postgres", database_url=dsn, dashboard_token=TOKEN,
        admin_hash_key=KEY, admin_email_hashes=admin_hash(boss, KEY),
    ) as (c, _):
        try:
            yield c, boss, other, dsn, sink
        finally:
            async with sink.pool.connection() as conn:
                await conn.execute("DELETE FROM users WHERE email LIKE %s", (f"%-{tag}@example.com",))


async def signup(c, email, **extra):
    return await c.post("/dashboard/v1/users", json={"email": email, "passwordHash": PASSWORD_HASH, **extra}, headers=AUTH)


@PG
async def test_only_an_invited_allowlisted_address_becomes_an_admin(api):
    c, boss, other, *_ = api
    made = (await signup(c, other, adminInvite=True)).json()
    assert made["role"] == "user" and made["verified"] is False  # an invite is not enough without the allowlist
    squatter = (await signup(c, boss, adminInvite=False)).json()
    assert squatter["role"] == "user"  # nor is the address without an invite


@PG
async def test_an_admin_starts_verified_and_the_caller_cannot_grant_the_role(api):
    c, boss, other, *_ = api
    # Old dashboards sent `role`; it is ignored.
    assert (await signup(c, other, role="admin")).json()["role"] == "user"
    admin = (await signup(c, boss, adminInvite=True)).json()
    assert admin["role"] == "admin" and admin["verified"] is True


@PG
async def test_taking_the_address_off_the_list_ends_admin_access_everywhere(api):
    c, boss, _, dsn, _ = api
    uid = (await signup(c, boss, adminInvite=True)).json()["id"]
    assert (await c.get(f"/dashboard/v1/users/{uid}", headers=AUTH)).json()["role"] == "admin"
    listed = (await c.get("/dashboard/v1/users", headers=AUTH)).json()
    assert next(u for u in listed if u["id"] == uid)["role"] == "admin"

    # The same database, but a gateway whose allowlist no longer has the address (or has no allowlist at all).
    for hashes in ("", admin_hash("someone-else@example.com", KEY)):
        sink = PostgresSink(dsn)  # make_gateway closes its sink on exit, so each gateway gets its own
        await sink.open(migrate=False)
        async with make_gateway(
            sink_obj=sink, sink="postgres", database_url=dsn, dashboard_token=TOKEN, admin_hash_key=KEY, admin_email_hashes=hashes,
        ) as (g, _):
            assert (await g.get(f"/dashboard/v1/users/{uid}", headers=AUTH)).json()["role"] == "user"
            found = (await g.post("/dashboard/v1/users/lookup", json={"email": boss}, headers=AUTH)).json()
            assert found["role"] == "user"
            listed = (await g.get("/dashboard/v1/users", headers=AUTH)).json()
            assert next(u for u in listed if u["id"] == uid)["role"] == "user"


def load_script():
    spec = importlib.util.spec_from_file_location("admin_script", Path(__file__).resolve().parents[1] / "scripts" / "admin.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@PG
async def test_the_script_promotes_only_allowlisted_active_confirmed_accounts(api):
    c, boss, other, dsn, _ = api
    script = load_script()
    await signup(c, boss)
    await signup(c, other)
    admins = allow(boss)
    with psycopg.connect(dsn) as conn:
        with pytest.raises(SystemExit, match="not on the admin allowlist"):
            script.promote(conn, admins, other, other)  # not allowlisted, however carefully confirmed
        with pytest.raises(SystemExit, match="no account"):
            script.promote(conn, allow("ghost@example.com"), "ghost@example.com", "ghost@example.com")
        with pytest.raises(SystemExit, match="confirmation"):
            script.promote(conn, admins, boss, None)
        with pytest.raises(SystemExit, match="confirmation"):
            script.promote(conn, admins, boss, "someone-else@example.com")
        with pytest.raises(SystemExit, match="Set GATEWAY_ADMIN"):
            script.promote(conn, AdminAllowlist(None, None), boss, boss)
        assert script.promote(conn, admins, boss, boss).startswith("promoted")
        assert script.promote(conn, admins, boss, boss) == "already an admin"
        row = conn.execute("SELECT role, email_verified_at IS NOT NULL FROM users WHERE email = %s", (boss,)).fetchone()
        assert row == ("admin", True)
        assert (boss, True, False) in script.listing(conn, admins)

        conn.execute("UPDATE users SET disabled = true WHERE email = %s", (boss,))
        conn.execute("UPDATE users SET role = 'user' WHERE email = %s", (boss,))
        conn.commit()
        with pytest.raises(SystemExit, match="disabled"):
            script.promote(conn, admins, boss, boss)


@PG
async def test_the_script_will_not_demote_the_last_admin_without_being_told(api):
    c, boss, _, dsn, _ = api
    script = load_script()
    await signup(c, boss, adminInvite=True)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE users SET role = 'user' WHERE role = 'admin' AND email <> %s", (boss,))
        conn.commit()
        with pytest.raises(SystemExit, match="confirmation"):
            script.demote(conn, boss, None, False)
        with pytest.raises(SystemExit, match="last admin"):
            script.demote(conn, boss, boss, False)
        assert script.demote(conn, boss, boss, True) == "demoted"
        assert script.demote(conn, boss, boss, True) == "not an admin"
