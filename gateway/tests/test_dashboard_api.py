"""The dashboard's only route to data: /dashboard/v1 (ADR 0001).

The first group needs no database. The second runs the account, key, scoping and token rules against a
real Postgres and is skipped unless TEST_DATABASE_URL is set (CI sets it):

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql://gateway:gateway@localhost:5433/gateway uv run pytest tests/test_dashboard_api.py
"""

from __future__ import annotations

import hashlib
import os
import secrets

import pytest
from conftest import make_gateway

from app.auth import hash_key
from app.sinks import PostgresSink

TOKEN = "t" * 40
AUTH = {"authorization": f"Bearer {TOKEN}"}
FAKE_HASH = "scrypt$" + "x" * 40  # the dashboard hashes passwords; the gateway only stores the string
PG = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


def th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- no database needed -----------------------------------------------------------------------------


async def test_routes_do_not_exist_without_a_token():
    async with make_gateway() as (c, _):
        assert (await c.get("/dashboard/v1/analytics", headers=AUTH)).status_code == 404


async def test_a_short_token_does_not_enable_the_routes():
    async with make_gateway(dashboard_token="short") as (c, _):
        assert (await c.get("/dashboard/v1/analytics", headers={"authorization": "Bearer short"})).status_code == 404


async def test_requests_without_the_right_token_are_refused():
    async with make_gateway(dashboard_token=TOKEN) as (c, _):
        for headers in ({}, {"authorization": "Bearer nope"}, {"authorization": TOKEN}, {"authorization": "Bearer "}):
            r = await c.get("/dashboard/v1/analytics", headers=headers)
            assert r.status_code == 401, headers
        # An ordinary gateway API key is not a dashboard token.
        r = await c.get("/dashboard/v1/users", headers={"authorization": "Bearer gw_live_" + "A" * 43})
        assert r.status_code == 401


async def test_without_postgres_the_api_says_so():
    async with make_gateway(dashboard_token=TOKEN) as (c, _):
        r = await c.get("/dashboard/v1/analytics", headers=AUTH)
        assert r.status_code == 503


# ---- against a real Postgres --------------------------------------------------------------------------


@pytest.fixture
async def pg():
    dsn = os.environ["TEST_DATABASE_URL"]
    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    tag = secrets.token_hex(3)
    # make_gateway closes the sink when it exits, so the cleanup runs before that, inside it.
    async with make_gateway(
        sink_obj=sink, sink="postgres", database_url=dsn, dashboard_token=TOKEN,
        key_max_per_user=3, key_default_rpm=60, key_max_rpm=120,
    ) as (c, _):
        try:
            yield c, sink, tag
        finally:
            like = f"%-{tag}@example.com"
            async with sink.pool.connection() as conn:
                await conn.execute("DELETE FROM log_events WHERE message LIKE %s", (f"%{tag}%",))
                await conn.execute("DELETE FROM spans WHERE span_id LIKE %s", (f"span_{tag}%",))
                await conn.execute("DELETE FROM runs WHERE run_id LIKE %s", (f"run_{tag}%",))
                await conn.execute(
                    "DELETE FROM auth_tokens WHERE user_id IN (SELECT id FROM users WHERE email LIKE %s)", (like,))
                await conn.execute(
                    "DELETE FROM api_keys WHERE user_id IN (SELECT id FROM users WHERE email LIKE %s)", (like,))
                await conn.execute("DELETE FROM users WHERE email LIKE %s", (like,))


async def signup(c, tag, name, role="user", verified=False, password_hash=FAKE_HASH):
    r = await c.post(
        "/dashboard/v1/users",
        json={"email": f"{name}-{tag}@example.com", "passwordHash": password_hash, "role": role, "verified": verified},
        headers=AUTH,
    )
    return r


