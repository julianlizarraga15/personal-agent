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

from owner_trace import TraceRecorder


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

    def enqueue(self, commit: str, turn_id: str | None = None) -> dict:
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
            if turn_id is not None:
                request["turn_id"] = turn_id
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.request_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(request, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, self.request_path)
            return self.manifest.write(
                **request,
                status="queued",
                updated_at=utc_now(),
                error=None,
                previous_image=None,
                rollback_image=None,
                rollback=None,
                rolled_back_at=None,
                verified_at=None,
                reported_at=None,
                recovered_from=None,
            )

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
    trace: TraceRecorder | None = None,
) -> dict:
    """Build and verify the bot while retaining rollback authority."""
    if compose_file != "/workspace/personal-agent/docker-compose.yml":
        raise RuntimeError("refusing an unexpected Compose file")
    if not bot_image.startswith("personal-agent-bot:"):
        raise RuntimeError("refusing an unexpected bot image")
    manifest = DeploymentManifest(state_dir)
    def execute(stage: str, command: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        if trace is not None:
            trace.event("deployment.controller_command.started", {"stage": stage, "command": command})
        started = time.monotonic()
        result = _run(command, runner, timeout)
        if trace is not None:
            trace.event("deployment.controller_command.finished", {"stage": stage, "command": command, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "elapsed_seconds": time.monotonic() - started})
        return result

    commit = str(request["commit"])
    head = execute("verify head", ["git", "-C", repository, "rev-parse", "HEAD"], 30)
    dirty = execute("verify clean", ["git", "-C", repository, "status", "--porcelain"], 30)
    if head.returncode or head.stdout.strip() != commit or dirty.returncode or dirty.stdout.strip():
        return manifest.transition("failed", error="queued commit no longer matches a clean self-checkout")

    inspect_previous = execute("inspect previous image", [docker, "image", "inspect", "--format", "{{.Id}}", bot_image], 60)
    previous_image = inspect_previous.stdout.strip() if inspect_previous.returncode == 0 else None
    manifest.transition("building", previous_image=previous_image, rollback_image=None)
    if trace is not None:
        trace.event("deployment.stage", {"stage": "building", "previous_image": previous_image})
    rollback_image = None
    if previous_image:
        rollback_image = f"personal-agent-bot:rollback-{request['deployment_id']}"
        tagged = execute("preserve rollback image", [docker, "tag", previous_image, rollback_image], 60)
        if tagged.returncode:
            return manifest.transition("failed", error="could not preserve the previous bot image")
        manifest.transition("building", rollback_image=rollback_image)

    build = execute("build", [docker_compose, "-f", compose_file, "-p", project_name, "build", "bot"])
    if build.returncode:
        return manifest.transition("failed", error=(build.stdout + build.stderr)[-4000:])
    manifest.transition("restarting")
    if trace is not None:
        trace.event("deployment.stage", {"stage": "restarting"})
    recreate = execute("restart", [docker_compose, "-f", compose_file, "-p", project_name, "up", "-d", "--no-build", "--force-recreate", "bot"])
    if recreate.returncode:
        return _rollback(manifest, compose_file, project_name, bot_image, rollback_image, docker_compose, docker, runner, sleeper, readiness_timeout, "recreate failed", trace)
    manifest.transition("verifying")
    if trace is not None:
        trace.event("deployment.stage", {"stage": "verifying"})
    if _wait_ready(compose_file, project_name, docker_compose, docker, runner, sleeper, readiness_timeout, stability_seconds, trace):
        if trace is not None:
            trace.event("deployment.verified", {"deployment_id": request.get("deployment_id"), "commit": commit})
        return manifest.transition("awaiting_report", verified_at=utc_now())
    return _rollback(manifest, compose_file, project_name, bot_image, rollback_image, docker_compose, docker, runner, sleeper, readiness_timeout, "bot failed readiness verification", trace)


def _wait_ready(compose_file: str, project_name: str, docker_compose: str, docker: str,
                runner: Callable[..., subprocess.CompletedProcess], sleeper: Callable[[float], None],
                timeout: int, stability_seconds: int, trace: TraceRecorder | None = None) -> bool:
    stable = 0
    for _ in range(timeout):
        service = _run([docker_compose, "-f", compose_file, "-p", project_name, "ps", "-q", "bot"], runner, 30)
        container_id = service.stdout.strip()
        if trace is not None:
            trace.event("deployment.readiness_check", {"iteration": _ + 1, "stage": "resolve_container", "exit_code": service.returncode, "stdout": service.stdout, "stderr": service.stderr})
        if service.returncode == 0 and container_id:
            state = _run([docker, "inspect", "--format", "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container_id], runner, 30)
            if trace is not None:
                trace.event("deployment.readiness_check", {"iteration": _ + 1, "stage": "inspect_health", "container_id": container_id, "exit_code": state.returncode, "stdout": state.stdout, "stderr": state.stderr})
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
              timeout: int, reason: str, trace: TraceRecorder | None = None) -> dict:
    if trace is not None:
        trace.event("deployment.rollback.started", {"reason": reason, "rollback_image": rollback_image})
    if not rollback_image:
        return manifest.transition("failed", error=reason, rollback="unavailable")
    restored = _run([docker, "tag", rollback_image, bot_image], runner, 60)
    recreated = _run([docker_compose, "-f", compose_file, "-p", project_name, "up", "-d", "--no-build", "--force-recreate", "bot"], runner)
    if trace is not None:
        trace.event("deployment.rollback_command", {"stage": "restore image", "exit_code": restored.returncode, "stdout": restored.stdout, "stderr": restored.stderr})
        trace.event("deployment.rollback_command", {"stage": "restart", "exit_code": recreated.returncode, "stdout": recreated.stdout, "stderr": recreated.stderr})
    if restored.returncode or recreated.returncode:
        return manifest.transition("rollback_failed", error=reason)
    if not _wait_ready(compose_file, project_name, docker_compose, docker, runner, sleeper, timeout, 3, trace):
        return manifest.transition("rollback_failed", error=f"{reason}; rollback bot failed readiness")
    if trace is not None:
        trace.event("deployment.rollback.finished", {"status": "completed", "reason": reason})
    return manifest.transition("rollback_completed", error=reason, rolled_back_at=utc_now())
