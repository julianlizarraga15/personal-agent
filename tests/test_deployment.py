import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from deployment import DeploymentManifest, deployment_lock, run_deployment


class DeploymentStateTests(unittest.TestCase):
    def test_manifest_creation_and_transitions_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = DeploymentManifest(directory)
            created = manifest.begin("abc123", "personal-agent-bot:old")
            self.assertEqual(created["status"], "restarting")
            self.assertEqual(manifest.transition("rollback_completed")["status"], "rollback_completed")
            self.assertEqual(manifest.read()["commit"], "abc123")
            self.assertEqual(json.loads((Path(directory) / "deployment.json").read_text())["status"], "rollback_completed")

    def test_lock_rejects_a_second_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with deployment_lock(directory):
                with self.assertRaisesRegex(RuntimeError, "already in progress"):
                    with deployment_lock(directory):
                        pass


class DeploymentRunnerTests(unittest.TestCase):
    def test_successful_startup_marks_manifest_healthy(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "newcommit\n", "")
            if command[:2] == ["docker", "inspect"]:
                return subprocess.CompletedProcess(command, 0, "running\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            result = run_deployment(
                compose_file="/workspace/personal-agent/docker-compose.yml",
                project_name="personal-agent", bot_image="personal-agent-bot:latest",
                state_dir=directory, wait_seconds=1, runner=runner, sleeper=lambda _: None,
            )
            self.assertEqual(result["status"], "healthy")
            self.assertTrue(any(command[-1:] == ["build"] or "build" in command for command in calls))

    def test_failed_startup_rolls_back_when_previous_image_exists(self) -> None:
        inspect_count = 0

        def runner(command, **kwargs):
            nonlocal inspect_count
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[:2] == ["docker", "inspect"]:
                inspect_count += 1
                return subprocess.CompletedProcess(command, 0, "exited\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            result = run_deployment(
                compose_file="/workspace/personal-agent/docker-compose.yml",
                project_name="personal-agent", bot_image="personal-agent-bot:latest",
                state_dir=directory, wait_seconds=1, runner=runner, sleeper=lambda _: None,
            )
            self.assertEqual(result["status"], "rollback_completed")
            self.assertEqual(inspect_count, 1)

    def test_failed_startup_without_previous_image_is_not_claimed_recovered(self) -> None:
        def runner(command, **kwargs):
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            if command[:2] == ["docker", "inspect"]:
                return subprocess.CompletedProcess(command, 0, "exited\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "previous image unavailable"):
                run_deployment(
                    compose_file="/workspace/personal-agent/docker-compose.yml",
                    project_name="personal-agent", bot_image="personal-agent-bot:latest",
                    state_dir=directory, wait_seconds=1, runner=runner, sleeper=lambda _: None,
                )
            self.assertEqual(DeploymentManifest(directory).read()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
