"""Small, human-readable activity log for completed agent work."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import threading


_LOCK = threading.Lock()
_DEFAULT_FILENAME = "WORK_LOG.md"
_MAX_FIELD_LENGTH = 800


def _field(value: object, limit: int = _MAX_FIELD_LENGTH) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _git_status(project: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--short"], cwd=project, capture_output=True,
            text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode:
        return "unavailable"
    lines = [_field(line, 240) for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return "clean"
    return "; ".join(lines[:20]) + ("; …" if len(lines) > 20 else "")


def record_work(
    project: Path, *, source: str, request: str, outcome: str, response: str = ""
) -> Path | None:
    """Append one concise turn record; documentation failure is best-effort."""

    try:
        root = project.resolve(strict=True)
        if not root.is_dir():
            return None
        filename = os.environ.get("AGENT_WORK_LOG_FILENAME", _DEFAULT_FILENAME).strip()
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            filename = _DEFAULT_FILENAME
        destination = root / filename
        entry = (
            f"\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · {_field(source, 80)}\n\n"
            f"- Request: {_field(request)}\n"
            f"- Outcome: {_field(outcome, 240)}\n"
            f"- Response: {_field(response)}\n"
            f"- Git status: {_git_status(root)}\n"
            "- Next step: Review this entry and continue from the recorded Git status.\n"
        )
        with _LOCK:
            with destination.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        return destination
    except (OSError, ValueError):
        return None