@PG
async def test_signup_is_unique_per_email_ignoring_case(pg):
    c, _, tag = pg
    a = await signup(c, tag, "alice")
    assert a.status_code == 201 and a.json()["verified"] is False
    dup = await c.post(
        "/dashboard/v1/users",
        json={"email": f"ALICE-{tag}@example.com", "passwordHash": FAKE_HASH}, headers=AUTH,
    )
    assert dup.status_code == 409
    found = await c.post("/dashboard/v1/users/lookup", json={"email": f"Alice-{tag}@Example.com"}, headers=AUTH)
    body = found.json()
    assert body["id"] == a.json()["id"] and body["passwordHash"] == FAKE_HASH
    assert body["locked"] is False and body["disabled"] is False
    assert (await c.post("/dashboard/v1/users/lookup", json={"email": "nobody@example.com"}, headers=AUTH)).json() is None
    assert (await signup(c, tag, "dave", role="admin", verified=True)).json()["verified"] is True


@PG
async def test_five_wrong_passwords_lock_and_a_success_clears(pg):
    c, _, tag = pg
    uid = (await signup(c, tag, "bob")).json()["id"]
    email = {"email": f"bob-{tag}@example.com"}
    for _ in range(5):
        await c.post(f"/dashboard/v1/users/{uid}/login-failure", headers=AUTH)
    assert (await c.post("/dashboard/v1/users/lookup", json=email, headers=AUTH)).json()["locked"] is True
    await c.post(f"/dashboard/v1/users/{uid}/login-success", headers=AUTH)
    assert (await c.post("/dashboard/v1/users/lookup", json=email, headers=AUTH)).json()["locked"] is False


@PG
async def test_keys_are_created_once_capped_listed_without_secret_and_revoked_by_the_owner_only(pg):
    c, sink, tag = pg
    alice = (await signup(c, tag, "alice", verified=True)).json()["id"]
    bob = (await signup(c, tag, "bob", verified=True)).json()["id"]
    body = {"name": "app", "expiresDays": 30, "models": ["mock/small", "mock/small"], "rpm": 50}
    made = []
    for _ in range(3):
        r = await c.post(f"/dashboard/v1/users/{alice}/keys", json=body, headers=AUTH)
        assert r.status_code == 201
        made.append(r.json())
    capped = await c.post(f"/dashboard/v1/users/{alice}/keys", json=body, headers=AUTH)
    assert capped.status_code == 409 and "at most 3" in capped.json()["detail"]
    assert (await c.post(f"/dashboard/v1/users/{bob}/keys", json={"name": "b"}, headers=AUTH)).status_code == 201

    # What the gateway authenticates with: the hash of exactly the key the user was shown.
    async with sink.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT key_hash, user_id, allowed_models, rate_limit_per_min, key_prefix FROM api_keys WHERE id = %s",
            (made[0]["id"],),
        )
        row = await cur.fetchone()
    assert row == (hash_key(made[0]["key"]), alice, ["mock/small"], 50, made[0]["key"][:12])
    assert made[0]["key"].startswith("gw_live_") and len(made[0]["key"]) == 51

    listed = (await c.get(f"/dashboard/v1/users/{alice}/keys", headers=AUTH)).json()
    assert len(listed) == 3 and made[0]["key"] not in str(listed)
    assert len((await c.get(f"/dashboard/v1/users/{bob}/keys", headers=AUTH)).json()) == 1
    # A key created without an rpm gets the default.
    assert (await c.get(f"/dashboard/v1/users/{bob}/keys", headers=AUTH)).json()[0]["rpm"] == 60

    assert (await c.post(f"/dashboard/v1/users/{bob}/keys/{made[0]['id']}/revoke", headers=AUTH)).status_code == 404
    assert (await c.post(f"/dashboard/v1/users/{alice}/keys/{made[0]['id']}/revoke", headers=AUTH)).status_code == 200
    assert (await c.post(f"/dashboard/v1/users/{alice}/keys", json=body, headers=AUTH)).status_code == 201  # freed slot


