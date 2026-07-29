"""Owner-visible, redacted execution traces stored outside Git."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any


LOGGER = logging.getLogger(__name__)
TRACE_SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 7
REDACTED = "[REDACTED]"

_SECRET_FIELD = re.compile(
    r"(?:^|_)(?:api[_-]?key|authorization|auth[_-]?token|password|passwd|secret|"
    r"credential|private[_-]?key|telegram[_-]?(?:bot[_-]?)?token|cookie|session[_-]?key)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_ENV_ASSIGNMENT = re.compile(
    r"(?m)^(\s*(?:OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|.*(?:PASSWORD|SECRET|TOKEN|PRIVATE_KEY|CREDENTIAL).*)\s*=\s*).+$",
    re.IGNORECASE,
)
_DATA_URL = re.compile(r"^data:([^;,]+);base64,([A-Za-z0-9+/=\s]+)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return vars(value)
    return value


def binary_metadata(data: bytes, media_type: str = "application/octet-stream") -> dict[str, Any]:
    return {
        "binary": True,
        "media_type": media_type,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _looks_like_env_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    name = Path(value).name.lower()
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


def redact(value: Any, *, field: str = "", env_content: bool = False) -> Any:
    """Return a JSON-safe copy with credentials and raw binary removed."""

    value = _jsonable(value)
    if _SECRET_FIELD.search(field):
        return REDACTED
    if isinstance(value, (bytes, bytearray, memoryview)):
        return binary_metadata(bytes(value))
    if isinstance(value, dict):
        contains_env_path = any(
            str(key).lower() in {"path", "file", "filename", "name"} and _looks_like_env_path(item)
            for key, item in value.items()
        )
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            redact_as_env = env_content or (
                contains_env_path
                and key_text.lower() in {"content", "output", "stdout", "stderr", "old_text", "new_text"}
            )
            result[key_text] = redact(item, field=key_text, env_content=redact_as_env)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, env_content=env_content) for item in value]
    if isinstance(value, str):
        if env_content:
            return "[REDACTED .env content]"
        match = _DATA_URL.match(value)
        if match:
            try:
                data = base64.b64decode(match.group(2), validate=False)
            except ValueError:
                return {"binary": True, "media_type": match.group(1), "size": None, "sha256": None}
            return binary_metadata(data, match.group(1))
        cleaned = value
        for pattern in _SECRET_TEXT_PATTERNS:
            cleaned = pattern.sub(REDACTED, cleaned)
        cleaned = _ENV_ASSIGNMENT.sub(r"\1[REDACTED]", cleaned)
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value), field=field, env_content=env_content)


class TraceStore:
    """Persistent ordered trace store safe for concurrent bot turns."""

    def __init__(self, path: Path | str, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trace_turns (
                    turn_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    project TEXT,
                    route TEXT,
                    models_json TEXT NOT NULL DEFAULT '[]',
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trace_events (
                    turn_id TEXT NOT NULL REFERENCES trace_turns(turn_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (turn_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS trace_turns_started_at ON trace_turns(started_at);
                """
            )
            self._purge_connection(connection)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            LOGGER.warning("could not set owner-only permissions on trace database path=%s", self.path)

    def _purge_connection(self, connection: sqlite3.Connection) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        cursor = connection.execute("DELETE FROM trace_turns WHERE started_at < ?", (cutoff,))
        return cursor.rowcount

    def purge(self) -> int:
        with self._lock, self._connect() as connection:
            return self._purge_connection(connection)

    def start_turn(
        self,
        turn_id: str,
        user_id: int,
        *,
        project: str | None,
        kind: str,
        data: Any | None = None,
    ) -> "TraceRecorder":
        timestamp = utc_now()
        with self._lock, self._connect() as connection:
            self._purge_connection(connection)
            connection.execute(
                "INSERT INTO trace_turns(turn_id,user_id,started_at,updated_at,status,project,schema_version) "
                "VALUES(?,?,?,?,?,?,?)",
                (turn_id, user_id, timestamp, timestamp, "running", project, TRACE_SCHEMA_VERSION),
            )
            connection.execute(
                "INSERT INTO trace_events(turn_id,sequence,timestamp,event_type,data_json) VALUES(?,?,?,?,?)",
                (turn_id, 1, timestamp, "turn.started", json.dumps(redact({"kind": kind, **(data or {})}), ensure_ascii=False)),
            )
        return TraceRecorder(self, turn_id)

    def append(
        self,
        turn_id: str,
        event_type: str,
        data: Any | None = None,
        *,
        status: str | None = None,
        route: str | None = None,
        model: str | None = None,
    ) -> None:
        timestamp = utc_now()
        payload = json.dumps(redact(data or {}), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._purge_connection(connection)
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM trace_events WHERE turn_id=?", (turn_id,)
            ).fetchone()
            sequence = int(row[0])
            connection.execute(
                "INSERT INTO trace_events(turn_id,sequence,timestamp,event_type,data_json) VALUES(?,?,?,?,?)",
                (turn_id, sequence, timestamp, event_type, payload),
            )
            updates = ["updated_at=?"]
            values: list[Any] = [timestamp]
            if status is not None:
                updates.append("status=?")
                values.append(status)
            if route is not None:
                updates.append("route=?")
                values.append(route)
            if model is not None:
                existing = connection.execute(
                    "SELECT models_json FROM trace_turns WHERE turn_id=?", (turn_id,)
                ).fetchone()
                models = json.loads(existing[0]) if existing else []
                if model not in models:
                    models.append(model)
                updates.append("models_json=?")
                values.append(json.dumps(models))
            values.append(turn_id)
            connection.execute(f"UPDATE trace_turns SET {', '.join(updates)} WHERE turn_id=?", values)

    def list_turns(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        self.purge()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT turn_id,started_at,updated_at,status,project,route,models_json "
                "FROM trace_turns WHERE user_id=? ORDER BY started_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 100))),
            ).fetchall()
        return [
            {
                "turn_id": row[0], "started_at": row[1], "updated_at": row[2], "status": row[3],
                "project": row[4], "route": row[5], "models": json.loads(row[6]),
            }
            for row in rows
        ]

    def export_turn(self, user_id: int, turn_id: str | None = None) -> dict[str, Any] | None:
        self.purge()
        with self._connect() as connection:
            if turn_id is None:
                row = connection.execute(
                    "SELECT turn_id,user_id,started_at,updated_at,status,project,route,models_json "
                    "FROM trace_turns WHERE user_id=? ORDER BY started_at DESC LIMIT 1", (user_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT turn_id,user_id,started_at,updated_at,status,project,route,models_json "
                    "FROM trace_turns WHERE user_id=? AND turn_id=?", (user_id, turn_id)
                ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                "SELECT sequence,timestamp,event_type,data_json FROM trace_events WHERE turn_id=? ORDER BY sequence",
                (row[0],),
            ).fetchall()
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "turn": {
                "turn_id": row[0], "user_id": row[1], "started_at": row[2], "updated_at": row[3],
                "status": row[4], "project": row[5], "route": row[6], "models": json.loads(row[7]),
            },
            "events": [
                {"sequence": item[0], "timestamp": item[1], "type": item[2], "data": json.loads(item[3])}
                for item in events
            ],
            "limitations": [
                "Provider-hidden controls and raw chain-of-thought are not available to the application.",
                "Hosted-search internals not returned by the provider are unavailable.",
                "Credential values are redacted and binary payloads are represented by type, size, and SHA-256.",
            ],
        }


class TraceRecorder:
    """Small fail-open facade used by request, tool, and deployment code."""

    def __init__(self, store: TraceStore | None, turn_id: str) -> None:
        self.store = store
        self.turn_id = turn_id

    def event(self, event_type: str, data: Any | None = None, **metadata: Any) -> None:
        if self.store is None:
            return
        try:
            self.store.append(self.turn_id, event_type, data, **metadata)
        except Exception:
            LOGGER.exception("trace write failed turn_id=%s event_type=%s", self.turn_id, event_type)

    def finish(self, status: str, data: Any | None = None) -> None:
        self.event("turn.finished", data or {}, status=status)


def configured_trace_store() -> TraceStore:
    state_dir = Path(os.environ.get("DEPLOYMENT_STATE_DIR", "/workspace/.personal-agent-state"))
    path = os.environ.get("TRACE_DB_PATH", str(state_dir / "traces.sqlite3"))
    raw_retention = os.environ.get("TRACE_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        retention = max(1, int(raw_retention))
    except ValueError:
        LOGGER.warning("invalid TRACE_RETENTION_DAYS; using default")
        retention = DEFAULT_RETENTION_DAYS
    return TraceStore(path, retention)
