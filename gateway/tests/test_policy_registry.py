"""Policy versions: publishing on deploy when the file changes, and the registry API.

The first group needs no database. The second needs Postgres and is skipped unless TEST_DATABASE_URL is set:

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql://gateway:gateway@localhost:5433/gateway uv run pytest tests/test_policy_registry.py
"""

from __future__ import annotations

import copy
import os

import pytest
from conftest import make_gateway, write_policy
from psycopg_pool import PoolClosed

from app.config import DATA_DIR
from app.policy import Policy, PolicyStore, load_policy_file
from app.policy_registry import app_version, fingerprint, release_info
from app.sinks import PostgresSink

TOKEN = "t" * 40
AUTH = {"authorization": f"Bearer {TOKEN}"}
PG = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


# ---- no database needed -----------------------------------------------------------------------------


def test_the_fingerprint_ignores_bookkeeping_but_not_routing_content(seed_policy_dict):
    base = Policy.model_validate(seed_policy_dict)
    renumbered = Policy.model_validate({**seed_policy_dict, "version": 99, "status": "draft"})
    assert fingerprint(base) == fingerprint(renumbered)
    fewer = copy.deepcopy(seed_policy_dict)
    fewer["models"].pop()
    assert fingerprint(Policy.model_validate(fewer)) != fingerprint(base)


def test_release_information_reads_the_deploy_environment(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "dev")
    info = release_info()
    assert info["release"] == "abc123" and info["git_branch"] == "dev"
    assert app_version() != "unknown"


async def test_policy_routes_need_the_dashboard_token():
    async with make_gateway(dashboard_token=TOKEN) as (c, _):
        assert (await c.get("/dashboard/v1/policies")).status_code == 401
        # An in-memory gateway has no policy table to serve.
        assert (await c.get("/dashboard/v1/policies", headers=AUTH)).status_code == 503


# ---- needs Postgres ---------------------------------------------------------------------------------


@pytest.fixture
async def sink():
    s = PostgresSink(os.environ["TEST_DATABASE_URL"])
    await s.open(migrate=True)
    async with s.pool.connection() as conn:
        await conn.execute("TRUNCATE routing_policy RESTART IDENTITY")
    try:
        yield s
    finally:
        try:  # make_gateway closes the sink when it exits, which is before this runs
            async with s.pool.connection() as conn:
                await conn.execute("TRUNCATE routing_policy RESTART IDENTITY")
        except PoolClosed:
            pass
        await s.close()


async def start(sink, path):
    store = PolicyStore(path, 30.0, sink)
    await store.startup()
    return store


async def versions(sink):
    async with sink.pool.connection() as conn:
        cur = await conn.execute("SELECT version, status, created_by FROM routing_policy ORDER BY version")
        return await cur.fetchall()


@PG
async def test_the_first_start_seeds_the_file_with_its_metadata(sink):
    await start(sink, DATA_DIR / "seed_policy.yaml")
    assert await versions(sink) == [(1, "active", "seed")]
    async with sink.pool.connection() as conn:
        cur = await conn.execute("SELECT model_count, source, app_version, content_hash FROM routing_policy")
        count, source, version, digest = await cur.fetchone()
    assert count == len(load_policy_file(DATA_DIR / "seed_policy.yaml").models)
    assert source == "seed" and version == app_version() and len(digest) == 64


@PG
async def test_an_unchanged_file_publishes_nothing(sink):
    await start(sink, DATA_DIR / "seed_policy.yaml")
    await start(sink, DATA_DIR / "seed_policy.yaml")
    assert await versions(sink) == [(1, "active", "seed")]


@PG
async def test_a_changed_file_is_published_as_a_new_version_on_start(sink, seed_policy_dict, tmp_path):
    await start(sink, DATA_DIR / "seed_policy.yaml")
    edited = copy.deepcopy(seed_policy_dict)
    edited["models"].pop()
    store = await start(sink, write_policy(tmp_path, edited))
    assert await versions(sink) == [(1, "retired", "seed"), (2, "active", "file_sync")]
    assert len((await store.get()).models) == len(edited["models"])
    await start(sink, write_policy(tmp_path, edited))  # same file again: still two versions
    assert len(await versions(sink)) == 2


@PG
async def test_a_deliberately_published_version_is_not_replaced_by_a_deploy(sink, seed_policy_dict, tmp_path):
    await start(sink, DATA_DIR / "seed_policy.yaml")
    async with sink.pool.connection() as conn:
        await conn.execute("UPDATE routing_policy SET created_by = 'api'")
    edited = copy.deepcopy(seed_policy_dict)
    edited["models"].pop()
    await start(sink, write_policy(tmp_path, edited))
    assert await versions(sink) == [(1, "active", "api")]


