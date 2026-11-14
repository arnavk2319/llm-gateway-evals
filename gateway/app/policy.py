from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, Field

log = logging.getLogger("gateway.policy")


class Capabilities(BaseModel):
    tools: bool = False
    json_mode: bool = False
    streaming: bool = True
    max_context: int = 8192


class Price(BaseModel):
    """List price in USD per 1M tokens."""

    input_per_1m: float = 0.0
    output_per_1m: float = 0.0


class ModelSpec(BaseModel):
    id: str
    provider: str
    model: str
    family: str
    tier: int = 1
    free: bool = False  # billed cost is 0 but shadow cost uses the list price
    capabilities: Capabilities = Field(default_factory=Capabilities)
    price: Price = Field(default_factory=Price)
    latency_ms_p50: float = 500.0
    quality: dict[str, float] = Field(default_factory=lambda: {"default": 0.5})

    def quality_for(self, task_type: str) -> float:
        return self.quality.get(task_type, self.quality.get("default", 0.5))


class ProviderSpec(BaseModel):
    kind: Literal["openai_compat", "mock"]
    base_url: str | None = None
    api_key_env: str | None = None
    # Optional second key for zero-downtime rotation / failover on a 401 or 403.
    api_key_env_secondary: str | None = None
    requires_env: list[str] = Field(default_factory=list)
    # Provider is hidden when any of these env vars is set (e.g. disable mock).
    disabled_if_env: list[str] = Field(default_factory=list)

    def available(self, env: Mapping[str, str]) -> bool:
        if any(env.get(k) for k in self.disabled_if_env):
            return False
        return all(env.get(k) for k in self.requires_env)


class TaskPolicy(BaseModel):
    min_tier: int = 1


class RoleConstraint(BaseModel):
    # "family": the model serving this role must come from a different family
    # than the model that produced the parent span (relaxed if impossible).
    independence: Literal["none", "family"] = "none"


class RetryPolicy(BaseModel):
    max_attempts: int = 3
    deadline_s: float = 30.0
    per_attempt_timeout_s: float = 20.0
    first_token_timeout_s: float = 15.0
    # Backoff (with jitter) for in-place retries of a pinned model.
    backoff_base_s: float = 0.2
    backoff_max_s: float = 2.0


class Weights(BaseModel):
    cost: float = 0.3
    latency: float = 0.15
    quality: float = 0.55


class LLMClassify(BaseModel):
    enabled: bool = False
    threshold: float = 0.7
    model: str | None = None
    timeout_s: float = 3.0


class Policy(BaseModel):
    version: int = 1
    status: str = "active"
    providers: dict[str, ProviderSpec]
    models: list[ModelSpec]
    task_policies: dict[str, TaskPolicy] = Field(default_factory=dict)
    role_constraints: dict[str, RoleConstraint] = Field(default_factory=dict)
    weights: Weights = Field(default_factory=Weights)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    llm_classify: LLMClassify = Field(default_factory=LLMClassify)

    def task_policy(self, task_type: str) -> TaskPolicy:
        return self.task_policies.get(task_type) or self.task_policies.get("default") or TaskPolicy()

    def model_by_id(self, model_id: str | None) -> ModelSpec | None:
        if not model_id:
            return None
        return next((m for m in self.models if m.id == model_id), None)

    def available_models(self, env: Mapping[str, str] | None = None) -> list[ModelSpec]:
        env = os.environ if env is None else env
        out = []
        for m in self.models:
            prov = self.providers.get(m.provider)
            if prov is not None and prov.available(env):
                out.append(m)
        return out


def load_policy_file(path: Path) -> Policy:
    with open(path, encoding="utf-8") as f:
        return Policy.model_validate(yaml.safe_load(f))


class PolicyStore:
    """Serves the active policy from an in-process cache.

    The hot path never blocks on the control plane: if the database is
    unreachable it keeps serving the last known good policy.
    """

    def __init__(self, file_path: Path, ttl_s: float, sink: Any) -> None:
        self._file_policy = load_policy_file(file_path)
        self._ttl = ttl_s
        self._sink = sink
        self._current: Policy = self._file_policy
        self._loaded_at = 0.0

    async def startup(self) -> None:
        try:
            row = await self._sink.load_active_policy()
            if row is None and getattr(self._sink, "persists_policy", False):
                await self._sink.save_policy(
                    self._file_policy.model_dump(mode="json"), status="active", created_by="seed"
                )
        except Exception:
            log.exception("policy seed failed; serving file policy")
        await self.get(force=True)

    async def get(self, force: bool = False) -> Policy:
        now = time.monotonic()
        if not force and self._loaded_at and now - self._loaded_at < self._ttl:
            return self._current
        try:
            row = await self._sink.load_active_policy()
            if row is not None:
                version, body = row
                policy = Policy.model_validate(body)
                policy.version = version
                self._current = policy
        except Exception:
            log.exception("policy refresh failed; keeping last known good")
        self._loaded_at = now
        return self._current
