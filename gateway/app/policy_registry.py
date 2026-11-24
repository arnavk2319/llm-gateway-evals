"""The routing-policy registry: what each published version is, where it came from, and CRUD over it.

A policy *body* is immutable once published, because routing decisions are explained by (policy version,
profile, route reason) in the logs and that explanation must stay true. What can change afterwards is the
metadata (notes and external references), and which version is active (promote or roll back). Only
drafts can be deleted; a version that has served traffic stays so spans keep pointing at something real.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .policy import Policy

# Versions published by these sources may be replaced automatically when the policy file changes.
# Anything else (the API, a future control plane) is a deliberate choice that a deploy must not undo.
AUTO_SOURCES = ("seed", "push_policy", "file_sync")

ADVISORY_LOCK = 727276  # shared with PostgresSink.save_policy

COLUMNS = (
    "version, status, created_by, source, created_at, activated_at, parent_version, "
    "coalesce(model_count, jsonb_array_length(body->'models')) AS model_count, app_version, release, "
    "git_branch, notes, external_refs, content_hash"
)


def fingerprint(policy: Policy) -> str:
    """Hash of the routing content only: the version number and status are bookkeeping, not routing."""
    body = policy.model_dump(mode="json", exclude={"version", "status"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def app_version() -> str:
    root = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        return str(tomllib.loads(root.read_text())["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def release_info() -> dict[str, str | None]:
    return {
        "app_version": app_version(),
        "release": os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("VERCEL_GIT_COMMIT_SHA"),
        "git_branch": os.environ.get("RENDER_GIT_BRANCH") or os.environ.get("VERCEL_GIT_COMMIT_REF"),
    }


def publish_meta(
    policy: Policy, source: str, notes: str | None = None, external_refs: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "content_hash": fingerprint(policy),
        "model_count": len(policy.models),
        "source": source,
        "notes": notes,
        "external_refs": external_refs or {},
        **release_info(),
    }


def _iso(v: Any) -> str | None:
    return None if v is None else v.isoformat().replace("+00:00", "Z")


def shape(row: dict[str, Any], with_body: bool = False) -> dict[str, Any]:
    out = {
        "version": row["version"],
        "status": row["status"],
        "createdBy": row["created_by"],
        "source": row["source"],
        "createdAt": _iso(row["created_at"]),
        "activatedAt": _iso(row["activated_at"]),
        "parentVersion": row["parent_version"],
        "modelCount": row["model_count"],
        "appVersion": row["app_version"],
        "release": row["release"],
        "gitBranch": row["git_branch"],
        "notes": row["notes"],
        "externalRefs": row["external_refs"] or {},
        "contentHash": row["content_hash"],
    }
    if with_body:
        out["body"] = row["body"]
    return out


class PolicyRegistry:
    """SQL for the policy versions. Publishing goes through sink.save_policy so there is one insert path."""

    def __init__(self, sink: Any) -> None:
        self._sink = sink
        self._pool = sink.pool

    async def _fetch(self, query: sql.Composable, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params)
            return list(await cur.fetchall())

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        q = sql.SQL("SELECT {} FROM routing_policy ORDER BY version DESC LIMIT %s").format(sql.SQL(COLUMNS))
        return [shape(r) for r in await self._fetch(q, (limit,))]

    async def get(self, version: int, with_body: bool = True) -> dict[str, Any] | None:
        cols = sql.SQL(COLUMNS + (", body" if with_body else ""))
        rows = await self._fetch(sql.SQL("SELECT {} FROM routing_policy WHERE version = %s").format(cols), (version,))
        return shape(rows[0], with_body) if rows else None

    async def active(self, with_body: bool = True) -> dict[str, Any] | None:
        cols = sql.SQL(COLUMNS + (", body" if with_body else ""))
        rows = await self._fetch(sql.SQL("SELECT {} FROM routing_policy WHERE status = 'active'").format(cols), ())
        return shape(rows[0], with_body) if rows else None

    async def create(
        self, policy: Policy, *, activate: bool, notes: str | None, external_refs: dict[str, str]
    ) -> dict[str, Any]:
        parent = await self.active(with_body=False)
        version = await self._sink.save_policy(
            policy.model_dump(mode="json"),
            status="active" if activate else "draft",
            created_by="api",
            parent_version=parent["version"] if parent else None,
            meta=publish_meta(policy, "api", notes, external_refs),
        )
        created = await self.get(version)
        assert created is not None
        return created

    async def update_meta(
        self, version: int, *, notes: str | None, set_notes: bool, external_refs: dict[str, str] | None
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE routing_policy SET notes = CASE WHEN %s THEN %s ELSE notes END, "
                "external_refs = coalesce(%s, external_refs) WHERE version = %s",
                (set_notes, notes, None if external_refs is None else Jsonb(external_refs), version),
            )
            if cur.rowcount == 0:
                return None
        return await self.get(version)

    async def activate(self, version: int) -> Literal["ok", "not_found"]:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
            cur = await conn.execute("SELECT status FROM routing_policy WHERE version = %s", (version,))
            row = await cur.fetchone()
            if row is None:
                return "not_found"
            if row[0] != "active":
                await conn.execute("UPDATE routing_policy SET status = 'retired' WHERE status = 'active'")
                await conn.execute(
                    "UPDATE routing_policy SET status = 'active', activated_at = now() WHERE version = %s", (version,)
                )
        return "ok"

    async def delete(self, version: int) -> Literal["ok", "not_found", "not_deletable"]:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute("DELETE FROM routing_policy WHERE version = %s AND status = 'draft'", (version,))
            if cur.rowcount:
                return "ok"
            cur = await conn.execute("SELECT 1 FROM routing_policy WHERE version = %s", (version,))
            return "not_deletable" if await cur.fetchone() else "not_found"
