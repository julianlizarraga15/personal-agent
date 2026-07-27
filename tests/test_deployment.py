import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from deployment import DeploymentBusy, DeploymentManifest, DeploymentQueue, deployment_lock, run_deployment


class DeploymentStateTests(unittest.TestCase):
    def test_manifest_transitions_are_atomic_and_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = DeploymentManifest(directory)
            manifest.write(deployment_id="deploy1", commit="abc123", status="queued")
            transitioned = manifest.transition("building", previous_image="sha256:old")
            self.assertEqual(transitioned["status"], "building")
            self.assertEqual(json.loads((Path(directory) / "deployment.json").read_text())["commit"], "abc123")

    def test_lock_rejects_a_second_holder_and_releases_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with deployment_lock(directory):
                with self.assertRaises(DeploymentBusy):
                    with deployment_lock(directory):
                        pass
            with deployment_lock(directory):
                pass

    def test_queue_requires_fresh_deployer_and_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = DeploymentQueue(directory)
            with self.assertRaisesRegex(RuntimeError, "controller is unavailable"):
                queue.enqueue("abc")
            queue.heartbeat()
            request = queue.enqueue("abc")
            self.assertEqual(request["status"], "queued")
            with self.assertRaises(DeploymentBusy):
                queue.enqueue("def")
            claimed = queue.claim()
            self.assertEqual(claimed["commit"], "abc")
            queue.finish()

    def test_claim_resumes_an_active_request_after_controller_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = DeploymentQueue(directory)
            queue.heartbeat()
            queue.enqueue("abc")
            first = queue.claim()
            second = queue.claim()
            self.assertEqual(second, first)


class FakeRunner:
    def __init__(self, *, previous: bool = True, fail_new_bot: bool = False) -> None:
        self.previous = previous
        self.fail_new_bot = fail_new_bot
        self.rollback_started = False
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[:4] == ["git", "-C", "/workspace/personal-agent", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, "abc123\n", "")
        if command[:4] == ["git", "-C", "/workspace/personal-agent", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0 if self.previous else 1, "sha256:old\n" if self.previous else "", "")
        if command[:2] == ["docker", "tag"] and command[-1] == "personal-agent-bot:latest":
            self.rollback_started = True
        if "ps" in command and command[-2:] == ["-q", "bot"]:
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[:2] == ["docker", "inspect"]:
            healthy = not self.fail_new_bot or self.rollback_started
            return subprocess.CompletedProcess(command, 0, "true healthy\n" if healthy else "false unhealthy\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class DeploymentRunnerTests(unittest.TestCase):
    def _request(self) -> dict:
        return {"deployment_id": "deploy1", "commit": "abc123", "requested_at": "now"}

    def test_success_waits_for_stability_and_awaits_telegram_report(self) -> None:
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            DeploymentManifest(directory).write(**self._request(), status="queued")
            result = run_deployment(
                self._request(), compose_file="/workspace/personal-agent/docker-compose.yml",
                project_name="personal-agent", bot_image="personal-agent-bot:latest",
                state_dir=directory, readiness_timeout=3, stability_seconds=2,
                runner=runner, sleeper=lambda _: None,
            )
        self.assertEqual(result["status"], "awaiting_report")
        self.assertTrue(any("ps" in command for command in runner.commands))

    def test_failed_startup_restores_previous_image(self) -> None:
        runner = FakeRunner(fail_new_bot=True)
        with tempfile.TemporaryDirectory() as directory:
            DeploymentManifest(directory).write(**self._request(), status="queued")
            result = run_deployment(
                self._request(), compose_file="/workspace/personal-agent/docker-compose.yml",
                project_name="personal-agent", bot_image="personal-agent-bot:latest",
                state_dir=directory, readiness_timeout=3, stability_seconds=2,
                runner=runner, sleeper=lambda _: None,
            )
        self.assertEqual(result["status"], "rollback_completed")
        self.assertTrue(runner.rollback_started)

    def test_failed_startup_without_previous_image_reports_unavailable_rollback(self) -> None:
        runner = FakeRunner(previous=False, fail_new_bot=True)
        with tempfile.TemporaryDirectory() as directory:
            DeploymentManifest(directory).write(**self._request(), status="queued")
            result = run_deployment(
                self._request(), compose_file="/workspace/personal-agent/docker-compose.yml",
                project_name="personal-agent", bot_image="personal-agent-bot:latest",
                state_dir=directory, readiness_timeout=1, stability_seconds=1,
                runner=runner, sleeper=lambda _: None,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["rollback"], "unavailable")

    def test_mismatched_or_dirty_checkout_fails_before_build(self) -> None:
        runner = FakeRunner()
        request = {**self._request(), "commit": "different"}
        with tempfile.TemporaryDirectory() as directory:
            DeploymentManifest(directory).write(**request, status="queued")
            result = run_deployment(
                request, compose_file="/workspace/personal-agent/docker-compose.yml",
                project_name="personal-agent", bot_image="personal-agent-bot:latest",
                state_dir=directory, runner=runner, sleeper=lambda _: None,
            )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(any("build" in command for command in runner.commands))


if __name__ == "__main__":
    unittest.main()
