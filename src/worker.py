"""Run Codex against a cloned repository and publish the resulting change."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Callable, Sequence


class WorkerError(RuntimeError):
    """A failure that should be reported to the caller without publishing work."""


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    stdout: str
    stderr: str


@dataclass(frozen=True)
class WorkflowResult:
    """The publishable result of a worker run."""

    branch: str
    commit: str
    tests: str
    elapsed_seconds: float
    diff: str

    def summary(self) -> str:
        return (
            "Codex completed. "
            f"Tests: {self.tests}. "
            f"Branch: {self.branch}. "
            f"Commit: {self.commit}. "
            f"Elapsed: {self.elapsed_seconds:.1f}s."
        )


def clone_repository(repo: str, destination: Path, branch: str | None = None) -> None:
    """Clone *repo* into *destination*, raising on a failed clone."""

    command = ["git", "clone", "--depth", "1"]
    if branch:
        command.extend(["--branch", branch])
    command.extend([repo, str(destination)])
    run_command(
        command,
        Path.cwd(),
        description="git clone",
    )


def run_command(command: list[str], cwd: Path, *, description: str) -> CommandResult:
    """Run a command in the repository and turn failures into useful worker errors."""

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise WorkerError(f"{description} could not start: {exc}") from exc
    if result.returncode:
        details = (result.stderr.strip() or result.stdout.strip() or "no output")
        raise WorkerError(f"{description} failed (exit {result.returncode}): {details}")
    return CommandResult(command, result.stdout, result.stderr)


def invoke_codex(task: str, repository: Path) -> CommandResult:
    """Run Codex CLI in unattended, repository-editing mode."""

    executable = os.environ.get("CODEX_BIN", "codex")
    command = [executable, "exec", "--full-auto", "--", task]
    return run_command(command, repository, description="Codex")


def available_test_command(repository: Path) -> list[str] | None:
    """Return a conservative project test command, if the project declares one."""

    if (repository / "pyproject.toml").exists() or (repository / "pytest.ini").exists():
        return ["pytest"]
    if (repository / "package.json").exists():
        return ["npm", "test", "--", "--runInBand"]
    if (repository / "go.mod").exists():
        return ["go", "test", "./..."]
    if (repository / "Cargo.toml").exists():
        return ["cargo", "test"]
    if (repository / "Makefile").exists():
        return ["make", "test"]
    return None


def run_project_tests(repository: Path) -> CommandResult | None:
    """Run declared tests; absence of a recognizable test setup is not an error."""

    command = available_test_command(repository)
    if command is None:
        return None
    return run_command(command, repository, description=f"Project tests ({shlex.join(command)})")


def git_diff(repository: Path) -> str:
    """Return the complete working-tree diff, including untracked files."""

    untracked = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"], repository, description="Finding untracked files"
    ).stdout.splitlines()
    if untracked:
        # Intent-to-add makes git diff include new files without putting their content in the index.
        run_command(["git", "add", "-N", "--", *untracked], repository, description="Preparing untracked files for diff")
    return run_command(["git", "diff", "HEAD", "--binary"], repository, description="Collecting git diff").stdout.strip()


def branch_name(task: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", "-".join(task.lower().split()))[:40].strip("-") or "task"
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"codex/{slug}-{stamp}"


def publish_changes(repository: Path, task: str) -> tuple[str, str]:
    """Create a non-main branch, commit changes, and push it to origin."""

    current = run_command(["git", "branch", "--show-current"], repository, description="Checking current branch").stdout.strip()
    name = branch_name(task)
    if name in {"main", "master"} or current == name:
        raise WorkerError("refusing to publish directly to the main branch")
    run_command(["git", "switch", "-c", name], repository, description=f"Creating branch {name}")
    run_command(["git", "add", "--all"], repository, description="Staging changes")
    run_command(["git", "commit", "-m", f"Codex: {task}"], repository, description="Committing changes")
    run_command(["git", "push", "--set-upstream", "origin", name], repository, description=f"Pushing branch {name}")
    return name, run_command(["git", "rev-parse", "HEAD"], repository, description="Reading commit").stdout.strip()


StatusCallback = Callable[[str], None]


def execute_workflow(
    task: str,
    repo: str,
    on_status: StatusCallback | None = None,
    base_branch: str | None = None,
) -> WorkflowResult:
    """Clone, edit with Codex, test, and publish a structured task result."""

    started = monotonic()

    def status(message: str) -> None:
        if on_status is not None:
            on_status(message)

    with tempfile.TemporaryDirectory(prefix="worker-") as workspace:
        clone_path = Path(workspace) / "repository"
        status("cloning repository")
        if base_branch:
            clone_repository(repo, clone_path, base_branch)
        else:
            clone_repository(repo, clone_path)
        status("running agent")
        invoke_codex(task, clone_path)
        status("running tests")
        tests = run_project_tests(clone_path)
        diff = git_diff(clone_path)
        if not diff:
            raise WorkerError("Codex completed successfully but produced no changes; nothing was pushed")
        status("pushing branch")
        branch, commit = publish_changes(clone_path, task)
        test_status = "not detected" if tests is None else "passed"
        status("finished")
        return WorkflowResult(branch, commit, test_status, monotonic() - started, diff)


def run_workflow(
    task: str,
    repo: str,
    on_status: StatusCallback | None = None,
    base_branch: str | None = None,
) -> str:
    """Compatibility wrapper returning the worker result as human-readable text."""

    result = execute_workflow(task, repo, on_status=on_status, base_branch=base_branch)
    return f"{result.summary()}\nDiff:\n{result.diff}"


def repository_tree(root: Path) -> list[str]:
    """Return a sorted, relative file tree while excluding Git metadata."""

    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def run(task: str, repo: str) -> str:
    """Clone a repository and format the task and its file tree."""

    with tempfile.TemporaryDirectory(prefix="worker-") as workspace:
        clone_path = Path(workspace) / "repository"
        clone_repository(repo, clone_path)
        files = repository_tree(clone_path)

    tree = "\n".join(f"- {file}" for file in files) or "(no files)"
    return f"Task: {task}\nRepository: {repo}\nFile tree:\n{tree}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex in a cloned repository and push a task branch.")
    parser.add_argument("--task", required=True, help="Task to give Codex")
    parser.add_argument("--repo", required=True, help="Git repository URL or local path")
    parser.add_argument("--base-branch", help="Branch to use as the starting point for this task")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        def print_status(message: str) -> None:
            print(f"STATUS: {message}", flush=True)

        result = execute_workflow(args.task, args.repo, on_status=print_status, base_branch=args.base_branch)
        print(f"RESULT: {json.dumps({'branch': result.branch, 'commit': result.commit, 'tests': result.tests, 'elapsed_seconds': result.elapsed_seconds})}", flush=True)
        print(f"SUMMARY: {result.summary()}", flush=True)
        print(f"Diff:\n{result.diff}", flush=True)
    except WorkerError as exc:
        print(f"Worker failed: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
