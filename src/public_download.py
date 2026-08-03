"""Approval-gated downloader for public HTTPS files."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import ssl
import stat
import time
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit


DEFAULT_SOCKET_PATH = Path("/run/public-download.sock")
DEFAULT_MAX_BYTES = 50_000_000
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
APPROVAL_TIMEOUT_SECONDS = 305
UPSTREAM_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 90.0
MAX_REDIRECTS = 5
MAX_URL_BYTES = 2048
MAX_DESTINATION_BYTES = 512

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_SENSITIVE_QUERY_RE = re.compile(
    r"(?:^|[-_])(?:auth|authorization|credential|key|password|secret|sig|signature|token)(?:$|[-_])",
    re.IGNORECASE,
)
_CREDENTIAL_NAME_RE = re.compile(
    r"(?:^|[._-])(?:auth|credential|credentials|secret|secrets|token|tokens)(?:[._-]|$)",
    re.IGNORECASE,
)
_PROTECTED_FILENAMES = {
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "application_default_credentials.json",
    "auth.json",
    "credential.json",
    "credentials.json",
    "deploy-key",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
    "secret.json",
    "secrets.json",
    "token.json",
}
_PROTECTED_SUFFIXES = {".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
_PROTECTED_ROOTS = {".agents", ".codex", ".git"}


class PublicDownloadError(RuntimeError):
    """A stable error safe to return across the download boundary."""


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    url: str
    destination: str


@dataclass(frozen=True, slots=True)
class DownloadApproval:
    url: str
    destination: str
    max_bytes: int

    @property
    def display_destination(self) -> str:
        return f"{self.url} -> {self.destination}"


ApprovalCallback = Callable[[DownloadApproval], Awaitable[bool]]
DownloadCallback = Callable[[str, int], tuple[bytes, str, str]]


def validate_url(value: object) -> str:
    """Return a normalized credential-free public HTTPS URL."""

    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_URL_BYTES:
        raise PublicDownloadError("Download URL is invalid or too long.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value) or "\\" in value:
        raise PublicDownloadError("Download URL is invalid.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise PublicDownloadError("Only credential-free HTTPS URLs are allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PublicDownloadError("Download URL has an invalid port.") from exc
    if port not in (None, 443):
        raise PublicDownloadError("Only HTTPS port 443 is allowed.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not _HOSTNAME_RE.fullmatch(host):
        raise PublicDownloadError("Download URL must use a public DNS hostname.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise PublicDownloadError("Local download destinations are not allowed.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise PublicDownloadError("IP-literal download destinations are not allowed.")
    if parsed.fragment:
        raise PublicDownloadError("Download URLs must not contain fragments.")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if _SENSITIVE_QUERY_RE.search(key):
            raise PublicDownloadError("Credential-bearing download URLs are not allowed.")
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


def validate_destination(value: object) -> str:
    """Return one safe relative POSIX destination beneath an active project."""

    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_DESTINATION_BYTES:
        raise PublicDownloadError("Download destination is invalid or too long.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value) or "\\" in value:
        raise PublicDownloadError("Download destination is invalid.")
    path = PurePosixPath(value)
    if path.is_absolute() or value.endswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise PublicDownloadError("Download destination must be a file path inside the active project.")
    if path.parts[0].lower() in _PROTECTED_ROOTS:
        raise PublicDownloadError("Download destination is protected.")
    for part in path.parts:
        lowered = part.lower()
        if lowered == ".env" or (lowered.startswith(".env.") and lowered != ".env.example"):
            raise PublicDownloadError("Download destination is protected.")
    leaf = path.name.lower()
    if (
        leaf in _PROTECTED_FILENAMES
        or Path(leaf).suffix in _PROTECTED_SUFFIXES
        or _CREDENTIAL_NAME_RE.search(leaf)
    ):
        raise PublicDownloadError("Credential-like download destinations are not allowed.")
    return path.as_posix()


def validate_request(value: object) -> DownloadRequest:
    if not isinstance(value, dict) or set(value) != {"url", "destination"}:
        raise PublicDownloadError("Malformed public download request.")
    return DownloadRequest(validate_url(value.get("url")), validate_destination(value.get("destination")))


def _resolve_public_address(host: str) -> str:
    """Resolve once, reject every non-public answer, and return a pinned address."""

    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise PublicDownloadError("Download hostname could not be resolved.") from exc
    addresses: list[str] = []
    for answer in answers:
        address = answer[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise PublicDownloadError("Download hostname could not be resolved.")
    try:
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except ValueError as exc:
        raise PublicDownloadError("Download hostname returned an invalid address.") from exc
    if any(
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in parsed
    ):
        raise PublicDownloadError("Download hostname resolved to a non-public address.")
    return addresses[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP peer is fixed after validated DNS resolution."""

    def __init__(self, host: str, address: str, *, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _download(url: str, max_bytes: int) -> tuple[bytes, str, str]:
    """Fetch one bounded public file, allowing only same-host HTTPS redirects."""

    current = validate_url(url)
    approved_host = urlsplit(current).hostname
    deadline = time.monotonic() + DOWNLOAD_TOTAL_TIMEOUT_SECONDS
    for redirect_count in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        host = parsed.hostname or ""
        address = _resolve_public_address(host)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PublicDownloadError("Download timed out.")
        connection = _PinnedHTTPSConnection(
            host,
            address,
            timeout=min(UPSTREAM_TIMEOUT_SECONDS, remaining),
        )
        try:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request(
                "GET",
                target,
                headers={"Accept": "*/*", "User-Agent": "personal-agent/1.0", "Connection": "close"},
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect_count >= MAX_REDIRECTS:
                    raise PublicDownloadError("Download redirect limit was exceeded.")
                redirected = validate_url(urljoin(current, location))
                if urlsplit(redirected).hostname != approved_host:
                    raise PublicDownloadError(
                        "Download redirected to another host; request that final URL separately."
                    )
                current = redirected
                continue
            if response.status < 200 or response.status >= 300:
                raise PublicDownloadError(f"Download failed with HTTP {response.status}.")
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise PublicDownloadError("Download returned an invalid size.") from exc
                if declared_size < 0 or declared_size > max_bytes:
                    raise PublicDownloadError("Download exceeded the size limit.")
            chunks: list[bytes] = []
            received = 0
            while received <= max_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PublicDownloadError("Download timed out.")
                if connection.sock is not None:
                    connection.sock.settimeout(min(UPSTREAM_TIMEOUT_SECONDS, remaining))
                chunk = response.read(min(64 * 1024, max_bytes + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
            if received > max_bytes:
                raise PublicDownloadError("Download exceeded the size limit.")
            body = b"".join(chunks)
            content_type = (response.getheader("Content-Type") or "application/octet-stream").split(";", 1)[0]
            return body, current, content_type[:255]
        except (TimeoutError, socket.timeout) as exc:
            raise PublicDownloadError("Download timed out.") from exc
        except PublicDownloadError:
            raise
        except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
            raise PublicDownloadError("Public HTTPS download failed.") from exc
        finally:
            connection.close()
    raise PublicDownloadError("Download redirect limit was exceeded.")


def _open_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise PublicDownloadError("Download destination directory is unsafe or unavailable.") from exc


def _store(project: Path, destination: str, body: bytes) -> None:
    """Atomically store bytes through non-symlink directory descriptors."""

    parts = PurePosixPath(validate_destination(destination)).parts
    project_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_fds: list[int] = []
    temporary_name: str | None = None
    try:
        parent_fd = project_fd
        for part in parts[:-1]:
            parent_fd = _open_directory(parent_fd, part)
            directory_fds.append(parent_fd)
        filename = parts[-1]
        try:
            existing = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISREG(existing.st_mode):
                raise PublicDownloadError("Download destination already exists; choose a new path.")
            raise PublicDownloadError("Download destination is not a regular file.")
        temporary_name = f".{filename}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        file_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
        with os.fdopen(file_fd, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                temporary_name,
                filename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise PublicDownloadError("Download destination already exists; choose a new path.") from exc
        os.unlink(temporary_name, dir_fd=parent_fd)
        temporary_name = None
    except PublicDownloadError:
        raise
    except OSError as exc:
        raise PublicDownloadError("Downloaded file could not be saved.") from exc
    finally:
        if temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=directory_fds[-1] if directory_fds else project_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)
        os.close(project_fd)


class PublicDownloadGateway:
    """Local gateway that binds one exact download approval to an active project turn."""

    def __init__(
        self,
        *,
        socket_path: Path = DEFAULT_SOCKET_PATH,
        workspace: str | Path = "/workspace",
        max_bytes: int = DEFAULT_MAX_BYTES,
        downloader: DownloadCallback = _download,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= DEFAULT_MAX_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {DEFAULT_MAX_BYTES}")
        self.socket_path = socket_path
        self.workspace = Path(workspace).resolve()
        self.max_bytes = max_bytes
        self.downloader = downloader
        self.server: asyncio.AbstractServer | None = None
        self._project: Path | None = None
        self._approval_callback: ApprovalCallback | None = None
        self._lease: object | None = None

    def bind_turn(self, project: Path, callback: ApprovalCallback) -> object | None:
        if self._project is not None or self._approval_callback is not None:
            return None
        resolved = project.resolve()
        if not resolved.is_relative_to(self.workspace) or not resolved.is_dir():
            return None
        lease = object()
        self._project = resolved
        self._approval_callback = callback
        self._lease = lease
        return lease

    def unbind_turn(self, lease: object | None) -> None:
        if lease is not None and lease is self._lease:
            self._project = None
            self._approval_callback = None
            self._lease = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            mode = self.socket_path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(mode):
                raise RuntimeError("Public download socket path is not a socket.")
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

    async def download(self, request: DownloadRequest) -> dict[str, Any]:
        project = self._project
        callback = self._approval_callback
        if project is None or callback is None:
            raise PublicDownloadError("Public downloads are available only during an active owner turn.")
        approval = DownloadApproval(request.url, request.destination, self.max_bytes)
        if not await callback(approval):
            raise PublicDownloadError("Public download was rejected or approval expired.")
        if project is not self._project or callback is not self._approval_callback:
            raise PublicDownloadError("Public download turn changed after approval.")
        body, final_url, content_type = await asyncio.wait_for(
            asyncio.to_thread(self.downloader, request.url, self.max_bytes),
            timeout=DOWNLOAD_TOTAL_TIMEOUT_SECONDS + 5,
        )
        if project is not self._project or callback is not self._approval_callback:
            raise PublicDownloadError("Public download turn changed before the file was saved.")
        await asyncio.to_thread(_store, project, request.destination, body)
        return {
            "url": final_url,
            "path": request.destination,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": content_type,
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                raise PublicDownloadError("Malformed public download request.")
            request = validate_request(json.loads(raw))
            response: dict[str, Any] = {"ok": True, "data": await self.download(request)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = {"ok": False, "error": "Malformed public download request."}
        except asyncio.TimeoutError:
            response = {"ok": False, "error": "Public download timed out."}
        except PublicDownloadError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            response = {"ok": False, "error": "Public download failed."}
        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"ok":false,"error":"Public download response exceeded the size limit."}\n'
        try:
            writer.write(encoded)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()


async def request_gateway(
    request: dict[str, Any], socket_path: Path = DEFAULT_SOCKET_PATH
) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path)), timeout=5)
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError("Public download gateway is unavailable.") from exc
    try:
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_REQUEST_BYTES:
            raise RuntimeError("Public download request is too large.")
        writer.write(encoded)
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=APPROVAL_TIMEOUT_SECONDS + 200)
        if not raw or len(raw) > MAX_RESPONSE_BYTES or not raw.endswith(b"\n"):
            raise RuntimeError("Public download gateway returned an invalid response.")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("Public download gateway returned an invalid response.")
        return response
    except (OSError, asyncio.TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Public download gateway request failed.") from exc
    finally:
        writer.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await writer.wait_closed()
