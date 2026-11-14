"""Manage gateway API keys.

    DATABASE_URL=postgresql://... uv run python scripts/keys.py create --name my-saas
    DATABASE_URL=...              uv run python scripts/keys.py create --name ci --expires-days 30 \\
                                      --models groq/llama-3.1-8b,mock/small --rate-limit 300
    DATABASE_URL=...              uv run python scripts/keys.py list
    DATABASE_URL=...              uv run python scripts/keys.py rotate <key-id> [--grace-hours 24]
    DATABASE_URL=...              uv run python scripts/keys.py revoke <key-id>

A key is shown once, when it is created. Only its SHA-256 hash is stored.

Rotation without downtime: `rotate` creates a new key with the same name and scopes and lets
the old one keep working for the grace period, so you can roll the new key out to callers and
then let the old one expire (or `revoke` it earlier).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from app.config import ENV_FILES  # noqa: E402

for _f in ENV_FILES:  # DATABASE_URL etc. from .env, like the gateway itself
    load_dotenv(_f)

from app.auth import generate_key, hash_key  # noqa: E402
from app.sinks import PostgresSink  # noqa: E402


def _new_record(name: str, *, models, rate_limit, expires_at, rotated_from=None) -> tuple[str, dict]:
    key = generate_key()
    return key, {
        "id": "key_" + secrets.token_hex(6),
        "name": name,
        "key_prefix": key[:12],
        "key_hash": hash_key(key),
        "allowed_models": models,
        "rate_limit_per_min": rate_limit,
        "expires_at": expires_at,
        "rotated_from": rotated_from,
    }


def _show_new_key(key: str, rec: dict) -> None:
    print(f"created {rec['id']} for '{rec['name']}'")
    print(f"\n  {key}\n")
    print("Copy it now: it is not stored and cannot be shown again.")


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--expires-days", type=int)
    c.add_argument("--models", help="comma-separated model ids this key may use (default: all)")
    c.add_argument("--rate-limit", type=int, help="requests per minute (default: gateway default)")

    sub.add_parser("list")

    r = sub.add_parser("revoke")
    r.add_argument("id")

    ro = sub.add_parser("rotate")
    ro.add_argument("id")
    ro.add_argument("--grace-hours", type=float, default=24.0)
    ro.add_argument("--expires-days", type=int, help="expiry for the new key")

    args = p.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    try:
        if args.cmd == "create":
            models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
            exp = datetime.now(timezone.utc) + timedelta(days=args.expires_days) if args.expires_days else None
            key, rec = _new_record(args.name, models=models, rate_limit=args.rate_limit, expires_at=exp)
            await sink.create_api_key(rec)
            _show_new_key(key, rec)

        elif args.cmd == "list":
            rows = await sink.list_api_keys()
            if not rows:
                print("no keys")
            for k in rows:
                print(
                    f"{k['id']}  {k['name']:<20} {k['key_prefix']}...  {k['status']:<8} "
                    f"expires={k['expires_at'] or '-'}  last_used={k['last_used_at'] or '-'}  "
                    f"models={','.join(k['allowed_models']) if k['allowed_models'] else 'all'}  "
                    f"rpm={k['rate_limit_per_min'] or 'default'}"
                )

        elif args.cmd == "revoke":
            ok = await sink.set_api_key_status(args.id, "revoked")
            print("revoked" if ok else "no such key")
            return 0 if ok else 1

        elif args.cmd == "rotate":
            old = next((k for k in await sink.list_api_keys() if k["id"] == args.id), None)
            if old is None:
                print("no such key")
                return 1
            now = datetime.now(timezone.utc)
            exp = now + timedelta(days=args.expires_days) if args.expires_days else None
            key, rec = _new_record(
                old["name"], models=old["allowed_models"], rate_limit=old["rate_limit_per_min"],
                expires_at=exp, rotated_from=old["id"],
            )
            await sink.create_api_key(rec)
            grace_end = now + timedelta(hours=args.grace_hours)
            if old["expires_at"] is None or old["expires_at"] > grace_end:
                await sink.set_api_key_expiry(old["id"], grace_end)
            _show_new_key(key, rec)
            print(f"old key {old['id']} stays valid until {grace_end.isoformat(timespec='seconds')}")
    finally:
        await sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
