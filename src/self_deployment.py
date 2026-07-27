"""Git publication and durable queueing for requested self-deployments."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Callable

from deployment import DeploymentQueue


ApprovalCallback = Callable[[str, str], bool]
NoticeCallback = Callable[[], None]


def _command(args: list[str], repository: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=repository, capture_output=True, text=True, timeout=timeout)


def _failure(stage: str, result: subprocess.CompletedProcess, status: str = "failed") -> str:
    return json.dumps({"stage": stage, "status": status, "exit_code": result.returncode, "output": (result.stdout + result.stderr)[-8000:]})


def is_non_fast_forward(output: str) -> bool:
    normalized = output.lower()
    return any(phrase in normalized for phrase in ("fetch first", "non-fast-forward", "rejected"))


def publish_and_queue(repository: Path, approval: ApprovalCallback, notice: NoticeCallback | None = None) -> str:
    branch = _command(["git", "branch", "--show-current"], repository, 30)
    if branch.returncode or branch.stdout.strip() != "main":
        return json.dumps({"stage": "preflight", "status": "rejected", "branch": branch.stdout.strip(), "error": "self-deployment requires main"})
    operation = _command(["git", "status", "--porcelain=v2", "--branch"], repository, 30)
    if operation.returncode:
        return _failure("preflight", operation)
    for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"):
        marker_result = _command(["git", "rev-parse", "--verify", "--quiet", marker], repository, 30)
        if marker_result.returncode == 0:
            return json.dumps({"stage": "preflight", "status": "rejected", "error": f"unfinished Git operation: {marker}"})

    fetched = _command(["git", "fetch", "origin", "main"], repository)
    if fetched.returncode:
        return _failure("sync", fetched)
    dirty = bool(_command(["git", "status", "--porcelain"], repository, 30).stdout.strip())
    divergence = _command(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], repository, 30)
    try:
        ahead, behind = (int(value) for value in divergence.stdout.split())
    except ValueError:
        return _failure("preflight", divergence)
    if behind and dirty:
        return json.dumps({"stage": "preflight", "status": "push_conflict", "behind": behind, "ahead": ahead, "error": "origin/main advanced while the checkout has local changes"})
    if behind:
        synchronized = _command(["git", "merge", "--ff-only", "origin/main"], repository)
        if synchronized.returncode:
            return _failure("sync", synchronized, "push_conflict")

    tests = _command(["python", "-m", "pytest"], repository, 300)
    if tests.returncode:
        tests = _command(["python", "-m", "unittest", "discover", "-s", "tests"], repository, 300)
    if tests.returncode:
        return _failure("tests", tests)

    dirty = bool(_command(["git", "status", "--porcelain"], repository, 30).stdout.strip())
    if dirty:
        diff = _command(["git", "diff", "--stat", "HEAD"], repository, 30)
        if not approval("self_deploy_commit", f"commit tested self-repository changes:\n{diff.stdout[-3000:]}"):
            return json.dumps({"stage": "commit", "status": "rejected"})
        staged = _command(["git", "add", "-A"], repository, 30)
        if staged.returncode:
            return _failure("git add", staged)
        committed = _command(["git", "commit", "-m", "Deploy self-update"], repository, 30)
        if committed.returncode:
            return _failure("commit", committed)

    divergence = _command(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], repository, 30)
    try:
        ahead, behind = (int(value) for value in divergence.stdout.split())
    except ValueError:
        return _failure("preflight", divergence)
    if behind:
        return json.dumps({"stage": "push", "status": "push_conflict", "error": "origin/main advanced before publication"})
    if ahead:
        if not approval("self_deploy_push", "push tested self-update to origin/main"):
            return json.dumps({"stage": "push", "status": "rejected"})
        pushed = _command(["git", "push", "origin", "main"], repository)
        if pushed.returncode and is_non_fast_forward(pushed.stdout + pushed.stderr):
            fetched = _command(["git", "fetch", "origin", "main"], repository)
            if fetched.returncode:
                return _failure("sync", fetched)
            rebased = _command(["git", "rebase", "origin/main"], repository)
            if rebased.returncode:
                return _failure("rebase", rebased, "push_conflict")
            pushed = _command(["git", "push", "origin", "main"], repository)
        if pushed.returncode:
            return _failure("push", pushed, "push_conflict" if is_non_fast_forward(pushed.stdout + pushed.stderr) else "failed")

    head = _command(["git", "rev-parse", "HEAD"], repository, 30)
    remote = _command(["git", "rev-parse", "origin/main"], repository, 30)
    if head.returncode or remote.returncode or head.stdout.strip() != remote.stdout.strip():
        return json.dumps({"stage": "verify", "status": "push_conflict", "error": "published commit does not match origin/main"})
    if _command(["git", "status", "--porcelain"], repository, 30).stdout.strip():
        return json.dumps({"stage": "verify", "status": "rejected", "error": "checkout changed after tests"})

    queue = DeploymentQueue(os.environ.get("DEPLOYMENT_STATE_DIR", "/workspace/.personal-agent-state"))
    try:
        request = queue.enqueue(head.stdout.strip())
    except RuntimeError as exc:
        return json.dumps({"stage": "queue", "status": "rejected", "error": str(exc)})
    if notice is not None:
        notice()
    return json.dumps({"stage": "queue", "status": "queued", "deployment_id": request["deployment_id"], "commit": request["commit"]})
