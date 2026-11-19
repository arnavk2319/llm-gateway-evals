from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .admins import AdminAllowlist

DATA_DIR = Path(__file__).parent / "data"
GATEWAY_DIR = Path(__file__).resolve().parents[1]
# Absolute so the gateway finds its env files regardless of the process's cwd.
ENV_FILES = (str(GATEWAY_DIR.parent / ".env"), str(GATEWAY_DIR / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=ENV_FILES,
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL", "GATEWAY_DATABASE_URL")
    )
    policy_path: Path = DATA_DIR / "seed_policy.yaml"
    policy_ttl_s: float = 30.0
    # "key1:name1,key2:name2". Empty means auth is disabled (local dev only).
    api_keys: str = ""
    sink: Literal["auto", "memory", "postgres", "null"] = "auto"
    auto_migrate: bool = True
    allow_mock_control: bool = True
    # Tests only: keep mock control when auth is required (never set this in a deployment).
    mock_control_with_auth: bool = False
    mock_latency_scale: float = 0.05
    debug_endpoints: bool = True
    # Write events before the response is returned. Serverless platforms may freeze the
    # process once the response is sent, which would drop post-response writes. auto = on Vercel.
    emit_inline: Literal["auto", "on", "off"] = "auto"
    max_request_bytes: int = 1_048_576  # larger request bodies are rejected with 413
    idempotency_ttl_s: int = 300

    # --- authentication
    # auto: required when keys/JWT secret are configured or when running on Vercel.
    auth: Literal["auto", "required", "off"] = "auto"
    jwt_secret: str | None = None  # enables short-lived HS256 tokens minted by your backend
    jwt_issuer: str | None = None
    default_rate_limit_per_min: int = 0  # 0 = unlimited unless a key sets its own
    auth_cache_ttl_s: float = 60.0  # how long a verified key is cached (bounds revocation lag)

    # --- dashboard API (/dashboard/v1). The dashboard has no database access: it calls these routes
    # with this shared secret. Unset means the routes are not mounted at all. See ADR 0001.
    dashboard_token: str | None = None
    key_max_per_user: int = 10  # active API keys one user may hold
    key_default_rpm: int = 60  # rate limit given to a key that does not ask for one
    key_max_rpm: int = 120  # the most a user may ask for
    # Admins: HMAC-SHA256 digests of the admin addresses plus the secret key that makes them (see app/admins.py).
    # Both unset means nobody is an admin. Make a digest with `scripts/admin.py hash <email>`.
    admin_hash_key: str | None = None
    admin_email_hashes: str | None = None

    # --- observability
    sentry_dsn: str | None = Field(
        default=None, validation_alias=AliasChoices("SENTRY_DSN", "GATEWAY_SENTRY_DSN")
    )
    sentry_traces_sample_rate: float = 0.0
    environment: str = Field(
        default="local", validation_alias=AliasChoices("VERCEL_ENV", "GATEWAY_ENVIRONMENT")
    )

    @property
    def emit_events_inline(self) -> bool:
        if self.emit_inline == "auto":
            return bool(os.environ.get("VERCEL"))
        return self.emit_inline == "on"

    @property
    def mock_control_enabled(self) -> bool:
        """Callers may steer the mock provider with a header, but never on an authenticated gateway."""
        return self.allow_mock_control and (self.mock_control_with_auth or not self.auth_required)

    @property
    def debug_enabled(self) -> bool:
        """/internal/* endpoints expose the policy and raw events: local development only."""
        return self.debug_endpoints and not self.auth_required

    @property
    def admins(self) -> AdminAllowlist:
        return AdminAllowlist(self.admin_hash_key, self.admin_email_hashes)

    @property
    def dashboard_api_enabled(self) -> bool:
        """A short token is as good as none, so the dashboard API stays off until it is long enough."""
        return bool(self.dashboard_token) and len(self.dashboard_token or "") >= 32

    @property
    def auth_required(self) -> bool:
        if self.auth == "required":
            return True
        if self.auth == "off":
            return False
        hosted = os.environ.get("VERCEL") or os.environ.get("RENDER")  # a public deployment never runs open
        return bool(self.api_keys or self.jwt_secret or hosted)

    @property
    def api_key_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in self.api_keys.split(","):
            part = part.strip()
            if not part:
                continue
            key, _, name = part.partition(":")
            out[key.strip()] = name.strip() or "default"
        return out
