from __future__ import annotations

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
    idempotency_ttl_s: int = 300

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
