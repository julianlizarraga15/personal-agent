"""Durable state and single-flight orchestration for self-deployment."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time
import uuid
from typing import Iterator, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentManifest:
    def __init__(self, state_dir: Path | str = "/workspace/.personal-agent-state") -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "deployment.json"

    def read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
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

    def begin(self, commit: str, previous_image: str | None) -> dict:
        return self.write(
            deployment_id=uuid.uuid4().hex[:12], commit=commit,
            previous_image=previous_image, rollback_image=None,
            started_at=utc_now(), updated_at=utc_now(), status="restarting",
        )

    def transition(self, status: str, **values: object) -> dict:
        return self.write(status=status, updated_at=utc_now(), **values)


@contextmanager
def deployment_lock(state_dir: Path | str = "/workspace/.personal-agent-state") -> Iterator[None]:
    """Use mkdir as an atomic lock that also works across container processes."""
    path = Path(state_dir) / "deployment.lock"
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError as exc:
        # A killed helper can leave the directory behind. Only reclaim a lock
        # that is clearly older than the maximum deployment window.
        if time.time() - path.stat().st_mtime > 3600:
            path.rmdir()
            path.mkdir()
        else:
            raise RuntimeError("deployment already in progress; wait for it to finish or inspect /pending") from exc
    try:
        yield
    finally:
        try:
            path.rmdir()
        except FileNotFoundError:
            pass


def _run(command: list[str], runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> subprocess.CompletedProcess:
    return runner(command, capture_output=True, text=True, timeout=600)


def run_deployment(*, compose_file: str, project_name: str, bot_image: str,
                   docker_compose: str = "docker-compose", state_dir: str = "/workspace/.personal-agent-state",
                   docker: str = "docker", wait_seconds: int = 30,
                   runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                   sleeper: Callable[[float], None] = time.sleep) -> dict:
    """Build, recreate, and verify the bot, rolling back on startup failure."""
    if not compose_file == "/workspace/personal-agent/docker-compose.yml":
        raise RuntimeError("refusing an unexpected Compose file")
    if not bot_image.startswith("personal-agent-bot:"):
        raise RuntimeError("refusing an unexpected bot image")
    manifest = DeploymentManifest(state_dir)
    with deployment_lock(state_dir):
        previous = bot_image if runner([docker, "image", "inspect", bot_image], capture_output=True, text=True, timeout=60).returncode == 0 else None
        commit_result = runner(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        commit = commit_result.stdout.strip() or "unknown"
        state = manifest.begin(commit, previous)
        print(json.dumps({"stage": "manifest", "status": "restarting", "deployment_id": state["deployment_id"]}), flush=True)
        if previous:
            rollback = f"personal-agent-bot:rollback-{state['deployment_id']}"
            tagged = _run([docker, "tag", previous, rollback], runner)
            if tagged.returncode:
                manifest.transition("failed", error="could not save rollback image")
                raise RuntimeError("could not save rollback image")
            state = manifest.transition("restarting", rollback_image=rollback)
        build = _run([docker_compose, "-f", compose_file, "-p", project_name, "build", "bot"], runner)
        print(json.dumps({"stage": "build", "status": "passed" if build.returncode == 0 else "failed"}), flush=True)
        if build.returncode:
            manifest.transition("failed", error=(build.stdout + build.stderr)[-4000:])
            raise RuntimeError("bot image build failed")
        recreate = _run([docker_compose, "-f", compose_file, "-p", project_name, "up", "-d", "--no-build", "--force-recreate", "bot"], runner)
        if recreate.returncode:
            return _rollback(manifest, state, compose_file, project_name, bot_image, docker_compose, docker, runner, "recreate failed")
        for _ in range(wait_seconds):
            inspect = runner([docker, "inspect", "--format", "{{.State.Status}}", f"{project_name}-bot-1"], capture_output=True, text=True, timeout=30)
            if inspect.returncode == 0 and inspect.stdout.strip() == "running":
                manifest.transition("healthy", healthy_at=utc_now())
                print(json.dumps({"stage": "ready", "status": "healthy"}), flush=True)
                return manifest.read() or {}
            sleeper(1)
        return _rollback(manifest, state, compose_file, project_name, bot_image, docker_compose, docker, runner, "bot did not become ready")


def _rollback(manifest: DeploymentManifest, state: dict, compose_file: str, project_name: str, bot_image: str,
              docker_compose: str, docker: str, runner: Callable[..., subprocess.CompletedProcess], reason: str) -> dict:
    rollback = state.get("rollback_image")
    if not rollback:
        manifest.transition("failed", error=reason, rollback="unavailable")
        raise RuntimeError(f"{reason}; previous image unavailable for rollback")
    restored = _run([docker, "tag", rollback, bot_image], runner)
    recreated = _run([docker_compose, "-f", compose_file, "-p", project_name, "up", "-d", "--no-build", "--force-recreate", "bot"], runner)
    status = "rollback_completed" if restored.returncode == 0 and recreated.returncode == 0 else "rollback_failed"
    result = manifest.transition(status, error=reason)
    print(json.dumps({"stage": "rollback", "status": status}), flush=True)
    if status != "rollback_completed":
        raise RuntimeError(f"{reason}; rollback failed")
    return result


if __name__ == "__main__":
    run_deployment(compose_file=os.environ["DEPLOY_COMPOSE_FILE"], project_name=os.environ["DEPLOY_PROJECT_NAME"], bot_image=os.environ["BOT_IMAGE"], state_dir=os.environ.get("DEPLOYMENT_STATE_DIR", "/workspace/.personal-agent-state"))
