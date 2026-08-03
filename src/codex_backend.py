"""Thin application adapter around the official asynchronous Codex SDK."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import ipaddress
import logging
import os
from pathlib import Path
import re
from typing import Any

import openai_codex
from openai_codex import AsyncCodex, ImageInput as SDKImageInput, TextInput
from openai_codex.api import AsyncThread
from openai_codex.client import CodexConfig
from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
    AskForApproval,
    Granular,
    GranularAskForApproval,
    ThreadStartParams,
)


LOGGER = logging.getLogger(__name__)
StatusCallback = Callable[[str], Awaitable[None]]
ApprovalCallback = Callable[["NetworkApprovalRequest"], Awaitable[bool]]
PINNED_CODEX_VERSION = "0.144.4"
TELEGRAM_APPROVAL_MODE = "telegram_user"
APPROVAL_TIMEOUT_SECONDS = 305
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

CODEX_PERMISSION_OVERRIDES = (
    'default_permissions="telegram-workspace"',
    'permissions.telegram-workspace.extends=":workspace"',
    'permissions.telegram-workspace.filesystem={"/codex-home"="deny","/trace-state"="deny",'
    '"/git-publish-secrets"="deny",'
    '"/openai-transcription-secrets"="deny",'
    '":workspace_roots"={"**/*.env"="deny"},glob_scan_max_depth=6}',
    "permissions.telegram-workspace.network.enabled=false",
    'mcp_servers.api-football.command="/usr/local/bin/api-football-mcp"',
    "mcp_servers.api-football.required=true",
    'mcp_servers.api-football.enabled_tools=["get"]',
    'mcp_servers.api-football.default_tools_approval_mode="auto"',
    'mcp_servers.git-publish.command="/usr/local/bin/git-publish-mcp"',
    "mcp_servers.git-publish.required=true",
    'mcp_servers.git-publish.enabled_tools=["publish"]',
    'mcp_servers.git-publish.default_tools_approval_mode="auto"',
    'mcp_servers.git-publish.tools.publish.approval_mode="approve"',
)

CODEX_CAPABILITY_INSTRUCTION = (
    "A credential-safe API-Football MCP get tool is available for approved football analytics. "
    "Use that tool rather than shell networking. Never attempt to locate, read, print, or reveal its credential. "
    "A credential-safe Git publish MCP tool can push the one configured repository after explicit Telegram owner "
    "approval. Before using it, commit all changes, ensure the working tree is clean, and pass the exact full HEAD "
    "commit ID. When the owner asks to publish, push, or retry publication, you must call the Git publish tool "
    "exactly once during that turn. Do not infer or repeat an approval result from an earlier turn, and do not claim "
    "that publication was attempted, approved, rejected, or completed unless the current tool call returned that "
    "outcome. If the tool cannot be called, report that it is unavailable. Never try to obtain or use the deploy key "
    "directly. "
    "When the user asks you to send an image displayed as a Telegram photo, include one standalone line per "
    "image at the end of the final response in exactly this form: [[telegram_image:path/to/image.png]]. When "
    "the user asks for a file or document, or says to send something as files, use one standalone line per file "
    "in exactly this form: [[telegram_file:path/to/file]]. Send the requested originals individually instead of "
    "creating an archive solely to work around delivery limitations. Use paths inside the current workspace or "
    "selected project, and do not use either marker for items the user did not ask to receive."
)

_TELEGRAM_ATTACHMENT_RE = re.compile(
    r"^[ \t]*\[\[telegram_(image|file):(.+?)\]\][ \t]*$", re.MULTILINE
)
MAX_TELEGRAM_ATTACHMENTS_PER_TURN = 4


@dataclass(frozen=True, slots=True)
class NetworkApprovalRequest:
    """A destination-scoped network request safe to render in Telegram."""

    method: str
    thread_id: str
    turn_id: str
    host: str
    protocol: str
    port: int
    reason: str
    cwd: str

    @property
    def destination(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    def response(self, approved: bool) -> dict[str, Any]:
        return {"decision": "accept" if approved else "decline"}


def _decline_approval(method: str) -> dict[str, Any]:
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn"}
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return {"decision": "decline"}
    return {}


def _public_https_host(host: object, protocol: object, port: object) -> tuple[str, int] | None:
    if not isinstance(host, str) or not isinstance(protocol, str):
        return None
    normalized = host.strip().lower().rstrip(".")
    if protocol.lower() != "https" or not _HOSTNAME_RE.fullmatch(normalized):
        return None
    if normalized == "localhost" or normalized.endswith((".localhost", ".local", ".internal")):
        return None
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        return None
    if port is None:
        port_value = 443
    elif isinstance(port, int) and not isinstance(port, bool):
        port_value = port
    else:
        return None
    if port_value != 443:
        return None
    return normalized, port_value


def network_approval_request(method: str, params: dict[str, Any] | None) -> NetworkApprovalRequest | None:
    """Accept only destination-scoped public HTTPS approval payloads."""

    if not isinstance(params, dict):
        return None
    context: dict[str, Any] | None = None
    if method == "item/commandExecution/requestApproval":
        value = params.get("networkApprovalContext")
        if isinstance(value, dict):
            context = value
    if context is None:
        return None
    target = _public_https_host(context.get("host"), context.get("protocol"), context.get("port"))
    if target is None:
        return None
    thread_id = params.get("threadId")
    turn_id = params.get("turnId")
    if not isinstance(thread_id, str) or not isinstance(turn_id, str):
        return None
    host, port = target
    reason = params.get("reason") if isinstance(params.get("reason"), str) else "Codex requested this destination."
    cwd = params.get("cwd") if isinstance(params.get("cwd"), str) else ""
    return NetworkApprovalRequest(
        method=method,
        thread_id=thread_id,
        turn_id=turn_id,
        host=host,
        protocol="https",
        port=port,
        reason=reason[:500],
        cwd=cwd,
    )


class _InteractiveAsyncCodex(AsyncCodex):
    """Pinned SDK compatibility layer for owner-reviewed app-server approvals."""

    def __init__(self, approval_handler: Callable[[str, dict[str, Any] | None], dict[str, Any]]) -> None:
        if openai_codex.__version__ != PINNED_CODEX_VERSION:
            raise RuntimeError(
                f"Manual approval adapter requires openai-codex {PINNED_CODEX_VERSION}; "
                f"found {openai_codex.__version__}."
            )
        launcher = os.environ.get("CODEX_SAFE_LAUNCHER", "/usr/local/bin/codex-safe-launcher")
        super().__init__(
            CodexConfig(
                codex_bin=launcher,
                config_overrides=CODEX_PERMISSION_OVERRIDES,
            )
        )
        sync_client = getattr(getattr(self, "_client", None), "_sync", None)
        if sync_client is None or not hasattr(sync_client, "_approval_handler"):
            raise RuntimeError("Pinned Codex SDK approval interface is unavailable.")
        sync_client._approval_handler = approval_handler

    async def thread_start(  # type: ignore[override]
        self,
        *,
        approval_mode: str,
        cwd: str,
        ephemeral: bool,
        sandbox: None,
    ) -> AsyncThread:
        if approval_mode != TELEGRAM_APPROVAL_MODE or sandbox is not None:
            raise ValueError("Telegram Codex threads require the managed permissions profile.")
        await self._ensure_initialized()
        approval_policy = AskForApproval(
            root=GranularAskForApproval(
                granular=Granular(
                    mcp_elicitations=False,
                    request_permissions=False,
                    rules=False,
                    sandbox_approval=True,
                    skill_approval=False,
                )
            )
        )
        started = await self._client.thread_start(
            ThreadStartParams(
                approval_policy=approval_policy,
                approvals_reviewer=ApprovalsReviewer.user,
                cwd=cwd,
                developer_instructions=CODEX_CAPABILITY_INSTRUCTION,
                ephemeral=ephemeral,
            )
        )
        return AsyncThread(self, started.thread.id)


class CodexBackendError(RuntimeError):
    """A Codex failure with a concise response safe to show in Telegram."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class CodexBusyError(CodexBackendError):
    """The user already has a turn in progress."""


