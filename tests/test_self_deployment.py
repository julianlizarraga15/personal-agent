import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import self_deployment
from deployment import DeploymentQueue


class SelfDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.remote = root / "origin.git"
        self.repository = root / "repo"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(self.repository)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "config", "user.name", "Test"], check=True)
        (self.repository / "tests").mkdir()
        (self.repository / "README.md").write_text("initial\n")
        subprocess.run(["git", "-C", str(self.repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repository), "commit", "-m", "initial"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repository), "remote", "add", "origin", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.repository), "push", "-u", "origin", "main"], check=True, capture_output=True)
        subprocess.run(["git", "--git-dir", str(self.remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        self.state_dir = root / "state"
        DeploymentQueue(self.state_dir).heartbeat()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, repository: Path, approval=lambda *_: True) -> dict:
        original = self_deployment._command

        def command(args, repo, timeout=120):
            if args[:3] == ["python", "-m", "pytest"]:
                return subprocess.CompletedProcess(args, 1, "", "pytest unavailable")
            if args[:4] == ["python", "-m", "unittest", "discover"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            return original(args, repo, timeout)

        with patch.object(self_deployment, "_command", side_effect=command), patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": str(self.state_dir)}):
            return json.loads(self_deployment.publish_and_queue(repository, approval))

    def test_clean_published_commit_can_be_redeployed(self) -> None:
        result = self._run(self.repository)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["commit"], subprocess.run(["git", "-C", str(self.repository), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())

    def test_dirty_checkout_is_committed_pushed_and_queued(self) -> None:
        (self.repository / "README.md").write_text("changed\n")
        result = self._run(self.repository)
        self.assertEqual(result["status"], "queued")
        local = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        remote = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "main"], capture_output=True, text=True).stdout
        self.assertEqual(local, remote)

    def test_dirty_checkout_rejects_remote_advance(self) -> None:
        other = Path(self.temporary.name) / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(other), "config", "user.name", "Test"], check=True)
        (other / "remote.txt").write_text("remote\n")
        subprocess.run(["git", "-C", str(other), "add", "."], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-m", "remote"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "push", "origin", "main"], check=True, capture_output=True)
        (self.repository / "README.md").write_text("local\n")
        result = self._run(self.repository)
        self.assertEqual(result["status"], "push_conflict")

    def test_fetch_authentication_failure_stops_before_tests_or_queue(self) -> None:
        original = self_deployment._command

        def command(args, repo, timeout=120):
            if args[:3] == ["git", "fetch", "origin"]:
                return subprocess.CompletedProcess(args, 128, "", "Permission denied (publickey)")
            return original(args, repo, timeout)

        with patch.object(self_deployment, "_command", side_effect=command), patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": str(self.state_dir)}):
            result = json.loads(self_deployment.publish_and_queue(self.repository, lambda *_: True))
        self.assertEqual(result["stage"], "sync")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(DeploymentQueue(self.state_dir).request_path.exists())

    def test_failed_test_suites_stop_before_approval_commit_or_queue(self) -> None:
        (self.repository / "README.md").write_text("changed\n")
        original_head = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        approvals: list[tuple[str, str]] = []
        original = self_deployment._command

        def command(args, repo, timeout=120):
            if args[:3] == ["python", "-m", "pytest"] or args[:4] == ["python", "-m", "unittest", "discover"]:
                return subprocess.CompletedProcess(args, 1, "", "tests failed")
            return original(args, repo, timeout)

        with patch.object(self_deployment, "_command", side_effect=command), patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": str(self.state_dir)}):
            result = json.loads(
                self_deployment.publish_and_queue(
                    self.repository, lambda action, summary: approvals.append((action, summary)) or True
                )
            )

        current_head = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual((result["stage"], result["status"]), ("tests", "failed"))
        self.assertEqual(approvals, [])
        self.assertEqual(current_head, original_head)
        self.assertFalse(DeploymentQueue(self.state_dir).request_path.exists())

    def test_rejected_commit_approval_leaves_changes_unpublished(self) -> None:
        (self.repository / "README.md").write_text("changed\n")
        original_head = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()

        result = self._run(self.repository, approval=lambda *_: False)

        current_head = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual((result["stage"], result["status"]), ("commit", "rejected"))
        self.assertEqual(current_head, original_head)
        self.assertEqual((self.repository / "README.md").read_text(), "changed\n")
        self.assertFalse(DeploymentQueue(self.state_dir).request_path.exists())

    def test_push_authentication_failure_does_not_queue(self) -> None:
        (self.repository / "README.md").write_text("changed\n")
        original = self_deployment._command

        def command(args, repo, timeout=120):
            if args[:3] == ["python", "-m", "pytest"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:3] == ["git", "push", "origin"]:
                return subprocess.CompletedProcess(args, 128, "", "Permission denied (publickey)")
            return original(args, repo, timeout)

        with patch.object(self_deployment, "_command", side_effect=command), patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": str(self.state_dir)}):
            result = json.loads(self_deployment.publish_and_queue(self.repository, lambda *_: True))
        self.assertEqual(result["stage"], "push")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(DeploymentQueue(self.state_dir).request_path.exists())

    def test_non_fast_forward_push_is_retried_once(self) -> None:
        (self.repository / "README.md").write_text("changed\n")
        original = self_deployment._command
        push_attempts = 0

        def command(args, repo, timeout=120):
            nonlocal push_attempts
            if args[:3] == ["python", "-m", "pytest"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:3] == ["git", "push", "origin"]:
                push_attempts += 1
                if push_attempts == 1:
                    return subprocess.CompletedProcess(args, 1, "", "non-fast-forward")
            return original(args, repo, timeout)

        with patch.object(self_deployment, "_command", side_effect=command), patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": str(self.state_dir)}):
            result = json.loads(self_deployment.publish_and_queue(self.repository, lambda *_: True))
        self.assertEqual(result["status"], "queued")
        self.assertEqual(push_attempts, 2)


if __name__ == "__main__":
    unittest.main()