@PG
async def test_only_an_active_confirmed_user_can_hold_keys(pg):
    c, _, tag = pg
    new = (await signup(c, tag, "carol")).json()["id"]  # signed up, email not confirmed
    r = await c.post(f"/dashboard/v1/users/{new}/keys", json={"name": "x"}, headers=AUTH)
    assert r.status_code == 403 and "Confirm your email" in r.json()["detail"]
    assert (await c.get(f"/dashboard/v1/users/{new}/keys", headers=AUTH)).json() == []

    ok = (await signup(c, tag, "dora", verified=True)).json()["id"]
    assert (await c.post(f"/dashboard/v1/users/{ok}/keys", json={"name": "x"}, headers=AUTH)).status_code == 201
    await c.post(f"/dashboard/v1/users/{ok}/disabled", json={"disabled": True}, headers=AUTH)
    assert (await c.post(f"/dashboard/v1/users/{ok}/keys", json={"name": "y"}, headers=AUTH)).status_code == 404
    ghost = "usr_" + "0" * 12
    assert (await c.post(f"/dashboard/v1/users/{ghost}/keys", json={"name": "x"}, headers=AUTH)).status_code == 404


@PG
async def test_key_limits_are_published_for_the_form(pg):
    c, _, _ = pg
    r = await c.get("/dashboard/v1/key-limits", headers=AUTH)
    assert r.json() == {"maxPerUser": 3, "defaultRpm": 60, "maxRpm": 120}


@PG
async def test_a_key_may_not_ask_for_more_than_the_rate_limit_ceiling(pg):
    c, _, tag = pg
    uid = (await signup(c, tag, "alice", verified=True)).json()["id"]
    r = await c.post(f"/dashboard/v1/users/{uid}/keys", json={"name": "x", "rpm": 121}, headers=AUTH)
    assert r.status_code == 400 and "1-120" in r.json()["detail"]


@PG
async def test_keys_made_here_authenticate_against_the_gateway(pg):
    c, _, tag = pg
    dsn = os.environ["TEST_DATABASE_URL"]
    uid = (await signup(c, tag, "alice", verified=True)).json()["id"]
    key = (await c.post(f"/dashboard/v1/users/{uid}/keys", json={"name": "x"}, headers=AUTH)).json()["key"]
    chat = {"messages": [{"role": "user", "content": "hi"}]}

    async def call() -> int:
        # A fresh gateway each time: its key cache starts empty, so it sees the database as it is now.
        sink = PostgresSink(dsn)
        await sink.open(migrate=False)
        async with make_gateway(auth="required", sink_obj=sink, sink="postgres", database_url=dsn) as (g, _):
            r = await g.post("/v1/chat/completions", json=chat, headers={"authorization": f"Bearer {key}"})
            return r.status_code

    assert await call() == 200
    assert (await c.post(f"/dashboard/v1/users/{uid}/disabled", json={"disabled": True}, headers=AUTH)).status_code == 200
    assert await call() == 401  # a disabled owner's keys stop working


@PG
async def test_disabling_a_user_hides_them_and_admins_cannot_be_disabled(pg):
    c, _, tag = pg
    uid = (await signup(c, tag, "bob")).json()["id"]
    assert (await c.post(f"/dashboard/v1/users/{uid}/disabled", json={"disabled": True}, headers=AUTH)).status_code == 200
    assert (await c.get(f"/dashboard/v1/users/{uid}", headers=AUTH)).status_code == 404
    await c.post(f"/dashboard/v1/users/{uid}/disabled", json={"disabled": False}, headers=AUTH)
    assert (await c.get(f"/dashboard/v1/users/{uid}", headers=AUTH)).status_code == 200
    boss = (await signup(c, tag, "boss", role="admin")).json()["id"]
    assert (await c.post(f"/dashboard/v1/users/{boss}/disabled", json={"disabled": True}, headers=AUTH)).status_code == 404


