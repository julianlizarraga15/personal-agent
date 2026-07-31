"""Credential-holding API-Football gateway exposed through one Unix socket."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
from typing import Any, Callable
from urllib.parse import urlencode


API_HOST = "v3.football.api-sports.io"
DEFAULT_SOCKET_PATH = Path("/run/api-football.sock")
DEFAULT_QUOTA_PATH = Path("/trace-state/api-football-quota.json")
DAILY_LIMIT = 100
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 20.0
MAX_PARAMETERS = 20
MAX_PARAMETER_VALUE_BYTES = 256
REDACTED = "[REDACTED]"

ALLOWED_ENDPOINTS = frozenset(
    {
        "status",
        "countries",
        "leagues",
        "teams",
        "teams/statistics",
        "venues",
        "standings",
        "fixtures",
        "fixtures/headtohead",
        "fixtures/statistics",
        "fixtures/events",
        "fixtures/lineups",
        "fixtures/players",
        "players",
        "players/profiles",
        "players/squads",
        "players/teams",
        "players/topscorers",
        "players/topassists",
        "players/topyellowcards",
        "players/topredcards",
        "injuries",
        "coachs",
        "transfers",
        "trophies",
        "sidelined",
    }
)

_PARAMETER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_INTEGER_RE = re.compile(r"^[0-9]{1,10}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMEZONE_RE = re.compile(r"^[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)*$")
_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
ENDPOINT_PARAMETERS: dict[str, frozenset[str]] = {
    "status": frozenset(),
    "countries": frozenset({"name", "code", "search"}),
    "leagues": frozenset({"id", "name", "country", "code", "season", "team", "type", "current", "search", "last"}),
    "teams": frozenset({"id", "name", "league", "season", "country", "code", "venue", "search"}),
    "teams/statistics": frozenset({"league", "season", "team", "date"}),
    "venues": frozenset({"id", "name", "city", "country", "search"}),
    "standings": frozenset({"league", "season", "team"}),
    "fixtures": frozenset({"id", "ids", "live", "date", "league", "season", "team", "last", "next", "from", "to", "round", "status", "venue", "timezone"}),
    "fixtures/headtohead": frozenset({"h2h", "date", "league", "season", "last", "next", "from", "to", "status", "venue", "timezone"}),
    "fixtures/statistics": frozenset({"fixture", "team", "type"}),
    "fixtures/events": frozenset({"fixture", "team", "player", "type"}),
    "fixtures/lineups": frozenset({"fixture", "team", "player", "type"}),
    "fixtures/players": frozenset({"fixture", "team"}),
    "players": frozenset({"id", "team", "league", "season", "search", "page"}),
    "players/profiles": frozenset({"player", "search", "page"}),
    "players/squads": frozenset({"team", "player"}),
    "players/teams": frozenset({"player"}),
    "players/topscorers": frozenset({"league", "season"}),
    "players/topassists": frozenset({"league", "season"}),
    "players/topyellowcards": frozenset({"league", "season"}),
    "players/topredcards": frozenset({"league", "season"}),
    "injuries": frozenset({"league", "season", "fixture", "team", "player", "date", "timezone"}),
    "coachs": frozenset({"id", "team", "search"}),
    "transfers": frozenset({"player", "team"}),
    "trophies": frozenset({"player", "coach"}),
    "sidelined": frozenset({"player", "coach"}),
}


class ApiFootballError(RuntimeError):
    """A stable error safe to return across the credential boundary."""


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def validate_request(value: object) -> tuple[str, dict[str, str]]:
    """Validate the local protocol and return a safe endpoint and query map."""

    if not isinstance(value, dict) or set(value) - {"method", "endpoint", "params"}:
        raise ApiFootballError("Malformed API-Football request.")
    if value.get("method") != "GET":
        raise ApiFootballError("Only GET requests are allowed.")
    endpoint = value.get("endpoint")
    if not isinstance(endpoint, str) or endpoint not in ALLOWED_ENDPOINTS:
        raise ApiFootballError("Unknown or disallowed API-Football endpoint.")
    if "://" in endpoint or endpoint.startswith("/") or ".." in endpoint or "\\" in endpoint:
        raise ApiFootballError("Invalid API-Football endpoint.")
    params = value.get("params", {})
    if not isinstance(params, dict) or len(params) > MAX_PARAMETERS:
        raise ApiFootballError("Malformed or excessive API-Football parameters.")
    clean: dict[str, str] = {}
    for key, item in params.items():
        if not isinstance(key, str) or not _PARAMETER_NAME_RE.fullmatch(key):
            raise ApiFootballError("Invalid API-Football parameter name.")
        if key not in ENDPOINT_PARAMETERS[endpoint]:
            raise ApiFootballError(f"Parameter {key} is not allowed for endpoint {endpoint}.")
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ApiFootballError("Invalid API-Football parameter value.")
        text = str(item)
        if len(text.encode("utf-8")) > MAX_PARAMETER_VALUE_BYTES or not _SAFE_TEXT_RE.fullmatch(text):
            raise ApiFootballError("Invalid API-Football parameter value.")
        lowered = text.lower()
        if "://" in lowered or ".." in text or "\\" in text or text.startswith("/"):
            raise ApiFootballError("Invalid API-Football parameter value.")
        if key in {"id", "league", "season", "team", "player", "fixture", "venue", "coach", "page", "last", "next"} and not _INTEGER_RE.fullmatch(text):
            raise ApiFootballError(f"Parameter {key} must be an integer.")
        if key in {"date", "from", "to"} and not _DATE_RE.fullmatch(text):
            raise ApiFootballError(f"Parameter {key} must use YYYY-MM-DD.")
        if key == "timezone" and not _TIMEZONE_RE.fullmatch(text):
            raise ApiFootballError("Invalid timezone parameter.")
        clean[key] = text
    return endpoint, clean


def redact_secret(value: Any, secret: str) -> Any:
    """Recursively remove the configured credential from untrusted values."""

    if isinstance(value, str):
        return value.replace(secret, REDACTED) if secret else value
    if isinstance(value, list):
        return [redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {str(redact_secret(key, secret)): redact_secret(item, secret) for key, item in value.items()}
    return value


class DailyQuota:
    """Atomic UTC daily request counter for the single in-process gateway."""

    def __init__(self, path: Path = DEFAULT_QUOTA_PATH, limit: int = DAILY_LIMIT) -> None:
        self.path = path
        self.limit = limit
        self._lock = asyncio.Lock()

    async def consume(self, *, day: str | None = None) -> int:
        async with self._lock:
            current_day = day or _utc_day()
            count = 0
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("date") == current_day:
                    count = int(value.get("count", 0))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ApiFootballError("API-Football quota state is unavailable.") from exc
            if count >= self.limit:
                raise ApiFootballError("API-Football daily request limit reached.")
            count += 1
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    json.dump({"date": current_day, "count": count}, handle, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            except OSError as exc:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ApiFootballError("API-Football quota state is unavailable.") from exc
            return count


def _upstream_get(endpoint: str, params: dict[str, str], key: str) -> dict[str, Any]:
    """Call the fixed TLS origin without consulting proxy environment variables."""

    path = f"/{endpoint}"
    if params:
        path += "?" + urlencode(params)
    connection = http.client.HTTPSConnection(
        API_HOST,
        443,
        timeout=UPSTREAM_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        connection.request("GET", path, headers={"x-apisports-key": key, "Accept": "application/json"})
        response = connection.getresponse()
        declared = response.getheader("Content-Length")
        if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
            raise ApiFootballError("API-Football response exceeded the size limit.")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ApiFootballError("API-Football response exceeded the size limit.")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiFootballError("API-Football returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise ApiFootballError("API-Football returned an invalid response.")
        payload = redact_secret(payload, key)
        if response.status < 200 or response.status >= 300:
            raise ApiFootballError(f"API-Football upstream request failed with HTTP {response.status}.")
        return payload
    except (TimeoutError, socket.timeout) as exc:
        raise ApiFootballError("API-Football request timed out.") from exc
    except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
        raise ApiFootballError("API-Football upstream request failed.") from exc
    finally:
        connection.close()


class ApiFootballGateway:
    """Small newline-delimited JSON server owning the upstream credential."""

    def __init__(
        self,
        key: str | None,
        *,
        socket_path: Path = DEFAULT_SOCKET_PATH,
        quota: DailyQuota | None = None,
        upstream: Callable[[str, dict[str, str], str], dict[str, Any]] = _upstream_get,
    ) -> None:
        self.key = (key or "").strip()
        self.socket_path = socket_path
        self.quota = quota or DailyQuota()
        self.upstream = upstream
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            mode = self.socket_path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(mode):
                raise RuntimeError("API-Football socket path is not a socket.")
            self.socket_path.unlink()
        try:
            self.server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
        except Exception:
            self.socket_path.unlink(missing_ok=True)
            raise

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        self.socket_path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: dict[str, Any]
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                raise ApiFootballError("Malformed API-Football request.")
            request = json.loads(raw)
            endpoint, params = validate_request(request)
            if not self.key:
                raise ApiFootballError("API-Football is not configured.")
            await self.quota.consume()
            payload = await asyncio.wait_for(
                asyncio.to_thread(self.upstream, endpoint, params, self.key),
                timeout=UPSTREAM_TIMEOUT_SECONDS + 2,
            )
            response = {"ok": True, "data": redact_secret(payload, self.key)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = {"ok": False, "error": "Malformed API-Football request."}
        except asyncio.TimeoutError:
            response = {"ok": False, "error": "API-Football request timed out."}
        except ApiFootballError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            response = {"ok": False, "error": "API-Football request failed."}
        encoded = json.dumps(redact_secret(response, self.key), separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"ok":false,"error":"API-Football response exceeded the size limit."}\n'
        try:
            writer.write(encoded)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()
