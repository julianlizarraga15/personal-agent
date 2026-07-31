"""Sandbox-visible client for the private API-Football gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from api_football import ALLOWED_ENDPOINTS, DEFAULT_SOCKET_PATH, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="api-football",
        description="Query approved read-only API-Football analytics without exposing credentials.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("endpoints", help="list approved endpoint paths")
    get = commands.add_parser("get", help="perform one approved GET request")
    get.add_argument("endpoint", help="endpoint path from `api-football endpoints`")
    get.add_argument("parameters", nargs="*", metavar="key=value")
    return parser


def _parameters(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid parameter {value!r}; expected key=value")
        key, item = value.split("=", 1)
        if not key or not item or key in result:
            raise ValueError(f"invalid parameter {value!r}")
        result[key] = item
    return result


async def request_gateway(request: dict[str, object], socket_path: Path = DEFAULT_SOCKET_PATH) -> dict[str, object]:
    encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RuntimeError("API-Football request is too large.")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path), limit=MAX_RESPONSE_BYTES + 1),
            timeout=5,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError("API-Football helper is unavailable.") from exc
    try:
        writer.write(encoded)
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=25)
        if not raw or len(raw) > MAX_RESPONSE_BYTES or not raw.endswith(b"\n"):
            raise RuntimeError("API-Football helper returned an invalid response.")
        response = json.loads(raw)
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise RuntimeError("API-Football helper returned an invalid response.")
        return response
    except (json.JSONDecodeError, UnicodeDecodeError, asyncio.TimeoutError) as exc:
        raise RuntimeError("API-Football helper returned an invalid response.") from exc
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> int:
    args = _parser().parse_args()
    if args.command == "endpoints":
        print(json.dumps(sorted(ALLOWED_ENDPOINTS), indent=2))
        return 0
    try:
        params = _parameters(args.parameters)
        response = asyncio.run(
            request_gateway({"method": "GET", "endpoint": args.endpoint, "params": params})
        )
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not response["ok"]:
        print(str(response.get("error", "API-Football request failed.")), file=sys.stderr)
        return 1
    print(json.dumps(response.get("data"), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
