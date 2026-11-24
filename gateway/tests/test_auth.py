import time
from datetime import UTC, datetime, timedelta

import pytest
from conftest import chat, make_gateway, settle

from app.auth import AuthError, generate_key, hash_key, mint_jwt, verify_jwt

SECRET = "test-secret-at-least-32-bytes-long-xxxx"


async def add_key(sink, name="alpha", **kw):
    key = generate_key()
    await sink.create_api_key(
        {"id": f"key_{name}", "name": name, "key_prefix": key[:12], "key_hash": hash_key(key), **kw}
    )
    return key


def bearer(token):
    return {"authorization": f"Bearer {token}"}


async def post(c, token=None, **kw):
    return await c.post("/v1/chat/completions", json=chat(**kw), headers=bearer(token) if token else {})


# ---- API keys ----------------------------------------------------------------

async def test_valid_key_authenticates_and_is_attributed():
    async with make_gateway(auth="required") as (c, sink):
        key = await add_key(sink, "alpha")
        r = await post(c, key)
        assert r.status_code == 200
        assert sink.events[-1].api_key_name == "alpha"
        await settle()
        assert sink.api_keys[hash_key(key)]["last_used_at"] is not None


async def test_missing_and_unknown_credentials_get_401_with_challenge():
    async with make_gateway(auth="required") as (c, _):
        r = await post(c)
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Bearer")
        r = await post(c, "gw_live_nope")
        assert r.status_code == 401
        assert 'error="invalid_token"' in r.headers["www-authenticate"]
        assert r.json()["error"]["type"] == "invalid_token"


async def test_expired_and_revoked_keys_say_so():
    async with make_gateway(auth="required", auth_cache_ttl_s=0) as (c, sink):
        old = await add_key(sink, "old", expires_at=datetime.now(UTC) - timedelta(minutes=1))
        r = await post(c, old)
        assert r.status_code == 401 and 'error_description="expired"' in r.headers["www-authenticate"]

        gone = await add_key(sink, "gone", status="revoked")
        r = await post(c, gone)
        assert r.status_code == 401 and 'error_description="revoked"' in r.headers["www-authenticate"]


async def test_rotation_two_keys_work_until_the_old_one_is_revoked():
    async with make_gateway(auth="required", auth_cache_ttl_s=0) as (c, sink):
        old = await add_key(sink, "app")
        new = generate_key()
        await sink.create_api_key({"id": "key_app2", "name": "app", "key_prefix": new[:12],
                                   "key_hash": hash_key(new), "rotated_from": "key_app"})
        assert (await post(c, old)).status_code == 200
        assert (await post(c, new)).status_code == 200
        await sink.set_api_key_status("key_app", "revoked")
        assert (await post(c, old)).status_code == 401
        assert (await post(c, new)).status_code == 200


async def test_revocation_lag_is_bounded_by_the_cache_ttl():
    async with make_gateway(auth="required", auth_cache_ttl_s=3600) as (c, sink):
        key = await add_key(sink, "cached")
        assert (await post(c, key)).status_code == 200
        await sink.set_api_key_status("key_cached", "revoked")
        assert (await post(c, key)).status_code == 200  # still cached: documented trade-off


async def test_model_scope_restricts_pinning_and_routing():
    async with make_gateway(auth="required") as (c, sink):
        key = await add_key(sink, "scoped", allowed_models=["mock/large"])
        r = await post(c, key, model="mock/small")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_model"
        r = await post(c, key)  # auto routing must stay inside the scope
        assert r.status_code == 200 and r.headers["x-gateway-model"] == "mock/large"


async def test_per_key_rate_limit_returns_429_with_retry_after():
    async with make_gateway(auth="required") as (c, sink):
        key = await add_key(sink, "limited", rate_limit_per_min=2)
        assert (await post(c, key)).status_code == 200
        assert (await post(c, key)).status_code == 200
        r = await post(c, key)
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1
        assert r.json()["error"]["type"] == "rate_limited"


async def test_env_configured_keys_still_work():
    async with make_gateway(api_keys="plain-key:legacy") as (c, sink):
        assert (await post(c, "plain-key")).status_code == 200
        assert sink.events[-1].api_key_name == "legacy"
        assert (await post(c, "other")).status_code == 401


async def test_credential_store_outage_fails_closed_but_keeps_verified_keys():
    async with make_gateway(auth="required", auth_cache_ttl_s=0.0) as (c, sink):
        known = await add_key(sink, "known")
        assert (await post(c, known)).status_code == 200  # verified once, now in the cache

        async def down(_):
            raise RuntimeError("db down")

        sink.get_api_key = down  # type: ignore[method-assign]
        assert (await post(c, known)).status_code == 200  # stale entry still honored
        r = await post(c, generate_key())
        assert r.status_code == 503 and r.json()["error"]["type"] == "auth_unavailable"


async def test_auth_off_by_default_locally(gw):
    c, _ = gw
    assert (await post(c)).status_code == 200


async def test_vercel_environment_requires_auth_by_default(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    async with make_gateway() as (c, _):
        assert (await post(c)).status_code == 401


# ---- short-lived tokens (re-auth) -----------------------------------------------

async def test_jwt_roundtrip_and_reauth_after_expiry():
    async with make_gateway(jwt_secret=SECRET) as (c, sink):
        tok = mint_jwt(SECRET, "my-saas", ttl_s=900)
        assert (await post(c, tok)).status_code == 200
        assert sink.events[-1].api_key_name == "my-saas"

        stale = mint_jwt(SECRET, "my-saas", ttl_s=60, now=time.time() - 3600)
        r = await post(c, stale)
        assert r.status_code == 401
        assert 'error_description="expired"' in r.headers["www-authenticate"]
        # the client's re-auth step: mint a fresh token and retry
        assert (await post(c, mint_jwt(SECRET, "my-saas"))).status_code == 200
        assert [l.tags.get("error_class") for l in sink.logs] == ["token_expired"]


async def test_jwt_scopes_and_rate_limit_claims():
    async with make_gateway(jwt_secret=SECRET) as (c, _):
        tok = mint_jwt(SECRET, "scoped", models=["mock/medium"], rate_limit_per_min=1)
        r = await post(c, tok)
        assert r.status_code == 200 and r.headers["x-gateway-model"] == "mock/medium"
        assert (await post(c, tok)).status_code == 429


def test_jwt_rejects_tampering_none_alg_wrong_issuer_and_garbage():
    good = mint_jwt(SECRET, "a", issuer="me")
    verify_jwt(good, SECRET, issuer="me")

    head, body, sig = good.split(".")
    forged = f"{head}.{body[:-2]}xx.{sig}"
    for bad in (forged, good + "x", "abc", "a.b.c"):
        with pytest.raises(AuthError):
            verify_jwt(bad, SECRET)

    import base64
    import json

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    none_alg = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'a', 'exp': time.time() + 600})}."
    with pytest.raises(AuthError, match="unsupported algorithm"):
        verify_jwt(none_alg, SECRET)
    with pytest.raises(AuthError, match="wrong issuer"):
        verify_jwt(good, SECRET, issuer="someone-else")
    with pytest.raises(AuthError, match="bad signature"):
        verify_jwt(good, "a-different-secret")


async def test_jwt_is_not_accepted_when_no_secret_is_configured():
    async with make_gateway(api_keys="k:x") as (c, _):
        assert (await post(c, mint_jwt(SECRET, "a"))).status_code == 401
