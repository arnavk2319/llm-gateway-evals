"""Create or update the database schema. Safe to run any time (it is idempotent).

    DATABASE_URL=postgresql://... uv run python scripts/migrate.py

The gateway also does this on startup, but run it once after creating a new database
(for example a Neon branch) so the dashboard has its tables before the first request.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from app.config import ENV_FILES

for _f in ENV_FILES:
    load_dotenv(_f)

from app.sinks import PostgresSink  # noqa: E402


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")
    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    await sink.close()
    print("schema is up to date")


if __name__ == "__main__":
    asyncio.run(main())
