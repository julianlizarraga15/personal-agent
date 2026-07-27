import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from telegram_bot import ConversationSession, WorkerExecutionError, _deployment_report, _monitor_deployment, _queued_deployment, required_settings, run_docker_worker, workspace_project_path


class FakeProcess:
    def __init__(self, lines: list[str], return_code: int = 0, stderr: str = "") -> None:
        self.stdout = iter(lines)
        self.stderr = FakeStream(stderr)
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code


class FakeStream:
    def __init__(self, value: str) -> None:
        self.value = value

    def read(self) -> str:
        return self.value


class TelegramWorkerTests(unittest.TestCase):
    def test_fresh_conversation_uses_configured_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"AGENT_WORKSPACE_ROOT": directory}, clear=False
        ):
            session = ConversationSession()
            workspace = Path(directory).resolve()

        self.assertEqual(session.project, None)
        self.assertEqual(session.agent.project.name, "computer")
        self.assertEqual(session.agent.project.path, workspace)

    def test_project_path_stays_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project = workspace / "demo"
            project.mkdir()
            self.assertEqual(workspace_project_path(workspace, "demo"), project.resolve())
            with self.assertRaises(ValueError):
                workspace_project_path(workspace, "../outside")

    def test_pending_approval_resolves_exact_request(self) -> None:
        session = ConversationSession()
        notified = threading.Event()
        result: list[bool] = []

        def wait_for_approval() -> None:
            result.append(session.request_approval("write_file", "write notes.txt", lambda request: notified.set()))

        thread = threading.Thread(target=wait_for_approval)
        thread.start()
        self.assertTrue(notified.wait(timeout=1))
        request = session.pending_approval
        assert request is not None
        self.assertFalse(session.resolve_approval("wrong-id", True))
        self.assertTrue(session.resolve_approval(request.request_id, True))
        thread.join(timeout=1)
        self.assertEqual(result, [True])
        self.assertIsNone(session.pending_approval)

    def test_pending_approval_can_resolve_without_id(self) -> None:
        session = ConversationSession()
        notified = threading.Event()
        result: list[bool] = []

        def wait_for_approval() -> None:
            result.append(session.request_approval("write_file", "write notes.txt", lambda request: notified.set()))

        thread = threading.Thread(target=wait_for_approval)
        thread.start()
        self.assertTrue(notified.wait(timeout=1))
        self.assertTrue(session.resolve_approval(None, True))
        thread.join(timeout=1)
        self.assertEqual(result, [True])

    def test_approval_prompt_failure_does_not_reject_pending_action(self) -> None:
        session = ConversationSession()
        notified = threading.Event()
        result: list[bool] = []

        def wait_for_approval() -> None:
            def failed_notify(request) -> None:
                notified.set()
                raise RuntimeError("Telegram delivery failed")

            result.append(session.request_approval("self_deploy", "redeploy", failed_notify))

        thread = threading.Thread(target=wait_for_approval)
        thread.start()
        self.assertTrue(notified.wait(timeout=1))
        self.assertTrue(session.resolve_approval(None, True))
        thread.join(timeout=1)
        self.assertEqual(result, [True])

    def test_conversation_session_keeps_project_and_context(self) -> None:
        session = ConversationSession(project="https://example.test/repo.git")
        first_prompt = session.prompt_for("Add a health endpoint")
        session.remember("Add a health endpoint", "success\nbranch: codex/health")
        second_prompt = session.prompt_for("Now add a test for it")

        self.assertIn("Add a health endpoint", first_prompt)
        self.assertIn("Prior conversation", second_prompt)
        self.assertIn("codex/health", second_prompt)

    def test_docker_worker_uses_previous_branch_when_provided(self) -> None:
        payload = {"branch": "codex/next", "commit": "abc", "tests": "passed", "elapsed_seconds": 1}
        process = FakeProcess([f"RESULT: {json.dumps(payload)}\n"])
        with patch("telegram_bot.subprocess.Popen", return_value=process) as popen:
            run_docker_worker("repo", "task", image="worker:latest", base_branch="codex/previous")
        self.assertEqual(popen.call_args.args[0][-2:], ["--base-branch", "codex/previous"])

    def test_required_settings_validates_and_parses_environment(self) -> None:
        self.assertEqual(
            required_settings({"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_ID": "42"}),
            ("token", 42, "repository-worker:latest"),
        )

    def test_docker_worker_streams_statuses_and_parses_result(self) -> None:
        payload = {"branch": "codex/fix", "commit": "abc", "tests": "passed", "elapsed_seconds": 2.5}
        process = FakeProcess(["STATUS: cloning repository\n", f"RESULT: {json.dumps(payload)}\n"])
        statuses: list[str] = []
        with patch("telegram_bot.subprocess.Popen", return_value=process) as popen:
            result = run_docker_worker("repo", "fix it", image="worker:latest", on_status=statuses.append)

        popen.assert_called_once_with(
            ["docker", "run", "--rm", "worker:latest", "--task", "fix it", "--repo", "repo"],
            stdout=-1,
            stderr=-1,
            text=True,
            bufsize=1,
        )
        self.assertEqual(statuses, ["cloning repository"])
        self.assertEqual(result.commit, "abc")

    def test_docker_worker_includes_logs_on_failure(self) -> None:
        with patch("telegram_bot.subprocess.Popen", return_value=FakeProcess(["worker output\n"], 1, "traceback")):
            with self.assertRaisesRegex(WorkerExecutionError, r"worker output[\s\S]*traceback"):
                run_docker_worker("repo", "task", image="worker:latest")

    def test_queued_deployment_protocol_is_parsed(self) -> None:
        queued = _queued_deployment('{"status":"queued","deployment_id":"d1","commit":"abc"}')
        self.assertEqual(queued["deployment_id"], "d1")
        self.assertIsNone(_queued_deployment("not json"))

    def test_deployment_report_distinguishes_success_and_rollback(self) -> None:
        self.assertIn("completed successfully", _deployment_report({"status": "awaiting_report", "deployment_id": "d1", "commit": "abc"}))
        self.assertIn("rollback completed", _deployment_report({"status": "rollback_completed", "deployment_id": "d1"}))


class DeploymentReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_notification_marks_manifest_healthy(self) -> None:
        messages = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": directory}):
            from deployment import DeploymentManifest

            manifest = DeploymentManifest(directory)
            manifest.write(status="awaiting_report", deployment_id="d1", commit="abc")

            async def send(message):
                messages.append(message)

            await _monitor_deployment(send, timeout_seconds=1)
            self.assertEqual(manifest.read()["status"], "healthy")
        self.assertIn("completed successfully", messages[0])

    async def test_failed_notification_is_reported_once_but_remains_failed(self) -> None:
        messages = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": directory}):
            from deployment import DeploymentManifest

            manifest = DeploymentManifest(directory)
            manifest.write(status="failed", deployment_id="d1", commit="abc", error="build broke")

            async def send(message):
                messages.append(message)

            await _monitor_deployment(send, timeout_seconds=1)
            state = manifest.read()
            self.assertEqual(state["status"], "failed")
            self.assertIn("reported_at", state)
        self.assertIn("build broke", messages[0])


if __name__ == "__main__":
    unittest.main()
