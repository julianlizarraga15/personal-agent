"""Durable request and status protocol for self-deployment."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator


ACTIVE_STATUSES = {"queued", "building", "restarting", "verifying", "awaiting_report"}
TERMINAL_REPORT_STATUSES = {"awaiting_report", "rollback_completed", "rollback_failed", "failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentBusy(RuntimeError):
    """A deployment request is already queued or running."""


class DeploymentManifest:
    def __init__(self, state_dir: Path | str = "/workspace/.personal-agent-state") -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "deployment.json"

    def read(self) -> dict | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"status": "corrupt", "path": str(self.path)}
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return {"status": "corrupt", "path": str(self.path)}

    def write(self, **values: object) -> dict:
        current = self.read() or {}
        current.update(values)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return current

    def transition(self, status: str, **values: object) -> dict:
        return self.write(status=status, updated_at=utc_now(), **values)


@contextmanager
def deployment_lock(state_dir: Path | str, *, blocking: bool = False) -> Iterator[None]:
    """Hold an automatically-released cross-process deployment lock."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock_file = (directory / "deployment.lock").open("a+", encoding="utf-8")
    operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(lock_file.fileno(), operation)
        except BlockingIOError as exc:
            raise DeploymentBusy("deployment already in progress; inspect /pending") from exc
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