@PG
async def test_email_links_are_single_use_expiring_and_rate_limited(pg):
    c, sink, tag = pg
    carol = (await signup(c, tag, "carol")).json()["id"]
    tok = secrets.token_urlsafe(32)

    async def issue(purpose, token):
        return await c.post(f"/dashboard/v1/users/{carol}/tokens", json={"purpose": purpose, "tokenHash": th(token)}, headers=AUTH)

    assert (await issue("verify", tok)).status_code == 201
    assert (await issue("verify", secrets.token_urlsafe(32))).status_code == 429  # one a minute
    assert (await issue("reset", secrets.token_urlsafe(32))).status_code == 201  # purposes counted separately

    async def usable(purpose, token):
        return (await c.post("/dashboard/v1/tokens/usable", json={"purpose": purpose, "tokenHash": th(token)}, headers=AUTH)).json()["usable"]

    assert await usable("verify", tok) is True
    assert await usable("reset", tok) is False  # a verify link can't reset a password
    verify = lambda t: c.post("/dashboard/v1/tokens/verify-email", json={"tokenHash": th(t)}, headers=AUTH)  # noqa: E731
    assert (await verify("nope")).json() == {"ok": False}
    assert (await verify(tok)).json() == {"ok": True}
    assert (await verify(tok)).json() == {"ok": False}  # single use
    assert (await c.get(f"/dashboard/v1/users/{carol}", headers=AUTH)).json()["verified"] is True

    old = secrets.token_urlsafe(32)
    async with sink.pool.connection() as conn:
        await conn.execute("UPDATE auth_tokens SET created_at = now() - interval '5 minutes' WHERE user_id = %s", (carol,))
    assert (await issue("verify", old)).status_code == 201
    async with sink.pool.connection() as conn:
        await conn.execute("UPDATE auth_tokens SET expires_at = now() - interval '1 minute' WHERE token_hash = %s", (th(old),))
    assert (await verify(old)).json() == {"ok": False}  # expired


@PG
async def test_the_hourly_cap_stops_inbox_spam(pg):
    c, sink, tag = pg
    erin = (await signup(c, tag, "erin")).json()["id"]
    for _ in range(5):
        async with sink.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO auth_tokens (id, user_id, purpose, token_hash, expires_at, created_at) "
                "VALUES (%s, %s, 'reset', %s, now() + interval '1 hour', now() - interval '10 minutes')",
                ("tok_" + secrets.token_hex(8), erin, th(secrets.token_urlsafe(8))),
            )
    r = await c.post(f"/dashboard/v1/users/{erin}/tokens", json={"purpose": "reset", "tokenHash": th("z")}, headers=AUTH)
    assert r.status_code == 429


@PG
async def test_password_reset_is_single_use_clears_lockout_and_voids_old_sessions(pg):
    c, _, tag = pg
    fay = (await signup(c, tag, "fay")).json()["id"]
    for _ in range(5):
        await c.post(f"/dashboard/v1/users/{fay}/login-failure", headers=AUTH)
    before = (await c.get(f"/dashboard/v1/users/{fay}", headers=AUTH)).json()["passwordChangedAt"]
    a, b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for token in (a, b):
        async with pg[1].pool.connection() as conn:  # bypass the per-minute cooldown to plant two links
            await conn.execute(
                "INSERT INTO auth_tokens (id, user_id, purpose, token_hash, expires_at) "
                "VALUES (%s, %s, 'reset', %s, now() + interval '1 hour')",
                ("tok_" + secrets.token_hex(8), fay, th(token)),
            )
    new = "scrypt$" + "n" * 40

    def reset(t, h=new):
        return c.post("/dashboard/v1/tokens/reset-password", json={"tokenHash": th(t), "passwordHash": h}, headers=AUTH)

    assert (await reset("nope")).json() == {"ok": False}
    assert (await reset(a)).json() == {"ok": True}
    assert (await reset(a)).json() == {"ok": False}  # single use
    assert (await reset(b)).json() == {"ok": False}  # sibling links die too
    creds = (await c.post("/dashboard/v1/users/lookup", json={"email": f"fay-{tag}@example.com"}, headers=AUTH)).json()
    assert creds["locked"] is False and creds["verified"] is True and creds["passwordHash"] == new
    assert (await c.get(f"/dashboard/v1/users/{fay}", headers=AUTH)).json()["passwordChangedAt"] > before


