"""The dashboard's only way to data: /dashboard/v1 (see docs-internal/adr/0001).

Mounted only when GATEWAY_DASHBOARD_TOKEN is set (32+ characters). Every route needs that token as
a bearer credential, compared in constant time, and all of them answer 503 when the gateway has no
Postgres. The dashboard names *whose* data it wants (a user id, or none for the admin view); the SQL
and the scoping filter live in dashboard_store.py.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .dashboard_store import SEVERITIES, DashboardStore
from .policy import Policy
from .policy_registry import PolicyRegistry

log = logging.getLogger("gateway.dashboard")

USER_ID = r"^usr_[a-f0-9]{12}$"
KEY_ID = r"^key_[a-f0-9]{12}$"
HASH = r"^[a-f0-9]{64}$"

UserId = Annotated[str, Field(pattern=USER_ID)]
Version = Annotated[int, Path(ge=1)]
Hours = Annotated[int, Query(ge=1, le=720)]
Scope = Annotated[str | None, Query(alias="user", pattern=USER_ID, description="omit for the admin (all users) view")]


def _error(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


class _Body(BaseModel):
    model_config = {"extra": "forbid"}


class Lookup(_Body):
    email: str = Field(min_length=3, max_length=254)


class NewUser(_Body):
    email: str = Field(min_length=3, max_length=254)
    passwordHash: str = Field(min_length=20, max_length=300)
    # True when the caller checked a valid admin invite code. Whether the address may be an admin is the
    # gateway's decision (app/admins.py), never the caller's.
    adminInvite: bool = False
    verified: bool = False
    role: Literal["user", "admin"] | None = Field(default=None, deprecated=True)  # ignored: old dashboards still send it


class Disabled(_Body):
    disabled: bool


class PasswordHash(_Body):
    passwordHash: str = Field(min_length=20, max_length=300)


class IssueToken(_Body):
    purpose: Literal["verify", "reset"]
    tokenHash: str = Field(pattern=HASH)


class TokenCheck(_Body):
    purpose: Literal["verify", "reset"]
    tokenHash: str = Field(pattern=HASH)


class TokenOnly(_Body):
    tokenHash: str = Field(pattern=HASH)


class ResetPassword(_Body):
    tokenHash: str = Field(pattern=HASH)
    passwordHash: str = Field(min_length=20, max_length=300)


class NewKey(_Body):
    name: str = Field(pattern=r"^[A-Za-z0-9 _.\-]{1,60}$")
    expiresDays: int | None = Field(default=None, ge=1, le=365)
    models: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:/-]{1,100}$")]] = Field(default_factory=list, max_length=20)
    rpm: int | None = Field(default=None, ge=1)


class Tag(_Body):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=200)


class LogFilter(_Body):
    hours: int = Field(default=24, ge=1, le=720)
    sources: list[str] = Field(default_factory=list, max_length=10)
    minSeverity: Literal["debug", "info", "warn", "error", "critical"] | None = None
    kinds: list[str] = Field(default_factory=list, max_length=20)
    q: str = Field(default="", max_length=200)
    run: str = Field(default="", max_length=100)
    span: str = Field(default="", max_length=100)
    tags: list[Tag] = Field(default_factory=list, max_length=8)
    page: int = Field(default=1, ge=1, le=1000)

    def as_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        assert self.minSeverity is None or self.minSeverity in SEVERITIES
        return d


class LogQuery(_Body):
    filter: LogFilter
    user: str | None = Field(default=None, pattern=USER_ID)
    limit: int = Field(default=100, ge=1, le=50_000)  # exports ask for a lot


REF_KEY = r"^[A-Za-z0-9_.\-]{1,40}$"
ExternalRefs = Annotated[dict[Annotated[str, Field(pattern=REF_KEY)], Annotated[str, Field(max_length=200)]], Field(max_length=20)]


class NewPolicy(_Body):
    policy: dict[str, Any]
    activate: bool = False  # a new version is a draft unless this says otherwise
    notes: str | None = Field(default=None, max_length=1000)
    externalRefs: ExternalRefs = Field(default_factory=dict)


class PolicyMeta(_Body):
    notes: str | None = Field(default=None, max_length=1000)
    externalRefs: ExternalRefs | None = None  # replaces the whole map when present


def build_router(settings: Settings) -> APIRouter:
    token = (settings.dashboard_token or "").encode()

    def _sink(request: Request) -> Any:
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip().encode() if header[:7].lower() == "bearer " else b""
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="unauthorized", headers={"www-authenticate": "Bearer"})
        sink = request.app.state.rt.sink
        if getattr(sink, "pool", None) is None:
            raise _error(503, "the dashboard API needs the Postgres sink")
        return sink

    async def authorize(request: Request) -> DashboardStore:
        return DashboardStore(_sink(request).pool, settings.admins)

    async def registry(request: Request) -> PolicyRegistry:
        return PolicyRegistry(_sink(request))

    router = APIRouter(prefix="/dashboard/v1", tags=["dashboard"])

    def ok(data: Any, status: int = 200) -> JSONResponse:
        return JSONResponse(data, status_code=status, headers={"cache-control": "no-store"})

    # ---- accounts ---------------------------------------------------------------------------------

    @router.post("/users/lookup")
    async def lookup(body: Lookup, store: DashboardStore = Depends(authorize)):
        return ok(await store.find_by_email(body.email.strip().lower()))

    @router.post("/users")
    async def create_user(body: NewUser, store: DashboardStore = Depends(authorize)):
        user = await store.create_user(body.email.strip().lower(), body.passwordHash, body.adminInvite, body.verified)
        if user is None:
            raise _error(409, "email already registered")
        return ok(user, 201)

    @router.get("/users")
    async def list_users(store: DashboardStore = Depends(authorize)):
        return ok(await store.list_users())

    @router.get("/users/{user_id}")
    async def active_user(user_id: Annotated[str, Field(pattern=USER_ID)], store: DashboardStore = Depends(authorize)):
        user = await store.find_active_by_id(user_id)
        if user is None:
            raise _error(404, "no such active user")
        return ok(user)

    @router.post("/users/{user_id}/login-failure")
    async def login_failure(user_id: Annotated[str, Field(pattern=USER_ID)], store: DashboardStore = Depends(authorize)):
        await store.record_failure(user_id)
        return ok({"ok": True})

    @router.post("/users/{user_id}/login-success")
    async def login_success(user_id: Annotated[str, Field(pattern=USER_ID)], store: DashboardStore = Depends(authorize)):
        await store.record_success(user_id)
        return ok({"ok": True})

    @router.post("/users/{user_id}/disabled")
    async def set_disabled(user_id: Annotated[str, Field(pattern=USER_ID)], body: Disabled, store: DashboardStore = Depends(authorize)):
        if not await store.set_disabled(user_id, body.disabled):
            raise _error(404, "no such user, or the user is an admin")
        return ok({"ok": True})

    @router.get("/users/{user_id}/password-hash")
    async def password_hash(user_id: Annotated[str, Field(pattern=USER_ID)], store: DashboardStore = Depends(authorize)):
        value = await store.password_hash_of(user_id)
        if value is None:
            raise _error(404, "no such user")
        return ok({"passwordHash": value})

    @router.post("/users/{user_id}/password")
    async def change_password(user_id: Annotated[str, Field(pattern=USER_ID)], body: PasswordHash, store: DashboardStore = Depends(authorize)):
        if not await store.change_password(user_id, body.passwordHash):
            raise _error(404, "no such user")
        return ok({"ok": True})

    # ---- emailed links ----------------------------------------------------------------------------

    @router.post("/users/{user_id}/tokens")
    async def issue_token(user_id: Annotated[str, Field(pattern=USER_ID)], body: IssueToken, store: DashboardStore = Depends(authorize)):
        if not await store.issue_token(user_id, body.purpose, body.tokenHash):
            raise _error(429, "too many links requested; try again later")
        return ok({"ok": True}, 201)

    @router.post("/tokens/usable")
    async def token_usable(body: TokenCheck, store: DashboardStore = Depends(authorize)):
        return ok({"usable": await store.token_usable(body.purpose, body.tokenHash)})

    @router.post("/tokens/verify-email")
    async def verify_email(body: TokenOnly, store: DashboardStore = Depends(authorize)):
        return ok({"ok": await store.verify_email(body.tokenHash)})

    @router.post("/tokens/reset-password")
    async def reset_password(body: ResetPassword, store: DashboardStore = Depends(authorize)):
        return ok({"ok": await store.reset_password(body.tokenHash, body.passwordHash)})

    # ---- API keys ---------------------------------------------------------------------------------

    @router.get("/users/{user_id}/keys")
    async def list_keys(user_id: Annotated[str, Field(pattern=USER_ID)], store: DashboardStore = Depends(authorize)):
        return ok(await store.list_keys(user_id))

    @router.post("/users/{user_id}/keys")
    async def create_key(user_id: Annotated[str, Field(pattern=USER_ID)], body: NewKey, store: DashboardStore = Depends(authorize)):
        rpm = body.rpm if body.rpm is not None else min(settings.key_default_rpm, settings.key_max_rpm)
        if rpm > settings.key_max_rpm:
            raise _error(400, f"Rate limit must be 1-{settings.key_max_rpm} requests per minute")
        created = await store.create_key(
            user_id, body.name, body.expiresDays, sorted(set(body.models)), rpm, settings.key_max_per_user
        )
        if created == "unknown_user":
            raise _error(404, "no such active user")
        if created == "unverified":
            raise _error(403, "Confirm your email address first.")
        if created == "limit":
            raise _error(409, f"You can have at most {settings.key_max_per_user} active keys. Revoke one first.")
        return ok(created, 201)

    @router.post("/users/{user_id}/keys/{key_id}/revoke")
    async def revoke_key(
        user_id: Annotated[str, Field(pattern=USER_ID)], key_id: Annotated[str, Field(pattern=KEY_ID)], store: DashboardStore = Depends(authorize)
    ):
        if not await store.revoke_key(user_id, key_id):
            raise _error(404, "no such key")
        return ok({"ok": True})

    @router.get("/key-limits")
    async def key_limits(store: DashboardStore = Depends(authorize)):
        return ok({
            "maxPerUser": settings.key_max_per_user,
            "defaultRpm": min(settings.key_default_rpm, settings.key_max_rpm),
            "maxRpm": settings.key_max_rpm,
        })

    # ---- routing policy registry --------------------------------------------------------------------

    @router.get("/policies")
    async def list_policies(reg: PolicyRegistry = Depends(registry), limit: Annotated[int, Query(ge=1, le=500)] = 100):
        return ok(await reg.list(limit))

    @router.get("/policies/active")
    async def active_policy(reg: PolicyRegistry = Depends(registry)):
        found = await reg.active()
        if found is None:
            raise _error(404, "no active policy")
        return ok(found)

    @router.get("/policies/{version}")
    async def get_policy(version: Version, reg: PolicyRegistry = Depends(registry), body: bool = True):
        found = await reg.get(version, body)
        if found is None:
            raise _error(404, "no such policy version")
        return ok(found)

    @router.post("/policies")
    async def create_policy(body: NewPolicy, reg: PolicyRegistry = Depends(registry)):
        try:
            policy = Policy.model_validate(body.policy)
        except ValidationError as e:
            first = e.errors()[0]
            raise _error(422, f"invalid policy: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from None
        unknown = sorted({m.provider for m in policy.models} - set(policy.providers))
        if not policy.models or unknown:
            raise _error(422, "invalid policy: needs at least one model, each with a provider listed in `providers`"
                         + (f" (unknown: {', '.join(unknown)})" if unknown else ""))
        return ok(await reg.create(policy, activate=body.activate, notes=body.notes, external_refs=body.externalRefs), 201)

    @router.patch("/policies/{version}")
    async def update_policy(version: Version, body: PolicyMeta, reg: PolicyRegistry = Depends(registry)):
        updated = await reg.update_meta(
            version, notes=body.notes, set_notes="notes" in body.model_fields_set, external_refs=body.externalRefs
        )
        if updated is None:
            raise _error(404, "no such policy version")
        return ok(updated)

    @router.post("/policies/{version}/activate")
    async def activate_policy(version: Version, reg: PolicyRegistry = Depends(registry)):
        if await reg.activate(version) == "not_found":
            raise _error(404, "no such policy version")
        return ok(await reg.get(version, False))

    @router.delete("/policies/{version}")
    async def delete_policy(version: Version, reg: PolicyRegistry = Depends(registry)):
        outcome = await reg.delete(version)
        if outcome == "not_found":
            raise _error(404, "no such policy version")
        if outcome == "not_deletable":
            raise _error(409, "only drafts can be deleted; a published version stays for the audit trail")
        return ok({"ok": True})

    # ---- analytics and logs -----------------------------------------------------------------------

    @router.get("/analytics")
    async def analytics(store: DashboardStore = Depends(authorize), hours: Hours = 24, user: Scope = None):
        return ok(await store.analytics(hours, user))

    @router.post("/logs/search")
    async def search_logs(body: LogQuery, store: DashboardStore = Depends(authorize)):
        return ok(await store.search_logs(body.filter.as_dict(), body.user, body.limit))

    @router.post("/logs/count")
    async def count_logs(body: LogQuery, store: DashboardStore = Depends(authorize)):
        return ok({"count": await store.count_logs(body.filter.as_dict(), body.user, 10_000)})

    @router.post("/logs/facets")
    async def log_facets(body: LogQuery, store: DashboardStore = Depends(authorize)):
        return ok(await store.log_facets(body.filter.as_dict(), body.user))

    @router.get("/runs/{run_id}")
    async def get_run(run_id: Annotated[str, Field(min_length=1, max_length=100)], store: DashboardStore = Depends(authorize), user: Scope = None):
        return ok(await store.get_run(run_id, user))

    @router.get("/exports/spans")
    async def export_spans(store: DashboardStore = Depends(authorize), hours: Hours = 24, user: Scope = None):
        return ok(await store.export_spans(hours, user))

    return router
