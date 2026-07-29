"""Git publication and durable queueing for requested self-deployments."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Callable

from deployment import DeploymentQueue
from owner_trace import TraceRecorder


ApprovalCallback = Callable[[str, str], bool]
NoticeCallback = Callable[[], None]
ActivityCallback = Callable[[str], None]


def _command(args: list[str], repository: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=repository, capture_output=True, text=True, timeout=timeout)


def _failure(stage: str, result: subprocess.CompletedProcess, status: str = "failed") -> str:
    return json.dumps({"stage": stage, "status": status, "exit_code": result.returncode, "output": (result.stdout + result.stderr)[-8000:]})


def is_non_fast_forward(output: str) -> bool:
    normalized = output.lower()
    return any(phrase in normalized for phrase in ("fetch first", "non-fast-forward", "rejected"))


def publish_and_queue(
    repository: Path,
    approval: ApprovalCallback,
    notice: NoticeCallback | None = None,
    trace: TraceRecorder | None = None,
    activity: ActivityCallback | None = None,
) -> str:
    def run(stage: str, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        if activity is not None:
            activity(f"Deployment: {stage}")
        if trace is not None:
            trace.event("deployment.command.started", {"stage": stage, "command": args, "cwd": str(repository)})
        started = time.monotonic()
        try:
            result = _command(args, repository, timeout)
        except Exception as exc:
            if trace is not None:
                trace.event("deployment.command.failed", {"stage": stage, "command": args, "error_type": type(exc).__name__, "error": str(exc), "elapsed_seconds": time.monotonic() - started})
            raise
        if trace is not None:
            trace.event("deployment.command.finished", {"stage": stage, "command": args, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "elapsed_seconds": time.monotonic() - started})
        return result

    branch = run("preflight branch", ["git", "branch", "--show-current"], 30)
    if branch.returncode or branch.stdout.strip() != "main":
        return json.dumps({"stage": "preflight", "status": "rejected", "branch": branch.stdout.strip(), "error": "self-deployment requires main"})
    operation = run("preflight status", ["git", "status", "--porcelain=v2", "--branch"], 30)
    if operation.returncode:
        return _failure("preflight", operation)
    for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"):
        marker_result = run("preflight operation", ["git", "rev-parse", "--verify", "--quiet", marker], 30)
        if marker_result.returncode == 0:
            return json.dumps({"stage": "preflight", "status": "rejected", "error": f"unfinished Git operation: {marker}"})

    fetched = run("fetch", ["git", "fetch", "origin", "main"])
    if fetched.returncode:
        return _failure("sync", fetched)
    dirty = bool(run("dirty check", ["git", "status", "--porcelain"], 30).stdout.strip())
    divergence = run("divergence check", ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], 30)
    try:
        ahead, behind = (int(value) for value in divergence.stdout.split())
    except ValueError:
        return _failure("preflight", divergence)
    if behind and dirty:
        return json.dumps({"stage": "preflight", "status": "push_conflict", "behind": behind, "ahead": ahead, "error": "origin/main advanced while the checkout has local changes"})
    if behind:
        synchronized = run("fast-forward", ["git", "merge", "--ff-only", "origin/main"])
        if synchronized.returncode:
            return _failure("sync", synchronized, "push_conflict")

    tests = run("tests", ["python", "-m", "pytest"], 300)
    if tests.returncode:
        tests = run("fallback tests", ["python", "-m", "unittest", "discover", "-s", "tests"], 300)
    if tests.returncode:
        return _failure("tests", tests)

    dirty = bool(run("post-test status", ["git", "status", "--porcelain"], 30).stdout.strip())
    if dirty:
        diff = run("diff", ["git", "diff", "--stat", "HEAD"], 30)
        if not approval("self_deploy_commit", f"commit tested self-repository changes:\n{diff.stdout[-3000:]}"):
            return json.dumps({"stage": "commit", "status": "rejected"})
        staged = run("stage", ["git", "add", "-A"], 30)
        if staged.returncode:
            return _failure("git add", staged)
        committed = run("commit", ["git", "commit", "-m", "Deploy self-update"], 30)
        if committed.returncode:
            return _failure("commit", committed)

    divergence = run("pre-push divergence", ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], 30)
    try:
        ahead, behind = (int(value) for value in divergence.stdout.split())
    except ValueError:
        return _failure("preflight", divergence)
    if behind:
        return json.dumps({"stage": "push", "status": "push_conflict", "error": "origin/main advanced before publication"})
    if ahead:
        if not approval("self_deploy_push", "push tested self-update to origin/main"):
            return json.dumps({"stage": "push", "status": "rejected"})
        pushed = run("push", ["git", "push", "origin", "main"])
        if pushed.returncode and is_non_fast_forward(pushed.stdout + pushed.stderr):
            fetched = run("conflict fetch", ["git", "fetch", "origin", "main"])
            if fetched.returncode:
                return _failure("sync", fetched)
            rebased = run("rebase", ["git", "rebase", "origin/main"])
            if rebased.returncode:
                return _failure("rebase", rebased, "push_conflict")
            pushed = run("push retry", ["git", "push", "origin", "main"])
        if pushed.returncode:
            return _failure("push", pushed, "push_conflict" if is_non_fast_forward(pushed.stdout + pushed.stderr) else "failed")

    head = run("verify head", ["git", "rev-parse", "HEAD"], 30)
    remote = run("verify remote", ["git", "rev-parse", "origin/main"], 30)
    if head.returncode or remote.returncode or head.stdout.strip() != remote.stdout.strip():
        return json.dumps({"stage": "verify", "status": "push_conflict", "error": "published commit does not match origin/main"})
    if run("verify clean", ["git", "status", "--porcelain"], 30).stdout.strip():
        return json.dumps({"stage": "verify", "status": "rejected", "error": "checkout changed after tests"})

    queue = DeploymentQueue(os.environ.get("DEPLOYMENT_STATE_DIR", "/workspace/.personal-agent-state"))
    try:
        request = queue.enqueue(head.stdout.strip(), turn_id=trace.turn_id if trace is not None else None)
    except RuntimeError as exc:
        return json.dumps({"stage": "queue", "status": "rejected", "error": str(exc)})
    if notice is not None:
        notice()
    if trace is not None:
        trace.event("deployment.queued", request)
    return json.dumps({"stage": "queue", "status": "queued", "deployment_id": request["deployment_id"], "commit": request["commit"]})