@PG
async def test_two_instances_starting_together_publish_once(sink, seed_policy_dict, tmp_path):
    import asyncio

    await start(sink, DATA_DIR / "seed_policy.yaml")
    edited = copy.deepcopy(seed_policy_dict)
    edited["models"].pop()
    path = write_policy(tmp_path, edited)
    await asyncio.gather(*(start(sink, path) for _ in range(4)))
    assert [v[1] for v in await versions(sink)].count("active") == 1
    assert len(await versions(sink)) == 2


@pytest.fixture
async def api(sink):
    async with make_gateway(
        sink_obj=sink, sink="postgres", database_url=os.environ["TEST_DATABASE_URL"], dashboard_token=TOKEN
    ) as (c, _):
        try:
            yield c
        finally:
            async with sink.pool.connection() as conn:
                await conn.execute("TRUNCATE routing_policy RESTART IDENTITY")


@PG
async def test_the_registry_api_lists_creates_updates_activates_and_deletes(api, seed_policy_dict):
    active = (await api.get("/dashboard/v1/policies/active", headers=AUTH)).json()
    assert active["version"] == 1 and active["status"] == "active" and active["modelCount"] > 0
    assert "body" in active and active["source"] == "seed" and active["appVersion"] == app_version()

    edited = copy.deepcopy(seed_policy_dict)
    edited["models"].pop()
    r = await api.post(
        "/dashboard/v1/policies", headers=AUTH,
        json={"policy": edited, "notes": "drop one model", "externalRefs": {"ticket": "OPS-12"}},
    )
    assert r.status_code == 201
    draft = r.json()
    assert draft["status"] == "draft" and draft["createdBy"] == "api" and draft["modelCount"] == len(edited["models"])
    assert draft["externalRefs"] == {"ticket": "OPS-12"} and draft["parentVersion"] == 1

    listing = (await api.get("/dashboard/v1/policies", headers=AUTH)).json()
    assert [p["version"] for p in listing] == [2, 1] and all("body" not in p for p in listing)

    r = await api.patch(
        "/dashboard/v1/policies/2", headers=AUTH, json={"notes": "reviewed", "externalRefs": {"release": "v0.2.0"}}
    )
    assert r.json()["notes"] == "reviewed" and r.json()["externalRefs"] == {"release": "v0.2.0"}
    r = await api.patch("/dashboard/v1/policies/2", headers=AUTH, json={"externalRefs": {"a": "b"}})
    assert r.json()["notes"] == "reviewed"  # not sent: left alone

    assert (await api.delete("/dashboard/v1/policies/1", headers=AUTH)).status_code == 409  # active
    promoted = (await api.post("/dashboard/v1/policies/2/activate", headers=AUTH)).json()
    assert promoted["status"] == "active"
    assert (await api.get("/dashboard/v1/policies/1", headers=AUTH)).json()["status"] == "retired"
    assert (await api.delete("/dashboard/v1/policies/1", headers=AUTH)).status_code == 409  # retired stays
    rolled_back = (await api.post("/dashboard/v1/policies/1/activate", headers=AUTH)).json()
    assert rolled_back["status"] == "active"

    spare = (await api.post("/dashboard/v1/policies", headers=AUTH, json={"policy": edited})).json()
    assert (await api.delete(f"/dashboard/v1/policies/{spare['version']}", headers=AUTH)).status_code == 200
    assert (await api.get(f"/dashboard/v1/policies/{spare['version']}", headers=AUTH)).status_code == 404
    assert (await api.post("/dashboard/v1/policies/999/activate", headers=AUTH)).status_code == 404
    assert (await api.patch("/dashboard/v1/policies/999", headers=AUTH, json={"notes": "x"})).status_code == 404


@PG
async def test_the_registry_api_refuses_malformed_input(api, seed_policy_dict):
    post = api.post
    assert (await post("/dashboard/v1/policies", headers=AUTH, json={"policy": {"models": []}})).status_code == 422
    broken = copy.deepcopy(seed_policy_dict)
    broken["models"][0]["provider"] = "no-such-provider"
    assert (await post("/dashboard/v1/policies", headers=AUTH, json={"policy": broken})).status_code == 422
    empty = {**seed_policy_dict, "models": []}
    assert (await post("/dashboard/v1/policies", headers=AUTH, json={"policy": empty})).status_code == 422
    bad_ref = {"policy": seed_policy_dict, "externalRefs": {"has space": "x"}}
    assert (await post("/dashboard/v1/policies", headers=AUTH, json=bad_ref)).status_code == 422
    too_many = {"policy": seed_policy_dict, "externalRefs": {f"k{i}": "v" for i in range(21)}}
    assert (await post("/dashboard/v1/policies", headers=AUTH, json=too_many)).status_code == 422
    assert (await post("/dashboard/v1/policies", headers=AUTH, json={"policy": seed_policy_dict, "x": 1})).status_code == 422
    assert (await api.get("/dashboard/v1/policies/0", headers=AUTH)).status_code == 422
