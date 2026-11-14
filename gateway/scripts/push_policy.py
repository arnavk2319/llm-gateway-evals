"""Validate a policy YAML and publish it as the new active version.

    DATABASE_URL=postgresql://... uv run python scripts/push_policy.py path/to/policy.yaml

Until the control plane exists (phase 2/3), this is how routing policy changes ship.
The previous active version is retired; the hot path picks up the change within
GATEWAY_POLICY_TTL_S seconds.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.config import ENV_FILES

for _f in ENV_FILES:  # DATABASE_URL etc. from .env, like the gateway itself
    load_dotenv(_f)

from app.policy import load_policy_file  # noqa: E402
from app.sinks import PostgresSink  # noqa: E402


async def main(path: str) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")
    policy = load_policy_file(Path(path))  # validates
    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    try:
        current = await sink.load_active_policy()
        version = await sink.save_policy(
            policy.model_dump(mode="json"), status="active", created_by="push_policy",
            parent_version=current[0] if current else None,
        )
        print(f"published policy version {version} (parent {current[0] if current else None})")
    finally:
        await sink.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