class DeploymentQueue:
    def __init__(self, state_dir: Path | str = "/workspace/.personal-agent-state", *, heartbeat_max_age: int = 15) -> None:
        self.state_dir = Path(state_dir)
        self.request_path = self.state_dir / "deployment-request.json"
        self.active_path = self.state_dir / "deployment-active.json"
        self.heartbeat_path = self.state_dir / "deployer-heartbeat"
        self.manifest = DeploymentManifest(self.state_dir)
        self.heartbeat_max_age = heartbeat_max_age

    def deployer_available(self, now: float | None = None) -> bool:
        try:
            age = (time.time() if now is None else now) - self.heartbeat_path.stat().st_mtime
            return age <= self.heartbeat_max_age
        except FileNotFoundError:
            return False

    def enqueue(self, commit: str) -> dict:
        current = self.manifest.read()
        if self.request_path.exists() or self.active_path.exists() or (current and current.get("status") in ACTIVE_STATUSES):
            raise DeploymentBusy("deployment already queued or running; inspect /pending")
        if not self.deployer_available():
            raise RuntimeError("deployment controller is unavailable; start the deployer service and retry")
        with deployment_lock(self.state_dir):
            current = self.manifest.read()
            if self.request_path.exists() or self.active_path.exists() or (current and current.get("status") in ACTIVE_STATUSES):
                raise DeploymentBusy("deployment already queued or running; inspect /pending")
            request = {"deployment_id": uuid.uuid4().hex[:12], "commit": commit, "requested_at": utc_now()}
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.request_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(request, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, self.request_path)
            return self.manifest.write(**request, status="queued", updated_at=utc_now(), error=None)

    def claim(self) -> dict | None:
        if not self.active_path.exists():
            if not self.request_path.exists():
                return None
            os.replace(self.request_path, self.active_path)
        try:
            value = json.loads(self.active_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not {"deployment_id", "commit"} <= value.keys():
                raise ValueError("invalid deployment request")
            return value
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.active_path.replace(self.active_path.with_suffix(".invalid"))
            self.manifest.transition("failed", error=f"invalid deployment request: {exc}")
            return None

    def finish(self) -> None:
        self.active_path.unlink(missing_ok=True)

    def heartbeat(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.touch()


def _run(command: list[str], runner: Callable[..., subprocess.CompletedProcess], timeout: int = 600) -> subprocess.CompletedProcess:
    return runner(command, capture_output=True, text=True, timeout=timeout)


def run_deployment(
    request: dict,
    *,
    compose_file: str,
    project_name: str,
    bot_image: str,
    repository: str = "/workspace/personal-agent",
    state_dir: str = "/workspace/.personal-agent-state",
    docker_compose: str = "docker-compose",
    docker: str = "docker",
    readiness_timeout: int = 60,
    stability_seconds: int = 10,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """Build and verify the bot while retaining rollback authority."""
    if compose_file != "/workspace/personal-agent/docker-compose.yml":
        raise RuntimeError("refusing an unexpected Compose file")
    if not bot_image.startswith("personal-agent-bot:"):
        raise RuntimeError("refusing an unexpected bot image")
    manifest = DeploymentManifest(state_dir)
    commit = str(request["commit"])
    head = _run(["git", "-C", repository, "rev-parse", "HEAD"], runner, 30)
    dirty = _run(["git", "-C", repository, "status", "--porcelain"], runner, 30)
    if head.returncode or head.stdout.strip() != commit or dirty.returncode or dirty.stdout.strip():
        return manifest.transition("failed", error="queued commit no longer matches a clean self-checkout")

    inspect_previous = _run([docker, "image", "inspect", "--format", "{{.Id}}", bot_image], runner, 60)
    previous_image = inspect_previous.stdout.strip() if inspect_previous.returncode == 0 else None
    manifest.transition("building", previous_image=previous_image, rollback_image=None)
    rollback_image = None
    if previous_image:
        rollback_image = f"personal-agent-bot:rollback-{request['deployment_id']}"
        tagged = _run([docker, "tag", previous_image, rollback_image], runner, 60)
        if tagged.returncode:
            return manifest.transition("failed", error="could not preserve the previous bot image")
        manifest.transition("building", rollback_image=rollback_image)

    build = _run([docker_compose, "-f", compose_file, "-p", project_name, "build", "bot"], runner)
    if build.returncode:
        return manifest.transition("failed", error=(build.stdout + build.stderr)[-4000:])
    manifest.transition("restarting")
    recreate = _run([docker_compose, "-f", compose_file, "-p", project_name, "up", "-d", "--no-build", "--force-recreate", "bot"], runner)
    if recreate.returncode:
        return _rollback(manifest, compose_file, project_name, bot_image, rollback_image, docker_compose, docker, runner, sleeper, readiness_timeout, "recreate failed")
    manifest.transition("verifying")
    if _wait_ready(compose_file, project_name, docker_compose, docker, runner, sleeper, readiness_timeout, stability_seconds):
        return manifest.transition("awaiting_report", verified_at=utc_now())
    return _rollback(manifest, compose_file, project_name, bot_image, rollback_image, docker_compose, docker, runner, sleeper, readiness_timeout, "bot failed readiness verification")


def _wait_ready(compose_file: str, project_name: str, docker_compose: str, docker: str,
                runner: Callable[..., subprocess.CompletedProcess], sleeper: Callable[[float], None],
                timeout: int, stability_seconds: int) -> bool:
    stable = 0
    for _ in range(timeout):
        service = _run([docker_compose, "-f", compose_file, "-p", project_name, "ps", "-q", "bot"], runner, 30)
        container_id = service.stdout.strip()
        if service.returncode == 0 and container_id:
            state = _run([docker, "inspect", "--format", "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container_id], runner, 30)
            if state.returncode == 0 and state.stdout.strip() in {"true healthy", "true none"}:
                stable += 1
                if stable >= stability_seconds:
                    return True
            else:
                stable = 0
        sleeper(1)
    return False


def _rollback(manifest: DeploymentManifest, compose_file: str, project_name: str, bot_image: str,
              rollback_image: str | None, docker_compose: str, docker: str,
              runner: Callable[..., subprocess.CompletedProcess], sleeper: Callable[[float], None],
              timeout: int, reason: str) -> dict:
    if not rollback_image:
        return manifest.transition("failed", error=reason, rollback="unavailable")
    restored = _run([docker, "tag", rollback_image, bot_image], runner, 60)
    recreated = _run([docker_compose, "-f", compose_file, "-p", project_name, "up", "-d", "--no-build", "--force-recreate", "bot"], runner)
    if restored.returncode or recreated.returncode:
        return manifest.transition("rollback_failed", error=reason)
    if not _wait_ready(compose_file, project_name, docker_compose, docker, runner, sleeper, timeout, 3):
        return manifest.transition("rollback_failed", error=f"{reason}; rollback bot failed readiness")
    return manifest.transition("rollback_completed", error=reason, rolled_back_at=utc_now())
