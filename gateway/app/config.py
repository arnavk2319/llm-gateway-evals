from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    mock_latency_scale: float = 0.05
    debug_endpoints: bool = True
    max_request_bytes: int = 1_048_576  # larger request bodies are rejected with 413
    idempotency_ttl_s: int = 300

    # --- authentication
    # auto: required when keys/JWT secret are configured or when running on Vercel.
    auth: Literal["auto", "required", "off"] = "auto"
    jwt_secret: str | None = None  # enables short-lived HS256 tokens minted by your backend
    jwt_issuer: str | None = None
    default_rate_limit_per_min: int = 0  # 0 = unlimited unless a key sets its own
    auth_cache_ttl_s: float = 60.0  # how long a verified key is cached (bounds revocation lag)

    # --- observability
    sentry_dsn: str | None = Field(
        default=None, validation_alias=AliasChoices("SENTRY_DSN", "GATEWAY_SENTRY_DSN")
    )
    sentry_traces_sample_rate: float = 0.0
    environment: str = Field(
        default="local", validation_alias=AliasChoices("VERCEL_ENV", "GATEWAY_ENVIRONMENT")
    )

    @property
    def mock_control_enabled(self) -> bool:
        """Callers may steer the mock provider with a header, but never on an authenticated gateway."""
        return self.allow_mock_control and not self.auth_required

    @property
    def debug_enabled(self) -> bool:
        """/internal/* endpoints expose the policy and raw events: local development only."""
        return self.debug_endpoints and not self.auth_required

    @property
    def auth_required(self) -> bool:
        if self.auth == "required":
            return True
        if self.auth == "off":
            return False
        return bool(self.api_keys or self.jwt_secret or os.environ.get("VERCEL"))

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
