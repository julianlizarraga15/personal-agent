"""Minimal stdio MCP adapter for approval-gated Git publication."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from git_publish import request_gateway


SERVER_NAME = "git-publish"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "publish"
INSTRUCTIONS = (
    "Use publish only after committing a clean working tree and pass the exact full HEAD commit ID. "
    "The bot validates one configured repository and branch, asks the Telegram owner for approval, "
    "and keeps the deploy key unavailable to Codex. Never attempt to locate, read, print, or reveal credentials."
)


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "title": "Publish the configured Git repository",
        "description": "Push an exact clean local HEAD to the configured GitHub repository after owner approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "commit": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{40}$",
                    "description": "Exact full commit ID returned by git rev-parse HEAD.",
                }
            },
            "required": ["commit"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


def _tool_error(request_id: object, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": message}], "isError": True},
    }


async def handle_request(message: object) -> dict[str, Any] | None:
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
            return _tool_error(request_id, "Unknown Git publication tool.")
        commit = arguments.get("commit")
        if not isinstance(commit, str):
            return _tool_error(request_id, "Invalid Git publication tool arguments.")
        try:
            response = await request_gateway({"commit": commit})
        except RuntimeError as exc:
            return _tool_error(request_id, str(exc))
        if not response.get("ok"):
            return _tool_error(request_id, str(response.get("error", "Git publication failed.")))
        data = response.get("data", {})
        text = (
            f"Published commit {data.get('commit', 'unknown')} to "
            f"{data.get('remote', 'the configured remote')} branch {data.get('branch', 'unknown')}."
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        }
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}


async def serve() -> None:
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
            sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> int:
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
