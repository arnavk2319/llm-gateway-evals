"""Per-call authentication: API keys (hashed, rotatable, revocable) and short-lived JWTs.

Callers send `Authorization: Bearer <credential>`:

- `gw_live_...`  an API key. Only its SHA-256 hash is stored. Keys can expire, be revoked,
                 be scoped to a list of models, and carry a per-minute rate limit. Several
                 keys may be active for one caller at once, which is how rotation works.
- `eyJ...`       a short-lived HS256 token minted by your own backend (see mint_jwt). The
                 client re-authenticates by minting a new token when it gets
                 `401 invalid_token: expired`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Settings

log = logging.getLogger("gateway.auth")

KEY_PREFIX = "gw_live_"
_NEGATIVE_TTL_S = 10.0
_TOUCH_EVERY_S = 60.0


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    # API keys are high-entropy random strings, so a fast hash is appropriate.
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class AuthError(Exception):
    def __init__(
        self, status: int, error: str, description: str, *,
        headers: dict[str, str] | None = None, error_class: str = "unauthorized",
    ) -> None:
        super().__init__(description)
        self.status = status
        self.error = error
        self.description = description
        self.error_class = error_class
        self.headers = headers if headers is not None else self._default_headers()

    def _default_headers(self) -> dict[str, str]:
        if self.status != 401:
            return {}
        if self.error == "missing_token":
            return {"WWW-Authenticate": 'Bearer realm="llm-gateway"'}
        return {
            "WWW-Authenticate": (
                f'Bearer realm="llm-gateway", error="{self.error}", error_description="{self.description}"'
            )
        }


@dataclass
class Principal:
    name: str
    key_id: str | None
    auth_method: str  # api_key | env_key | jwt | none
    allowed_models: list[str] | None = None
    rate_limit_per_min: int | None = None
    user_id: str | None = None  # the account that owns the key (dashboard-created keys)


# ---- JWT (HS256, no external dependency) ------------------------------------

def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_jwt(
    secret: str, sub: str, *, ttl_s: int = 900, models: list[str] | None = None,
    rate_limit_per_min: int | None = None, issuer: str | None = None, now: float | None = None,
) -> str:
    """Mint a short-lived token. Run this in your backend, never in the browser."""
    issued = int(now if now is not None else time.time())
    claims: dict[str, Any] = {"sub": sub, "iat": issued, "exp": issued + ttl_s}
    if models:
        claims["models"] = list(models)
    if rate_limit_per_min:
        claims["rpm"] = rate_limit_per_min
    if issuer:
        claims["iss"] = issuer
    head = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"


def verify_jwt(
    token: str, secret: str, *, issuer: str | None = None, now: float | None = None, leeway_s: float = 5.0
) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError(401, "invalid_token", "malformed token")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        given = _b64url_decode(parts[2])
    except ValueError:
        raise AuthError(401, "invalid_token", "malformed token") from None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AuthError(401, "invalid_token", "unsupported algorithm")  # also rejects alg=none
    expected = hmac.new(secret.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, given):
        raise AuthError(401, "invalid_token", "bad signature")
    if not isinstance(claims, dict):
        raise AuthError(401, "invalid_token", "malformed token")
    t = time.time() if now is None else now
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise AuthError(401, "invalid_token", "missing expiry")
    if t > exp + leeway_s:
        raise AuthError(401, "invalid_token", "expired", error_class="token_expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and t + leeway_s < nbf:
        raise AuthError(401, "invalid_token", "not yet valid")
    if issuer and claims.get("iss") != issuer:
        raise AuthError(401, "invalid_token", "wrong issuer")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise AuthError(401, "invalid_token", "missing subject")
    return claims


# ---- authenticator ----------------------------------------------------------

class Authenticator:
    def __init__(self, settings: Settings, sink: Any) -> None:
        self._s = settings
        self._sink = sink
        # Keep only hashes of env-configured keys in memory.
        self._env_keys = {hash_key(k): name for k, name in settings.api_key_map.items()}
        self._cache: dict[str, tuple[float, dict[str, Any] | None, float]] = {}
        self._buckets: dict[str, tuple[float, float]] = {}
        self._quota: dict[str, int] = {}
        self._quota_day = ""
        self._touched: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def required(self) -> bool:
        return self._s.auth_required

    async def authenticate(self, authorization: str | None) -> Principal:
        if not self.required:
            return Principal("anonymous", None, "none")

        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            raise AuthError(401, "missing_token", "Authorization: Bearer <credential> is required")

        if token.startswith(KEY_PREFIX):
            principal = await self._api_key(token)
        elif token.count(".") == 2 and self._s.jwt_secret:
            claims = verify_jwt(token, self._s.jwt_secret, issuer=self._s.jwt_issuer)
            models = claims.get("models")
            rpm = claims.get("rpm")
            principal = Principal(
                claims["sub"], None, "jwt",
                allowed_models=models if isinstance(models, list) else None,
                rate_limit_per_min=rpm if isinstance(rpm, int) else None,
            )
        else:
            principal = self._env_key(token)  # keys from GATEWAY_API_KEYS may have any shape

        self._check_rate(principal)
        self._check_quota(principal)
        return principal

    def _env_key(self, token: str) -> Principal:
        h = hash_key(token)
        for env_hash, name in self._env_keys.items():
            if hmac.compare_digest(env_hash, h):
                return Principal(name, None, "env_key")
        raise AuthError(401, "invalid_token", "unknown credential")

    # -- API keys

    async def _api_key(self, token: str) -> Principal:
        h = hash_key(token)
        rec = await self._lookup(h)
        if rec is None:
            return self._env_key(token)
        if rec.get("status") != "active" or rec.get("user_disabled"):
            raise AuthError(401, "invalid_token", "revoked", error_class="key_revoked")
        exp = rec.get("expires_at")
        if exp is not None:
            if isinstance(exp, (int, float)):
                exp = datetime.fromtimestamp(exp, tz=UTC)
            if exp <= datetime.now(UTC):
                raise AuthError(401, "invalid_token", "expired", error_class="key_expired")
        self._touch(rec["id"])
        return Principal(
            rec["name"], rec["id"], "api_key",
            allowed_models=list(rec["allowed_models"]) if rec.get("allowed_models") else None,
            rate_limit_per_min=rec.get("rate_limit_per_min"),
            user_id=rec.get("user_id"),
        )

    async def _lookup(self, key_hash: str) -> dict[str, Any] | None:
        now = time.monotonic()
        hit = self._cache.get(key_hash)
        if hit and hit[0] > now:
            return hit[1]
        try:
            rec = await self._sink.get_api_key(key_hash)
        except Exception:
            # Database unreachable: keep honoring a previously verified key, fail closed otherwise.
            if hit and hit[1] is not None:
                return hit[1]
            raise AuthError(503, "auth_unavailable", "credential store unavailable",
                            headers={}, error_class="auth_unavailable") from None
        ttl = self._s.auth_cache_ttl_s if rec is not None else _NEGATIVE_TTL_S
        self._cache[key_hash] = (now + ttl, rec, now)
        if len(self._cache) > 5000:
            self._cache.clear()
        return rec

    def _touch(self, key_id: str) -> None:
        now = time.monotonic()
        if now - self._touched.get(key_id, -1e9) < _TOUCH_EVERY_S:
            return
        self._touched[key_id] = now
        task = asyncio.create_task(self._safe_touch(key_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_touch(self, key_id: str) -> None:
        try:
            await self._sink.touch_api_key(key_id)
        except Exception:  # last_used is best effort; never fail a request over it
            log.debug("touch_api_key failed", exc_info=True)

    # -- daily quota (per user, counted in this process)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _check_quota(self, p: Principal) -> None:
        """A user's requests per UTC day across all of their keys; unowned keys count on their own. A stopgap: the
        counts live in this process, so they reset on restart and are per instance (see the production tickets)."""
        limit = self._s.user_daily_requests
        if limit <= 0:
            return
        now = self._now()
        day = now.date().isoformat()
        if day != self._quota_day:
            self._quota_day, self._quota = day, {}
        ident = p.user_id or p.key_id or p.name
        used = self._quota.get(ident, 0)
        if used >= limit:
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wait = max(1, math.ceil((midnight - now).total_seconds()))
            raise AuthError(
                429, "quota_exceeded", f"Daily limit of {limit} requests reached. It resets at 00:00 UTC.",
                headers={"Retry-After": str(wait)}, error_class="quota_exceeded",
            )
        self._quota[ident] = used + 1

    # -- rate limiting (token bucket, per instance)

    def _check_rate(self, p: Principal) -> None:
        limit = p.rate_limit_per_min or self._s.default_rate_limit_per_min
        if not limit or limit <= 0:
            return
        ident = p.key_id or p.name
        now = time.monotonic()
        tokens, last = self._buckets.get(ident, (float(limit), now))
        rate = limit / 60.0
        tokens = min(float(limit), tokens + (now - last) * rate)
        if tokens < 1.0:
            wait = math.ceil((1.0 - tokens) / rate)
            raise AuthError(
                429, "rate_limited", f"Rate limit of {limit}/min exceeded",
                headers={"Retry-After": str(wait)}, error_class="rate_limited",
            )
        self._buckets[ident] = (tokens - 1.0, now)
