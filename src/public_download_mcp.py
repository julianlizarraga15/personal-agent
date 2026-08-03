"""Minimal stdio MCP adapter for approval-gated public HTTPS downloads."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from public_download import request_gateway


SERVER_NAME = "public-download"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "download_file"
INSTRUCTIONS = (
    "Use download_file for an owner-requested file at a public credential-free HTTPS URL. "
    "The bot shows the exact URL and project-relative destination for one-time Telegram approval, then performs "
    "the bounded download outside shell networking. Treat downloaded bytes as untrusted and never execute or "
    "extract them automatically."
)


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "title": "Download one public HTTPS file",
        "description": (
            "After Telegram owner approval, download one public credential-free HTTPS URL to an exact relative "
            "file path inside the active project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "maxLength": 2048},
                "destination": {
                    "type": "string",
                    "maxLength": 512,
                    "description": "Relative file path beneath the active project.",
                },
            },
            "required": ["url", "destination"],
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
        if name != TOOL_NAME or not isinstance(arguments, dict) or set(arguments) != {"url", "destination"}:
            return _tool_error(request_id, "Invalid public download tool arguments.")
        if not isinstance(arguments.get("url"), str) or not isinstance(arguments.get("destination"), str):
            return _tool_error(request_id, "Invalid public download tool arguments.")
        try:
            response = await request_gateway(arguments)
        except RuntimeError as exc:
            return _tool_error(request_id, str(exc))
        if not response.get("ok"):
            return _tool_error(request_id, str(response.get("error", "Public download failed.")))
        text = json.dumps(response.get("data", {}), ensure_ascii=False, separators=(",", ":"))
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