class CodexTurnDiscarded(RuntimeError):
    """The session was intentionally replaced while its turn was ending."""


@dataclass(frozen=True, slots=True)
class CodexTurnReservation:
    """Exclusive per-user admission spanning preparation and Codex execution."""

    user_id: int
    session: "CodexSession"


@dataclass(frozen=True, slots=True)
class CodexImageInput:
    """Validated in-memory image supplied to one Codex turn."""

    data: bytes
    media_type: str

    def __post_init__(self) -> None:
        if self.media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("unsupported image media type")


@dataclass(frozen=True, slots=True)
class CodexTurnResult:
    """Completed text plus workspace paths requested for Telegram delivery."""

    text: str
    cwd: Path
    image_paths: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()


@dataclass
class CodexSession:
    cwd: Path
    thread: Any
    active_turn: Any | None = None
    approval_callback: ApprovalCallback | None = field(default=None, repr=False)
    event_loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
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


def telegram_attachments_from_message(text: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Remove delivery markers and return at most four requested attachments."""

    image_paths: list[str] = []
    file_paths: list[str] = []
    attachment_count = 0

    def remove_marker(match: re.Match[str]) -> str:
        nonlocal attachment_count
        kind = match.group(1)
        path = match.group(2).strip()
        if path and attachment_count < MAX_TELEGRAM_ATTACHMENTS_PER_TURN:
            (image_paths if kind == "image" else file_paths).append(path)
            attachment_count += 1
        return ""

    visible = _TELEGRAM_ATTACHMENT_RE.sub(remove_marker, text).strip()
    if not visible:
        if image_paths and not file_paths:
            visible = "Here’s the image."
        elif file_paths and not image_paths:
            visible = "Here’s the file."
        else:
            visible = "Here are the requested attachments."
    return visible, tuple(image_paths), tuple(file_paths)


def telegram_images_from_message(text: str) -> tuple[str, tuple[str, ...]]:
    """Backward-compatible image-only view of Telegram delivery markers."""

    visible, image_paths, _ = telegram_attachments_from_message(text)
    return visible, image_paths


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
        self.client = client or _InteractiveAsyncCodex(self._handle_approval)
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
                approval_mode=TELEGRAM_APPROVAL_MODE,
                cwd=str(cwd),
                ephemeral=True,
                sandbox=None,
            )
        except Exception as exc:
            raise translate_codex_error(exc) from exc
        return CodexSession(cwd=cwd, thread=thread)

    def _handle_approval(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """Bridge the SDK reader thread to the owning Telegram event loop."""

        request = network_approval_request(method, params)
        if request is None:
            return _decline_approval(method)
        session = next(
            (
                candidate
                for candidate in self.sessions.values()
                if getattr(candidate.thread, "id", None) == request.thread_id
            ),
            None,
        )
        if session is None or session.approval_callback is None or session.event_loop is None:
            return request.response(False)
        try:
            future = asyncio.run_coroutine_threadsafe(session.approval_callback(request), session.event_loop)
            approved = bool(future.result(timeout=APPROVAL_TIMEOUT_SECONDS))
        except FutureTimeoutError:
            LOGGER.warning("Codex network approval expired")
            approved = False
        except Exception:
            LOGGER.warning("Codex network approval failed or expired", exc_info=True)
            approved = False
        return request.response(approved)

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

    async def reserve_turn(self, user_id: int, default_cwd: Path) -> CodexTurnReservation:
        """Reserve one user turn before any potentially expensive input preparation."""

        async with self._session_lock:
            session = self.sessions.get(user_id)
            if session is None:
                session = await self._start_thread(default_cwd.resolve())
                self.sessions[user_id] = session
            if session.active_turn is not None or session.turn_lock.locked():
                raise CodexBusyError("I’m still working on your previous request.")
            await session.turn_lock.acquire()
        return CodexTurnReservation(user_id=user_id, session=session)

    def release_turn(self, reservation: CodexTurnReservation) -> None:
        """Release a preparation reservation, including one invalidated by a reset."""

        if reservation.session.turn_lock.locked():
            reservation.session.turn_lock.release()

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
        image: CodexImageInput | None = None,
        on_status: StatusCallback | None = None,
        on_approval: ApprovalCallback | None = None,
        reservation: CodexTurnReservation | None = None,
    ) -> CodexTurnResult:
        owns_reservation = reservation is None
        if reservation is None:
            reservation = await self.reserve_turn(user_id, default_cwd)
        elif reservation.user_id != user_id:
            raise ValueError("Codex turn reservation belongs to another user")
        session = reservation.session
        if self.sessions.get(user_id) is not session:
            if owns_reservation:
                self.release_turn(reservation)
            raise CodexTurnDiscarded()
        session.approval_callback = on_approval
        session.event_loop = asyncio.get_running_loop()
        completed_items: list[Any] = []
        try:
            turn_input: Any = text
            if image is not None:
                encoded = base64.b64encode(image.data).decode("ascii")
                turn_input = [
                    TextInput(text),
                    SDKImageInput(f"data:{image.media_type};base64,{encoded}"),
                ]
            handle = await session.thread.turn(turn_input)
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
                        root = getattr(item, "root", item)
                        if getattr(root, "type", None) == "mcpToolCall":
                            LOGGER.info(
                                "Codex MCP tool completed server=%s tool=%s status=%s",
                                getattr(root, "server", "unknown"),
                                getattr(root, "tool", getattr(root, "name", "unknown")),
                                getattr(root, "status", "unknown"),
                            )
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
            session.approval_callback = None
            session.event_loop = None
            if owns_reservation:
                self.release_turn(reservation)

        if self.sessions.get(user_id) is not session:
            raise CodexTurnDiscarded()
        response = final_message_from_items(completed_items)
        if response is None:
            raise CodexBackendError("Codex finished without returning a response. Please try again.")
        visible_text, image_paths, file_paths = telegram_attachments_from_message(response)
        return CodexTurnResult(
            text=visible_text,
            cwd=session.cwd,
            image_paths=image_paths,
            file_paths=file_paths,
        )
