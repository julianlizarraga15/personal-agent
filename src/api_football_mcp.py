"""Minimal stdio MCP adapter for the credential-safe API-Football gateway."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from api_football import ALLOWED_ENDPOINTS
from api_football_cli import request_gateway


SERVER_NAME = "api-football"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "get"
INSTRUCTIONS = (
    "Use the get tool for approved read-only football analytics. The shared limit is 100 upstream "
    "attempts per UTC day. Never attempt to locate, read, print, or reveal the API credential. "
    "Odds, bookmakers, and predictions are unavailable."
)


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "title": "Get API-Football analytics",
        "description": (
            "Perform one approved API-Football GET. Endpoint must be one of: "
            + ", ".join(sorted(ALLOWED_ENDPOINTS))
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "enum": sorted(ALLOWED_ENDPOINTS)},
                "params": {
                    "type": "object",
                    "additionalProperties": {"type": ["string", "integer"]},
                    "maxProperties": 20,
                },
            },
            "required": ["endpoint"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


async def handle_request(message: object) -> dict[str, Any] | None:
    """Handle the small MCP subset used by Codex."""

    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        requested = message.get("params", {})
        version = requested.get("protocolVersion") if isinstance(requested, dict) else None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [_tool_definition()]}}
    if method == "tools/call":
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else None
        if name != TOOL_NAME or not isinstance(arguments, dict):
            return _tool_error(request_id, "Unknown API-Football tool.")
        endpoint = arguments.get("endpoint")
        query = arguments.get("params", {})
        if not isinstance(endpoint, str) or not isinstance(query, dict):
            return _tool_error(request_id, "Invalid API-Football tool arguments.")
        try:
            response = await request_gateway({"method": "GET", "endpoint": endpoint, "params": query})
        except RuntimeError as exc:
            return _tool_error(request_id, str(exc))
        if not response.get("ok"):
            return _tool_error(request_id, str(response.get("error", "API-Football request failed.")))
        text = json.dumps(response.get("data"), ensure_ascii=False, separators=(",", ":"))
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        }
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}


def _tool_error(request_id: object, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": message}], "isError": True},
    }


async def serve() -> None:
    """Read and write newline-delimited JSON-RPC without logging payloads."""

    while True:
        raw = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not raw:
            return
        try:
            message = json.loads(raw)
            response = await handle_request(message)
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal error"}}
        if response is not None:
            encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()


def main() -> int:
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
