"""Thin application adapter around the official asynchronous Codex SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, Sandbox


LOGGER = logging.getLogger(__name__)
StatusCallback = Callable[[str], Awaitable[None]]


class CodexBackendError(RuntimeError):
    """A Codex failure with a concise response safe to show in Telegram."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class CodexBusyError(CodexBackendError):
    """The user already has a turn in progress."""


class CodexTurnDiscarded(RuntimeError):
    """The session was intentionally replaced while its turn was ending."""


@dataclass
class CodexSession:
    cwd: Path
    thread: Any
    active_turn: Any | None = None
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def event_status(event: Any) -> str | None:
    """Map the small set of useful SDK events to coarse Telegram activity."""

    method = getattr(event, "method", "")
    payload = getattr(event, "payload", None)
    item = getattr(payload, "item", None)
    item = getattr(item, "root", item)
    item_type = getattr(item, "type", None)

    if method == "turn/started":
        return "Thinking…"
    if method == "item/started":
        return {
            "reasoning": "Thinking…",
            "commandExecution": "Running a command…",
            "fileChange": "Changing files…",
        }.get(item_type)
    if method == "item/completed":
        return {
            "commandExecution": "Command finished…",
            "fileChange": "Files changed…",
        }.get(item_type)
    return None


def final_message_from_items(items: list[Any]) -> str | None:
    """Return the last final (or phase-less) completed agent message."""

    fallback: str | None = None
    for wrapped in items:
        item = getattr(wrapped, "root", wrapped)
        if getattr(item, "type", None) != "agentMessage":
            continue
        text = getattr(item, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        phase = getattr(item, "phase", None)
        phase_value = getattr(phase, "value", phase)
        if phase_value == "final_answer":
            fallback = text.strip()
        elif phase is None:
            fallback = text.strip()
    return fallback


def translate_codex_error(exc: BaseException) -> CodexBackendError:
    """Collapse SDK/runtime details into stable, non-sensitive user errors."""

    detail = f"{type(exc).__name__}: {exc}".lower()
    if any(token in detail for token in ("not logged in", "not authenticated", "unauthorized", "authentication", "login required", "401")):
        return CodexBackendError(
            "Codex isn’t signed in. Run the one-time ChatGPT device login on the host, then try again."
        )
    if any(token in detail for token in ("rate limit", "usage limit", "quota", "subscription limit", "insufficient_quota", "429")):
        return CodexBackendError(
            "Your Codex subscription limit has been reached. Try again after the limit resets."
        )
    if any(token in detail for token in ("sandbox", "permission denied", "operation not permitted", "approval required", "outside workspace", "network policy")):
        return CodexBackendError(
            "Codex couldn’t do that because the workspace sandbox denied the operation."
        )
    if any(token in detail for token in ("transport", "connection", "websocket", "broken pipe", "app-server", "app server", "stream disconnected")):
        return CodexBackendError(
            "Codex lost its local connection. Please try again; restart the bot if it keeps happening."
        )
    return CodexBackendError("Codex couldn’t complete that request. Please try again.")


class CodexBackend:
    """Application-scoped SDK client and ephemeral per-user threads."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or AsyncCodex()
        self.sessions: dict[int, CodexSession] = {}
        self._started = False
        self._session_lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._started:
            await self.client.__aenter__()
            self._started = True

    async def close(self) -> None:
        sessions = list(self.sessions.values())
        self.sessions.clear()
        for session in sessions:
            if session.active_turn is not None:
                try:
                    await session.active_turn.interrupt()
                except Exception:
                    LOGGER.debug("Codex turn interruption failed during shutdown", exc_info=True)
        if self._started:
            await self.client.__aexit__(None, None, None)
            self._started = False

    async def _start_thread(self, cwd: Path) -> CodexSession:
        try:
            thread = await self.client.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(cwd),
                ephemeral=True,
                sandbox=Sandbox.workspace_write,
            )
        except Exception as exc:
            raise translate_codex_error(exc) from exc
        return CodexSession(cwd=cwd, thread=thread)

    async def new_session(self, user_id: int, cwd: Path) -> CodexSession:
        cwd = cwd.resolve()
        async with self._session_lock:
            old = self.sessions.pop(user_id, None)
            if old is not None and old.active_turn is not None:
                try:
                    await old.active_turn.interrupt()
                except Exception:
                    LOGGER.debug("Codex turn interruption failed while replacing session", exc_info=True)
            session = await self._start_thread(cwd)
            self.sessions[user_id] = session
            return session

    async def stop_session(self, user_id: int) -> bool:
        async with self._session_lock:
            session = self.sessions.pop(user_id, None)
        if session is None:
            return False
        if session.active_turn is not None:
            try:
                await session.active_turn.interrupt()
            except Exception as exc:
                LOGGER.warning("Codex turn interruption failed error_type=%s", type(exc).__name__)
        return True

    async def run_turn(
        self,
        user_id: int,
        text: str,
        *,
        default_cwd: Path,
        on_status: StatusCallback | None = None,
    ) -> str:
        session = self.sessions.get(user_id)
        if session is None:
            session = await self.new_session(user_id, default_cwd)
        if session.active_turn is not None or session.turn_lock.locked():
            raise CodexBusyError("I’m still working on your previous request.")

        await session.turn_lock.acquire()
        completed_items: list[Any] = []
        try:
            handle = await session.thread.turn(text)
            session.active_turn = handle
            if self.sessions.get(user_id) is not session:
                await handle.interrupt()
                raise CodexTurnDiscarded()
            async for event in handle.stream():
                status = event_status(event)
                if status is not None and on_status is not None:
                    await on_status(status)
                if getattr(event, "method", "") == "item/completed":
                    item = getattr(getattr(event, "payload", None), "item", None)
                    if item is not None:
                        completed_items.append(item)
                if getattr(event, "method", "") == "turn/completed":
                    turn = getattr(getattr(event, "payload", None), "turn", None)
                    status_value = getattr(getattr(turn, "status", None), "value", getattr(turn, "status", None))
                    if status_value == "failed":
                        error = getattr(turn, "error", None)
                        raise RuntimeError(getattr(error, "message", None) or "Codex turn failed")
        except asyncio.CancelledError:
            if session.active_turn is not None:
                try:
                    await session.active_turn.interrupt()
                except Exception:
                    LOGGER.debug("Codex turn interruption failed after cancellation", exc_info=True)
            raise
        except CodexBackendError:
            raise
        except CodexTurnDiscarded:
            raise
        except Exception as exc:
            if self.sessions.get(user_id) is not session:
                raise CodexTurnDiscarded() from exc
            raise translate_codex_error(exc) from exc
        finally:
            session.active_turn = None
            session.turn_lock.release()

        if self.sessions.get(user_id) is not session:
            raise CodexTurnDiscarded()
        response = final_message_from_items(completed_items)
        if response is None:
            raise CodexBackendError("Codex finished without returning a response. Please try again.")
        return response
