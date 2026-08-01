"""Repository-scoped, approval-gated Git publication gateway."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
from typing import Any


DEFAULT_SOCKET_PATH = Path("/run/git-publish.sock")
DEFAULT_SECRETS_ROOT = Path("/git-publish-secrets")
DEFAULT_KEY_PATH = Path("/git-publish-secrets/deploy-key")
DEFAULT_KNOWN_HOSTS_PATH = Path("/git-publish-secrets/known_hosts")
DEFAULT_BWRAP_PATH = Path("/usr/bin/bwrap")
MAX_REQUEST_BYTES = 4 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
MAX_GIT_OUTPUT_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
PUSH_TIMEOUT_SECONDS = 90
BUNDLE_TIMEOUT_SECONDS = 90
GATEWAY_TIMEOUT_SECONDS = 720
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_BRANCH_RE = re.compile(r"^(?![./])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,128}(?<![./])$")
_GITHUB_SSH_RE = re.compile(r"^git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$")
_DANGEROUS_CONFIG_SECTION_RE = re.compile(r"^\s*\[(?:include(?:if)?|url)(?:\s|\])", re.IGNORECASE | re.MULTILINE)


class GitPublishError(RuntimeError):
    """A stable publication error safe to return across the credential boundary."""


@dataclass(frozen=True, slots=True)
class GitPublishApproval:
    repository: str
    remote: str
    branch: str
    commit: str

    @property
    def destination(self) -> str:
        return f"{self.remote} ({self.branch})"


ApprovalCallback = Callable[[GitPublishApproval], Awaitable[bool]]


def validate_request(value: object) -> str:
    if not isinstance(value, dict) or set(value) != {"commit"}:
        raise GitPublishError("Malformed Git publication request.")
    commit = value.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise GitPublishError("Git publication requires the exact 40-character local commit ID.")
    return commit


def _regular_file(path: Path, description: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        mode = path.lstat().st_mode
    except OSError as exc:
        raise GitPublishError(f"{description} is not configured.") from exc
    if stat.S_ISLNK(mode) or not resolved.is_file():
        raise GitPublishError(f"{description} is not configured.")
    return resolved


def _validated_repository(repository: Path, workspace: Path) -> Path:
    try:
        root = repository.resolve(strict=True)
        workspace_root = workspace.resolve(strict=True)
    except OSError as exc:
        raise GitPublishError("The configured publication repository is unavailable.") from exc
    if root == workspace_root or not root.is_relative_to(workspace_root) or not root.is_dir():
        raise GitPublishError("The configured publication repository is outside the workspace.")
    git_dir = root / ".git"
    try:
        mode = git_dir.lstat().st_mode
    except OSError as exc:
        raise GitPublishError("The configured publication repository is not a Git checkout.") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise GitPublishError("The configured publication repository must use an internal .git directory.")
    config = _regular_file(git_dir / "config", "The repository Git configuration")
    try:
        if config.stat().st_size > 1024 * 1024:
            raise GitPublishError("The repository Git configuration is invalid.")
        config_text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GitPublishError("The repository Git configuration is invalid.") from exc
    if _DANGEROUS_CONFIG_SECTION_RE.search(config_text):
        raise GitPublishError("The repository Git configuration contains disallowed URL or include rules.")
    if (git_dir / "objects" / "info" / "alternates").exists():
        raise GitPublishError("Git object alternates are not allowed for publication.")
    return root


def _validated_secrets(
    secrets_root: Path,
    key_path: Path,
    known_hosts_path: Path,
    workspace: Path,
) -> tuple[Path, Path, Path]:
    try:
        root = secrets_root.resolve(strict=True)
        workspace_root = workspace.resolve(strict=True)
        mode = secrets_root.lstat().st_mode
    except OSError as exc:
        raise GitPublishError("The Git publication secrets directory is not configured.") from exc
    if stat.S_ISLNK(mode) or not root.is_dir():
        raise GitPublishError("The Git publication secrets directory is not configured.")
    if root == workspace_root or root.is_relative_to(workspace_root) or workspace_root.is_relative_to(root):
        raise GitPublishError("Git publication secrets must be outside the workspace.")
    key = _regular_file(key_path, "The Git publication deploy key")
    known_hosts = _regular_file(known_hosts_path, "The Git publication known-hosts file")
    if key.parent != root or known_hosts.parent != root:
        raise GitPublishError("Git publication secrets must be direct files in the configured secrets directory.")
    if key.stat().st_mode & 0o077:
        raise GitPublishError("The Git publication deploy key must have owner-only permissions.")
    return root, key, known_hosts


def _base_git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp/git-publish-home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git_environment(key_path: Path, known_hosts_path: Path) -> dict[str, str]:
    ssh_command = shlex.join(
        [
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-i",
            str(key_path),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "ConnectTimeout=15",
        ]
    )
    return {**_base_git_environment(), "GIT_SSH_COMMAND": ssh_command}


def _git_command(args: list[str]) -> list[str]:
    return [
        "/usr/bin/git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        *args,
    ]


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 20,
) -> str:
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            size = output.tell()
            if size > MAX_GIT_OUTPUT_BYTES:
                raise GitPublishError("Git publication command returned too much output.")
            output.seek(0)
            raw = output.read()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitPublishError("Git publication command failed.") from exc
    if result.returncode != 0:
        raise GitPublishError("Git publication command failed.")
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise GitPublishError("Git publication command returned invalid output.") from exc


def _run_git(repository: Path, args: list[str], *, env: dict[str, str], timeout: int = 20) -> str:
    return _run_process(_git_command(args), cwd=repository, env=env, timeout=timeout)


def _source_sandbox_command(
    repository: Path,
    args: list[str],
    *,
    secrets_root: Path,
    stage: Path | None = None,
) -> list[str]:
    command = [
        str(DEFAULT_BWRAP_PATH),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
    ]
    for protected in (Path("/codex-home"), Path("/trace-state"), secrets_root):
        if protected.exists() and not protected.is_relative_to(Path("/tmp")) and not protected.is_relative_to(Path("/run")):
            command.extend(("--tmpfs", str(protected)))
    if stage is not None:
        command.extend(("--dir", "/tmp/git-publish-stage"))
        command.extend(("--bind", str(stage), "/tmp/git-publish-stage"))
    command.extend(("--chdir", str(repository), "--clearenv"))
    for name, value in _base_git_environment().items():
        command.extend(("--setenv", name, value))
    command.extend(_git_command(args))
    return command


def _run_source_git(
    repository: Path,
    args: list[str],
    *,
    secrets_root: Path,
    stage: Path | None = None,
    timeout: int = 20,
) -> str:
    command = _source_sandbox_command(repository, args, secrets_root=secrets_root, stage=stage)
    return _run_process(command, cwd=Path("/"), env={"PATH": "/usr/bin:/bin"}, timeout=timeout)


def _inspect_source(repository: Path, secrets_root: Path) -> tuple[str, str]:
    head = _run_source_git(repository, ["rev-parse", "--verify", "HEAD"], secrets_root=secrets_root)
    status = _run_source_git(
        repository,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        secrets_root=secrets_root,
    )
    return head, status


def _create_source_bundle(repository: Path, secrets_root: Path, destination: Path) -> None:
    stage = destination.parent
    _run_source_git(
        repository,
        ["bundle", "create", f"/tmp/git-publish-stage/{destination.name}", "HEAD"],
        secrets_root=secrets_root,
        stage=stage,
        timeout=BUNDLE_TIMEOUT_SECONDS,
    )
    bundle = _regular_file(destination, "The Git publication bundle")
    if bundle.stat().st_size > MAX_BUNDLE_BYTES:
        raise GitPublishError("The Git publication bundle exceeds the size limit.")


def _publish_bundle(
    bundle: Path,
    expected_commit: str,
    remote: str,
    branch: str,
    key_path: Path,
    known_hosts_path: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="git-publish-safe-") as directory:
        root = Path(directory)
        safe_repository = root / "repository.git"
        plain_env = _base_git_environment()
        _run_git(
            root,
            ["clone", "--bare", "--no-local", str(bundle), str(safe_repository)],
            env=plain_env,
            timeout=BUNDLE_TIMEOUT_SECONDS,
        )
        bundled_head = _run_git(
            safe_repository,
            ["rev-parse", "--verify", "HEAD"],
            env=plain_env,
        )
        if bundled_head != expected_commit:
            raise GitPublishError("The repository changed while publication was prepared.")
        _run_git(
            safe_repository,
            ["push", "--porcelain", "--no-verify", remote, f"{expected_commit}:refs/heads/{branch}"],
            env=_git_environment(key_path, known_hosts_path),
            timeout=PUSH_TIMEOUT_SECONDS,
        )


class GitPublishGateway:
    """Own the deploy key and publish one configured repository after approval."""

    def __init__(
        self,
        repository: str | Path | None,
        remote: str | None,
        branch: str | None,
        *,
        workspace: str | Path = "/workspace",
        secrets_root: str | Path = DEFAULT_SECRETS_ROOT,
        key_path: str | Path = DEFAULT_KEY_PATH,
        known_hosts_path: str | Path = DEFAULT_KNOWN_HOSTS_PATH,
        socket_path: Path = DEFAULT_SOCKET_PATH,
    ) -> None:
        self.repository = Path(repository) if repository else None
        self.remote = (remote or "").strip()
        self.branch = (branch or "main").strip()
        self.workspace = Path(workspace)
        self.secrets_root = Path(secrets_root)
        self.key_path = Path(key_path)
        self.known_hosts_path = Path(known_hosts_path)
        self.socket_path = socket_path
        self.server: asyncio.AbstractServer | None = None
        self._approval_callback: ApprovalCallback | None = None
        self._approval_lease: object | None = None
        self._push_lock = asyncio.Lock()

    def bind_approval(self, callback: ApprovalCallback) -> object | None:
        if self._approval_callback is not None:
            return None
        lease = object()
        self._approval_callback = callback
        self._approval_lease = lease
        return lease

    def unbind_approval(self, lease: object | None) -> None:
        if lease is not None and lease is self._approval_lease:
            self._approval_callback = None
            self._approval_lease = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            mode = self.socket_path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(mode):
                raise RuntimeError("Git publication socket path is not a socket.")
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

    def _configuration(self) -> tuple[Path, Path, Path, Path]:
        if self.repository is None or not self.remote:
            raise GitPublishError("Git publication is not configured.")
        if not _GITHUB_SSH_RE.fullmatch(self.remote):
            raise GitPublishError("The configured publication remote must be one GitHub SSH repository.")
        if not _BRANCH_RE.fullmatch(self.branch):
            raise GitPublishError("The configured publication branch is invalid.")
        repository = _validated_repository(self.repository, self.workspace)
        secrets_root, key_path, known_hosts_path = _validated_secrets(
            self.secrets_root,
            self.key_path,
            self.known_hosts_path,
            self.workspace,
        )
        return repository, secrets_root, key_path, known_hosts_path

    async def publish(self, expected_commit: str) -> dict[str, str]:
        async with self._push_lock:
            repository, secrets_root, key_path, known_hosts_path = self._configuration()
            head, status = await asyncio.to_thread(_inspect_source, repository, secrets_root)
            if head != expected_commit:
                raise GitPublishError("The repository changed after publication was requested.")
            if status:
                raise GitPublishError("Commit all repository changes before publication.")
            callback = self._approval_callback
            if callback is None:
                raise GitPublishError("Git publication is available only during an active owner turn.")
            approval = GitPublishApproval(str(repository), self.remote, self.branch, head)
            if not await callback(approval):
                raise GitPublishError("Git publication was not approved.")
            current, current_status = await asyncio.to_thread(_inspect_source, repository, secrets_root)
            if current != head:
                raise GitPublishError("The repository changed after publication approval.")
            if current_status:
                raise GitPublishError("The repository became dirty after publication approval.")
            with tempfile.TemporaryDirectory(prefix="git-publish-bundle-") as directory:
                bundle = Path(directory) / "repository.bundle"
                await asyncio.to_thread(_create_source_bundle, repository, secrets_root, bundle)
                await asyncio.to_thread(
                    _publish_bundle,
                    bundle,
                    head,
                    self.remote,
                    self.branch,
                    key_path,
                    known_hosts_path,
                )
            return {"repository": str(repository), "remote": self.remote, "branch": self.branch, "commit": head}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                raise GitPublishError("Malformed Git publication request.")
            request = json.loads(raw)
            commit = validate_request(request)
            response: dict[str, Any] = {"ok": True, "data": await self.publish(commit)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = {"ok": False, "error": "Malformed Git publication request."}
        except asyncio.TimeoutError:
            response = {"ok": False, "error": "Git publication timed out."}
        except GitPublishError as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            response = {"ok": False, "error": "Git publication failed."}
        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"ok":false,"error":"Git publication response exceeded the size limit."}\n'
        try:
            writer.write(encoded)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()


async def request_gateway(request: dict[str, Any], socket_path: Path = DEFAULT_SOCKET_PATH) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path)), timeout=5)
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError("Git publication gateway is unavailable.") from exc
    try:
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_REQUEST_BYTES:
            raise RuntimeError("Git publication request is too large.")
        writer.write(encoded)
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=GATEWAY_TIMEOUT_SECONDS)
        if not raw or len(raw) > MAX_RESPONSE_BYTES or not raw.endswith(b"\n"):
            raise RuntimeError("Git publication gateway returned an invalid response.")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("Git publication gateway returned an invalid response.")
        return response
    except (OSError, asyncio.TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Git publication gateway request failed.") from exc
    finally:
        writer.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await writer.wait_closed()