@PG
async def test_changing_your_own_password_moves_the_session_cutoff(pg):
    c, _, tag = pg
    gus = (await signup(c, tag, "gus")).json()["id"]
    t0 = (await c.get(f"/dashboard/v1/users/{gus}", headers=AUTH)).json()["passwordChangedAt"]
    new = "scrypt$" + "g" * 40
    assert (await c.post(f"/dashboard/v1/users/{gus}/password", json={"passwordHash": new}, headers=AUTH)).status_code == 200
    assert (await c.get(f"/dashboard/v1/users/{gus}", headers=AUTH)).json()["passwordChangedAt"] > t0
    assert (await c.get(f"/dashboard/v1/users/{gus}/password-hash", headers=AUTH)).json() == {"passwordHash": new}


@PG
async def test_each_user_sees_only_their_own_calls_and_logs_an_admin_sees_all(pg):
    c, sink, tag = pg
    alice = (await signup(c, tag, "alice")).json()["id"]
    bob = (await signup(c, tag, "bob")).json()["id"]
    async with sink.pool.connection() as conn:
        for who, uid in (("a", alice), ("b", bob)):
            run = f"run_{tag}_{who}"
            await conn.execute("INSERT INTO runs (run_id, api_key_name, user_id) VALUES (%s, 'app', %s)", (run, uid))
            await conn.execute(
                "INSERT INTO spans (span_id, run_id, status, model, origin, user_id, node_timings, created_at) "
                "VALUES (%s, %s, 'ok', %s, 'external', %s, '{\"call\": 5}', now())",
                (f"span_{tag}{who}", run, f"model-{who}-{tag}", uid),
            )
            await conn.execute(
                "INSERT INTO log_events (source, kind, severity, message, run_id, user_id, tags) "
                "VALUES ('gateway', 'attempt_failed', 'warn', %s, %s, %s, '{}')", (f"boom {who}{tag}", run, uid))
        await conn.execute(
            "INSERT INTO log_events (source, kind, severity, message, tags) VALUES ('vercel', 'runtime_log', 'error', %s, '{}')",
            (f"infra {tag}",),
        )

    flt = {"hours": 1, "q": tag}

    async def search(user):
        r = await c.post("/dashboard/v1/logs/search", json={"filter": flt, "user": user}, headers=AUTH)
        assert r.status_code == 200, r.text
        return r.json()

    mine = await search(alice)
    assert mine and all(r["runId"] == f"run_{tag}_a" for r in mine), "alice sees only her rows"
    assert not any(r["source"] == "vercel" for r in mine), "infrastructure logs are not shown to users"
    everyone = await search(None)
    assert {r["source"] for r in everyone} >= {"vercel", "gateway"}
    assert {r["runId"] for r in everyone} >= {f"run_{tag}_a", f"run_{tag}_b"}

    async def count(user):
        return (await c.post("/dashboard/v1/logs/count", json={"filter": flt, "user": user}, headers=AUTH)).json()["count"]

    assert await count(alice) < await count(None)

    facets = (await c.post("/dashboard/v1/logs/facets", json={"filter": flt, "user": alice}, headers=AUTH)).json()
    assert [f["value"] for f in facets["sources"]] == ["gateway"]

    own = (await c.get(f"/dashboard/v1/runs/run_{tag}_a?user={alice}", headers=AUTH)).json()
    assert len(own["spans"]) == 1
    foreign = (await c.get(f"/dashboard/v1/runs/run_{tag}_b?user={alice}", headers=AUTH)).json()
    assert foreign == {"spans": [], "attempts": [], "events": []}, "no peeking at another user's run"
    assert len((await c.get(f"/dashboard/v1/runs/run_{tag}_b", headers=AUTH)).json()["spans"]) == 1

    dash_a = (await c.get(f"/dashboard/v1/analytics?hours=1&user={alice}", headers=AUTH)).json()
    assert [m["key"] for m in dash_a["byModel"]] == [f"model-a-{tag}"] and dash_a["kpis"]["requests"] == 1
    dash_all = (await c.get("/dashboard/v1/analytics?hours=1", headers=AUTH)).json()
    assert f"model-b-{tag}" in [m["key"] for m in dash_all["byModel"]]
    # The chart series: one-minute bars for a one-hour window, scoped like everything else.
    assert dash_a["bucket"] == "minute" and sum(p["requests"] for p in dash_a["series"]) == 1
    assert (await c.get(f"/dashboard/v1/analytics?hours=24&user={alice}", headers=AUTH)).json()["bucket"] == "hour"
    assert (await c.get("/dashboard/v1/analytics?hours=720", headers=AUTH)).json()["bucket"] == "day"

    exported = (await c.get(f"/dashboard/v1/exports/spans?hours=1&user={alice}", headers=AUTH)).json()
    assert [r["span_id"] for r in exported] == [f"span_{tag}a"]
    assert exported[0]["created_at"].endswith("Z")

    users = (await c.get("/dashboard/v1/users", headers=AUTH)).json()
    assert {u["id"] for u in users} >= {alice, bob}
    assert next(u for u in users if u["id"] == alice)["calls24h"] == 1


