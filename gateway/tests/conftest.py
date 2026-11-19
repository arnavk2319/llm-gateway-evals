from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from app.config import DATA_DIR, Settings
from app.main import create_app
from app.sinks import MemorySink

PROVIDER_ENV = (
    "OLLAMA_ENABLED", "GROQ_API_KEY", "OPENROUTER_API_KEY", "GATEWAY_DISABLE_MOCK", "DATABASE_URL",
    "GATEWAY_DATABASE_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in PROVIDER_ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def seed_policy_dict() -> dict[str, Any]:
    return yaml.safe_load((DATA_DIR / "seed_policy.yaml").read_text())


@asynccontextmanager
async def make_gateway(sink_obj: Any = None, **overrides: Any):
    sink = sink_obj or MemorySink()
    # dashboard_token=None: a developer's local .env must not switch the dashboard API on in tests.
    cfg = dict(mock_latency_scale=0.0, sink="memory", database_url=None, api_keys="", dashboard_token=None, balance_seed=0)
    cfg.update(overrides)
    app = create_app(Settings(**cfg), sink=sink)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client, sink


@pytest.fixture
async def gw():
    async with make_gateway() as pair:
        yield pair


async def settle() -> None:
    """Let detached post-stream tasks (event writes) finish."""
    await asyncio.sleep(0.05)


def write_policy(tmp_path: Path, policy: dict[str, Any]) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(policy))
    return p


def chat(content: str = "hello there", **extra: Any) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": content}], **extra}