@PG
async def test_log_search_filters_by_tag_severity_and_escapes_wildcards(pg):
    c, sink, tag = pg
    uid = (await signup(c, tag, "alice")).json()["id"]
    async with sink.pool.connection() as conn:
        for sev, msg, tags in (
            ("error", f"fail 100% {tag}", '{"model": "m1"}'),
            ("info", f"fine 1000 {tag}", '{"model": "m2"}'),
        ):
            await conn.execute(
                "INSERT INTO log_events (source, kind, severity, message, user_id, tags) VALUES ('gateway', 'x', %s, %s, %s, %s::jsonb)",
                (sev, msg, uid, tags),
            )

    async def search(**f):
        r = await c.post("/dashboard/v1/logs/search", json={"filter": {"hours": 1, **f}, "user": uid}, headers=AUTH)
        assert r.status_code == 200, r.text
        return [x["message"] for x in r.json()]

    assert len(await search(q=tag)) == 2
    assert await search(q=tag, minSeverity="error") == [f"fail 100% {tag}"]
    assert await search(q=tag, tags=[{"key": "model", "value": "m2"}]) == [f"fine 1000 {tag}"]
    assert await search(q=f"100% {tag}") == [f"fail 100% {tag}"]  # a literal %, not a wildcard
    assert await search(q="%") != []  # matches only rows that really contain a percent sign
    assert all("%" in m for m in await search(q="%"))


@PG
async def test_inputs_are_validated_before_anything_runs(pg):
    c, _, _ = pg
    bad_id = await c.get("/dashboard/v1/users/not-a-user/keys", headers=AUTH)
    assert bad_id.status_code == 422
    assert (await c.get("/dashboard/v1/analytics?hours=0", headers=AUTH)).status_code == 422
    assert (await c.get("/dashboard/v1/analytics?user=usr_x", headers=AUTH)).status_code == 422
    body = {"filter": {"hours": 24, "sources": ["a"] * 11}}
    assert (await c.post("/dashboard/v1/logs/search", json=body, headers=AUTH)).status_code == 422
    extra = {"email": "a@b.co", "passwordHash": FAKE_HASH, "role": "user", "isAdmin": True}
    assert (await c.post("/dashboard/v1/users", json=extra, headers=AUTH)).status_code == 422
    bad_role = {"email": "a@b.co", "passwordHash": FAKE_HASH, "role": "root"}
    assert (await c.post("/dashboard/v1/users", json=bad_role, headers=AUTH)).status_code == 422


