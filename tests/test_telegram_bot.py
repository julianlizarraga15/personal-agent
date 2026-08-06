import asyncio
import base64
import gzip
from io import BytesIO
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path

from PIL import Image

import telegram_bot
from codex_backend import CodexTurnResult
from git_publish import GitPublishApproval
from public_download import DownloadApproval
from telegram_bot import (
    CodexApprovalBroker,
    DEFAULT_IMAGE_PROMPT,
    ConversationSession,
    PendingApproval,
    WorkerExecutionError,
    _codex_file_bytes,
    _codex_image_bytes,
    _deployment_report,
    _monitor_deployment,
    _queued_deployment,
    _reply_agent_response,
    _reply_codex_files,
    _reply_codex_images,
    _telegram_html,
    _telegram_response_chunks,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    _validate_image,
    required_settings,
    run_docker_worker,
    workspace_project_path,
)
from usage import ModelUsage, SessionUsage, UsageStore


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
    def test_agent_markdown_is_rendered_as_telegram_html(self) -> None:
        source = (
            "# Match data\n\n"
            "- **[Sportradar](https://example.test/data?year=2026&kind=event)** — "
            "includes `water_break_start`.\n"
            "- *FIFA* and _StatsBomb_\n\n"
            "> Check the methodology.\n\n"
            "```python\nprint(\"<ready>\")\n```"
        )

        rendered = _telegram_html(source)

        self.assertIn("<b>Match data</b>", rendered)
        self.assertIn('<b><a href="https://example.test/data?year=2026&amp;kind=event">Sportradar</a></b>', rendered)
        self.assertIn("<code>water_break_start</code>", rendered)
        self.assertIn("<i>FIFA</i> and <i>StatsBomb</i>", rendered)
        self.assertIn("<blockquote>", rendered)
        self.assertIn("Check the methodology.", rendered)
        self.assertIn("<pre><code>print(&quot;&lt;ready&gt;&quot;)", rendered)
        self.assertNotIn("**", rendered)
        self.assertNotIn("\n\n\n", rendered)

    def test_agent_markdown_drops_unsupported_html_and_unsafe_links(self) -> None:
        rendered = _telegram_html('<script>alert("hi")</script> [unsafe](javascript:alert(1))')

        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<a ", rendered)
        self.assertIn('alert(&quot;hi&quot;)', rendered)
        self.assertIn("unsafe", rendered)

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

    def test_pending_approval_reaction_must_target_its_prompt(self) -> None:
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
        request.bind_message(123, 456)
        self.assertFalse(session.resolve_reaction_approval(123, 999, True))
        self.assertTrue(session.resolve_reaction_approval(123, 456, True))
        thread.join(timeout=1)
        self.assertEqual(result, [True])

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

    def test_pending_approval_resolves_only_its_bound_prompt_once(self) -> None:
        session = ConversationSession()
        request = PendingApproval("approval-1", "write_file", "write notes.txt")
        session.pending_approval = request

        self.assertTrue(session.bind_approval_prompt(request.request_id, 42, 100))
        self.assertFalse(session.resolve_approval_for_prompt(42, 99, True))
        self.assertFalse(session.resolve_approval_for_prompt(41, 100, True))
        self.assertTrue(session.resolve_approval_for_prompt(42, 100, True))
        self.assertFalse(session.resolve_approval_for_prompt(42, 100, False))
        self.assertTrue(request.approved)

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


class CodexApprovalBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_approval_is_exact_owner_bound_and_one_shot(self) -> None:
        broker = CodexApprovalBroker()
        prompt_message = SimpleNamespace(edit_text=AsyncMock())
        bot = SimpleNamespace(send_message=AsyncMock(return_value=prompt_message))
        request = DownloadApproval("https://example.com/report.pdf", "data/raw/report.pdf", 50_000_000)

        approval = asyncio.create_task(broker.request_download(bot, 99, 42, request))
        await asyncio.sleep(0)
        token = next(iter(broker.pending))
        self.assertFalse(broker.resolve(token, 7, True))
        self.assertTrue(broker.resolve(token, 42, True))

        self.assertTrue(await approval)
        prompt = bot.send_message.await_args.kwargs["text"]
        self.assertIn(request.url, prompt)
        self.assertIn(request.destination, prompt)
        self.assertIn("50 MB", prompt)
        callback_data = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(callback_data, f"codex-download:{token}:allow")
        self.assertFalse(broker.resolve(token, 42, True))

    async def test_publication_approval_is_sent_directly_to_owner_chat(self) -> None:
        broker = CodexApprovalBroker()
        prompt_message = SimpleNamespace(edit_text=AsyncMock())
        bot = SimpleNamespace(send_message=AsyncMock(return_value=prompt_message))
        request = GitPublishApproval(
            "/workspace/mental-models",
            "git@github.com:julianlizarraga15/mental-models.git",
            "main",
            "a" * 40,
        )

        approval = asyncio.create_task(broker.request_publish(bot, 99, 42, request))
        await asyncio.sleep(0)
        token = next(iter(broker.pending))
        self.assertTrue(broker.resolve(token, 42, True))

        self.assertTrue(await approval)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], 99)
        self.assertIn("one exact commit", bot.send_message.await_args.kwargs["text"])
        callback_data = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(callback_data, f"codex-publish:{token}:allow")
        prompt_message.edit_text.assert_awaited_once_with(
            "Publication approved once.\nDestination: "
            "git@github.com:julianlizarraga15/mental-models.git (main)"
        )


class AgentResponseFormattingTests(unittest.IsolatedAsyncioTestCase):
    async def test_formatted_response_uses_html_parse_mode(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())

        await _reply_agent_response(message, "A **bold** answer")

        message.reply_text.assert_awaited_once_with(
            "A <b>bold</b> answer",
            parse_mode="HTML",
        )

    async def test_formatted_response_falls_back_to_plain_text_on_parse_error(self) -> None:
        message = SimpleNamespace(
            reply_text=AsyncMock(side_effect=[telegram_bot.BadRequest("can't parse entities"), None])
        )

        await _reply_agent_response(message, "A **bold** answer")

        self.assertEqual(message.reply_text.await_count, 2)
        self.assertEqual(message.reply_text.await_args_list[0].kwargs["parse_mode"], "HTML")
        self.assertEqual(message.reply_text.await_args_list[1].args[0], "A **bold** answer")
        self.assertNotIn("parse_mode", message.reply_text.await_args_list[1].kwargs)

    async def test_long_response_sends_all_chunks_in_order(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        text = "\n\n".join(f"Paragraph {index}: " + ("word " * 900) for index in range(4))

        await _reply_agent_response(message, text)

        replies = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertGreater(len(replies), 1)
        self.assertEqual("".join(_telegram_response_chunks(text)), text)
        self.assertEqual("".join(replies), "".join(_telegram_html(chunk) for chunk in _telegram_response_chunks(text)))
        self.assertTrue(all("parse_mode" in call.kwargs for call in message.reply_text.await_args_list))

    def test_long_response_prefers_readable_boundaries(self) -> None:
        text = "First paragraph with several words.\n\nSecond paragraph follows."
        with patch.object(telegram_bot, "TELEGRAM_MAX_MESSAGE_LENGTH", 42):
            chunks = _telegram_response_chunks(text)

        self.assertEqual("".join(chunks), text)
        self.assertTrue(chunks[0].endswith("\n\n") or chunks[0].endswith(" "))
        self.assertTrue(all(len(_telegram_html(chunk)) <= 42 for chunk in chunks))

    def test_oversized_single_line_is_hard_split_safely(self) -> None:
        text = "x" * (TELEGRAM_MAX_MESSAGE_LENGTH + 500)

        chunks = _telegram_response_chunks(text)

        self.assertEqual("".join(chunks), text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= TELEGRAM_MAX_MESSAGE_LENGTH for chunk in chunks))
        self.assertTrue(all(len(_telegram_html(chunk)) <= TELEGRAM_MAX_MESSAGE_LENGTH for chunk in chunks))

    async def test_html_parse_failure_falls_back_per_chunk_and_continues(self) -> None:
        html_attempts = 0

        async def send_reply(*args, **kwargs):
            nonlocal html_attempts
            if kwargs.get("parse_mode") == "HTML":
                html_attempts += 1
                if html_attempts in {1, 3}:
                    raise telegram_bot.BadRequest("chunk rejected")

        message = SimpleNamespace(reply_text=AsyncMock(side_effect=send_reply))
        text = "\n\n".join("A **formatted** paragraph " + ("word " * 20) for _ in range(3))
        with patch.object(telegram_bot, "TELEGRAM_MAX_MESSAGE_LENGTH", 80):
            chunks = _telegram_response_chunks(text)
            await _reply_agent_response(message, text)

        self.assertEqual(message.reply_text.await_count, len(chunks) + 2)
        self.assertEqual(message.reply_text.await_args_list[1].args[0], chunks[0])
        plain_replies = [call.args[0] for call in message.reply_text.await_args_list if "parse_mode" not in call.kwargs]
        self.assertEqual(plain_replies, [chunks[0], chunks[2]])
        self.assertGreater(message.reply_text.await_count, 3)

    async def test_no_response_chunk_exceeds_telegram_limit(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        text = ("**bold** & <tag> " * 700) + "\n\n" + ("plain " * 700)

        await _reply_agent_response(message, text)

        for call in message.reply_text.await_args_list:
            self.assertLessEqual(len(call.args[0]), TELEGRAM_MAX_MESSAGE_LENGTH)


class CodexOutputImageTests(unittest.IsolatedAsyncioTestCase):
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    async def test_codex_image_is_sent_as_photo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plot.png").write_bytes(self.PNG)
            result = CodexTurnResult("Here it is.", root, ("plot.png",))
            message = SimpleNamespace(reply_photo=AsyncMock(), reply_document=AsyncMock())

            failures = await _reply_codex_images(message, result)

        self.assertEqual(failures, 0)
        message.reply_photo.assert_awaited_once()
        self.assertEqual(message.reply_photo.await_args.kwargs["photo"].getvalue(), self.PNG)
        message.reply_document.assert_not_awaited()

    async def test_photo_failure_falls_back_to_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plot.png").write_bytes(self.PNG)
            result = CodexTurnResult("Here it is.", root, ("plot.png",))
            message = SimpleNamespace(
                reply_photo=AsyncMock(side_effect=telegram_bot.BadRequest("photo rejected")),
                reply_document=AsyncMock(),
            )

            failures = await _reply_codex_images(message, result)

        self.assertEqual(failures, 0)
        message.reply_document.assert_awaited_once()
        self.assertEqual(message.reply_document.await_args.kwargs["document"].getvalue(), self.PNG)

    def test_codex_image_must_be_valid_and_inside_selected_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            root.mkdir()
            (root / "bad.png").write_bytes(b"not an image")
            (base / "outside.png").write_bytes(self.PNG)

            with self.assertRaises(ValueError):
                _codex_image_bytes(root, "bad.png")
            with self.assertRaises(ValueError):
                _codex_image_bytes(root, "../outside.png")


class CodexOutputFileTests(unittest.IsolatedAsyncioTestCase):
    PNG = CodexOutputImageTests.PNG

    async def test_two_png_files_are_sent_byte_exactly_as_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.png").write_bytes(self.PNG)
            (root / "second.png").write_bytes(self.PNG + b"second")
            result = CodexTurnResult(
                "Files attached.", root, file_paths=("first.png", "second.png")
            )
            message = SimpleNamespace(reply_photo=AsyncMock(), reply_document=AsyncMock())

            failures = await _reply_codex_files(message, result)

        self.assertEqual(failures, 0)
        self.assertEqual(message.reply_document.await_count, 2)
        self.assertEqual(
            [call.kwargs["filename"] for call in message.reply_document.await_args_list],
            ["first.png", "second.png"],
        )
        self.assertEqual(
            [call.kwargs["document"].getvalue() for call in message.reply_document.await_args_list],
            [self.PNG, self.PNG + b"second"],
        )
        message.reply_photo.assert_not_awaited()

    async def test_arbitrary_regular_files_are_sent_as_documents(self) -> None:
        payloads = {
            "report.pdf": b"%PDF-1.7\n",
            "source.py": b"print('safe')\n",
            "bundle.zip": b"PK\x03\x04archive",
            "payload.bin": b"\x00\xff\x10",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in payloads.items():
                (root / name).write_bytes(data)
            result = CodexTurnResult("Attached.", root, file_paths=tuple(payloads))
            message = SimpleNamespace(reply_photo=AsyncMock(), reply_document=AsyncMock())

            failures = await _reply_codex_files(message, result)

        self.assertEqual(failures, 0)
        self.assertEqual(
            [call.kwargs["document"].getvalue() for call in message.reply_document.await_args_list],
            list(payloads.values()),
        )
        message.reply_photo.assert_not_awaited()

    def test_file_rejects_missing_directory_outside_and_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            root.mkdir()
            (root / "folder").mkdir()
            (base / "outside.bin").write_bytes(b"outside")
            for name, data in {
                ".env": b"VALUE=secret",
                ".env.local": b"VALUE=secret",
                "auth.json": b"{}",
                "deploy.pem": b"certificate-like",
                "notes.txt": b"-----BEGIN PRIVATE KEY-----\nsecret",
            }.items():
                (root / name).write_bytes(data)

            rejected = [
                "missing.bin",
                "folder",
                "../outside.bin",
                ".env",
                ".env.local",
                "auth.json",
                "deploy.pem",
                "notes.txt",
            ]
            for requested_path in rejected:
                with self.subTest(requested_path=requested_path), self.assertRaises((ValueError, FileNotFoundError)):
                    _codex_file_bytes(root, requested_path)

            (root / ".env.example").write_bytes(b"PLACEHOLDER=value")
            self.assertEqual(_codex_file_bytes(root, ".env.example").getvalue(), b"PLACEHOLDER=value")

    def test_file_rejects_oversized_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"TELEGRAM_MAX_OUTPUT_DOCUMENT_BYTES": "3"}
        ):
            root = Path(directory)
            (root / "large.bin").write_bytes(b"four")

            with self.assertRaises(ValueError):
                _codex_file_bytes(root, "large.bin")

    async def test_failed_delivery_is_content_free_in_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret_path = "missing-sensitive-name.txt"
            result = CodexTurnResult("Attached.", root, file_paths=(secret_path,))
            message = SimpleNamespace(reply_document=AsyncMock())

            with self.assertLogs("telegram_bot", level="WARNING") as captured:
                failures = await _reply_codex_files(message, result)

        self.assertEqual(failures, 1)
        self.assertNotIn(secret_path, "\n".join(captured.output))

    async def test_telegram_document_failure_is_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requested_path = "sensitive-report.bin"
            (root / requested_path).write_bytes(b"safe bytes")
            result = CodexTurnResult("Attached.", root, file_paths=(requested_path,))
            message = SimpleNamespace(reply_document=AsyncMock(side_effect=RuntimeError("remote rejected")))

            with self.assertLogs("telegram_bot", level="WARNING") as captured:
                failures = await _reply_codex_files(message, result)

        self.assertEqual(failures, 1)
        log_text = "\n".join(captured.output)
        self.assertNotIn(requested_path, log_text)
        self.assertNotIn("remote rejected", log_text)


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

    async def test_notification_delivery_failure_remains_pending_and_retries(self) -> None:
        messages = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEPLOYMENT_STATE_DIR": directory}):
            from deployment import DeploymentManifest

            manifest = DeploymentManifest(directory)
            manifest.write(status="awaiting_report", deployment_id="d1", commit="abc")

            async def fail(_message):
                raise RuntimeError("Telegram unavailable")

            await _monitor_deployment(fail, timeout_seconds=1)
            state = manifest.read()
            self.assertEqual(state["status"], "awaiting_report")
            self.assertNotIn("reported_at", state)

            async def succeed(message):
                messages.append(message)

            await _monitor_deployment(succeed, timeout_seconds=1)
            self.assertEqual(manifest.read()["status"], "healthy")
        self.assertEqual(len(messages), 1)


class ImageMessageTests(unittest.IsolatedAsyncioTestCase):
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    STATIC_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    ANIMATED_GIF = base64.b64decode(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAAh+QQAAAAAACwAAAAAAQABAAACAQQAOw=="
    )

    def setUp(self) -> None:
        telegram_bot.SESSIONS.clear()

    def tearDown(self) -> None:
        telegram_bot.SESSIONS.clear()

    @staticmethod
    def media(data: bytes, *, size: int | None = None, mime_type: str | None = None) -> SimpleNamespace:
        downloaded = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
        return SimpleNamespace(
            file_size=len(data) if size is None else size,
            mime_type=mime_type,
            get_file=AsyncMock(return_value=downloaded),
        )

    @staticmethod
    def update(message: SimpleNamespace, user_id: int = 42) -> SimpleNamespace:
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=message)

    async def test_photo_uses_largest_rendition_and_caption(self) -> None:
        smaller = self.media(self.PNG)
        largest = self.media(self.PNG)
        message = SimpleNamespace(
            photo=(smaller, largest),
            document=None,
            caption="Read this error",
            reply_text=AsyncMock(),
        )

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._run_agent", new_callable=AsyncMock
        ) as run_agent:
            await telegram_bot.image_message(self.update(message), SimpleNamespace())

        smaller.get_file.assert_not_awaited()
        largest.get_file.assert_awaited_once_with()
        image = run_agent.await_args.kwargs["image"]
        self.assertEqual((image.data, image.media_type, image.detail), (self.PNG, "image/png", "high"))
        self.assertEqual(run_agent.await_args.args[2], "Read this error")

    async def test_image_document_uses_default_prompt_and_detected_format(self) -> None:
        document = self.media(self.STATIC_GIF, mime_type="image/jpeg")
        message = SimpleNamespace(photo=(), document=document, caption="  ", reply_text=AsyncMock())

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._run_agent", new_callable=AsyncMock
        ) as run_agent:
            await telegram_bot.image_message(self.update(message), SimpleNamespace())

        image = run_agent.await_args.kwargs["image"]
        self.assertEqual(image.media_type, "image/gif")
        self.assertEqual(run_agent.await_args.args[2], DEFAULT_IMAGE_PROMPT)

    async def test_declared_oversize_image_is_rejected_before_download(self) -> None:
        photo = self.media(self.PNG, size=11)
        message = SimpleNamespace(photo=(photo,), document=None, caption=None, reply_text=AsyncMock())

        with patch.dict(
            os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42", "TELEGRAM_MAX_IMAGE_BYTES": "10"}
        ), patch("telegram_bot._run_agent", new_callable=AsyncMock) as run_agent:
            await telegram_bot.image_message(self.update(message), SimpleNamespace())

        photo.get_file.assert_not_awaited()
        run_agent.assert_not_awaited()
        self.assertIn("too large", message.reply_text.await_args.args[0].lower())

    async def test_actual_oversize_image_is_rejected_after_download(self) -> None:
        photo = self.media(self.PNG, size=1)
        message = SimpleNamespace(photo=(photo,), document=None, caption=None, reply_text=AsyncMock())

        with patch.dict(
            os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42", "TELEGRAM_MAX_IMAGE_BYTES": "10"}
        ), patch("telegram_bot._run_agent", new_callable=AsyncMock) as run_agent:
            await telegram_bot.image_message(self.update(message), SimpleNamespace())

        run_agent.assert_not_awaited()
        self.assertIn("too large", message.reply_text.await_args.args[0].lower())

    async def test_invalid_or_animated_image_is_rejected(self) -> None:
        for payload in (b"not-an-image", self.ANIMATED_GIF):
            with self.subTest(payload=payload):
                message = SimpleNamespace(
                    photo=(self.media(payload),), document=None, caption=None, reply_text=AsyncMock()
                )
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                    "telegram_bot._run_agent", new_callable=AsyncMock
                ) as run_agent:
                    await telegram_bot.image_message(self.update(message), SimpleNamespace())
                run_agent.assert_not_awaited()
                self.assertIn("couldn’t use that image", message.reply_text.await_args.args[0].lower())

    async def test_download_failure_is_reported_without_starting_agent(self) -> None:
        photo = self.media(self.PNG)
        photo.get_file.side_effect = RuntimeError("telegram unavailable")
        message = SimpleNamespace(photo=(photo,), document=None, caption=None, reply_text=AsyncMock())

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._run_agent", new_callable=AsyncMock
        ) as run_agent:
            await telegram_bot.image_message(self.update(message), SimpleNamespace())

        run_agent.assert_not_awaited()
        self.assertIn("download", message.reply_text.await_args.args[0].lower())

    async def test_unauthorized_and_busy_images_are_not_downloaded(self) -> None:
        for user_id, running in ((7, False), (42, True)):
            with self.subTest(user_id=user_id, running=running):
                photo = self.media(self.PNG)
                message = SimpleNamespace(photo=(photo,), document=None, caption=None, reply_text=AsyncMock())
                if running:
                    telegram_bot.SESSIONS[42] = ConversationSession(running=True)
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                    "telegram_bot._run_agent", new_callable=AsyncMock
                ) as run_agent:
                    await telegram_bot.image_message(self.update(message, user_id), SimpleNamespace())
                photo.get_file.assert_not_awaited()
                run_agent.assert_not_awaited()
                if running:
                    self.assertIn("still working", message.reply_text.await_args.args[0].lower())

    def test_image_validation_accepts_supported_static_formats(self) -> None:
        expected_types = {
            "GIF": "image/gif",
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }
        for image_format, media_type in expected_types.items():
            with self.subTest(image_format=image_format):
                buffer = BytesIO()
                Image.new("RGB", (1, 1), "white").save(buffer, format=image_format)
                self.assertEqual(_validate_image(buffer.getvalue()), media_type)


class AudioMessageTests(unittest.IsolatedAsyncioTestCase):
    OGG = b"OggS" + b"\x00" * 32
    MP3 = b"ID3" + b"\x04\x00\x00" + b"\x00" * 32

    def setUp(self) -> None:
        telegram_bot.SESSIONS.clear()

    def tearDown(self) -> None:
        telegram_bot.SESSIONS.clear()

    @staticmethod
    def media(
        data: bytes,
        *,
        size: int | None = None,
        duration: int | None = None,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> SimpleNamespace:
        downloaded = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
        return SimpleNamespace(
            file_size=len(data) if size is None else size,
            duration=duration,
            file_name=file_name,
            mime_type=mime_type,
            get_file=AsyncMock(return_value=downloaded),
        )

    @staticmethod
    def update(message: SimpleNamespace, user_id: int = 42) -> SimpleNamespace:
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=message)

    async def test_voice_note_is_downloaded_and_passed_to_agent_with_caption(self) -> None:
        voice = self.media(self.OGG, duration=8, mime_type="audio/ogg")
        message = SimpleNamespace(
            voice=voice,
            audio=None,
            document=None,
            caption="Summarize this",
            reply_text=AsyncMock(),
        )

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._run_agent", new_callable=AsyncMock
        ) as run_agent:
            await telegram_bot.audio_message(self.update(message), SimpleNamespace())

        voice.get_file.assert_awaited_once_with()
        audio = run_agent.await_args.kwargs["audio"]
        self.assertEqual((audio.data, audio.filename, audio.media_type), (self.OGG, "audio.ogg", "audio/ogg"))
        self.assertEqual(run_agent.await_args.args[2], "Summarize this")

    async def test_audio_attachment_precedes_audio_document(self) -> None:
        audio = self.media(self.MP3, duration=5, file_name="song.mp3")
        document = self.media(self.OGG, file_name="voice.ogg")
        message = SimpleNamespace(
            voice=None,
            audio=audio,
            document=document,
            caption=None,
            reply_text=AsyncMock(),
        )

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._run_agent", new_callable=AsyncMock
        ) as run_agent:
            await telegram_bot.audio_message(self.update(message), SimpleNamespace())

        audio.get_file.assert_awaited_once_with()
        document.get_file.assert_not_awaited()
        self.assertEqual(run_agent.await_args.kwargs["audio"].filename, "audio.mp3")

    async def test_declared_size_and_duration_are_rejected_before_download(self) -> None:
        cases = (
            (self.media(self.OGG, size=11, duration=1), {"TELEGRAM_MAX_AUDIO_BYTES": "10"}, "too large"),
            (self.media(self.OGG, size=1, duration=11), {"TELEGRAM_MAX_AUDIO_SECONDS": "10"}, "too long"),
        )
        for media, override, expected in cases:
            with self.subTest(expected=expected):
                message = SimpleNamespace(
                    voice=media,
                    audio=None,
                    document=None,
                    caption=None,
                    reply_text=AsyncMock(),
                )
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42", **override}), patch(
                    "telegram_bot._run_agent", new_callable=AsyncMock
                ) as run_agent:
                    await telegram_bot.audio_message(self.update(message), SimpleNamespace())
                media.get_file.assert_not_awaited()
                run_agent.assert_not_awaited()
                self.assertIn(expected, message.reply_text.await_args.args[0].lower())

    async def test_actual_oversize_invalid_and_download_failure_do_not_run_agent(self) -> None:
        cases = (
            (self.media(self.OGG, size=1), {"TELEGRAM_MAX_AUDIO_BYTES": "10"}, "too large"),
            (self.media(b"not audio"), {}, "couldn’t use"),
        )
        for media, override, expected in cases:
            with self.subTest(expected=expected):
                message = SimpleNamespace(voice=media, audio=None, document=None, caption=None, reply_text=AsyncMock())
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42", **override}), patch(
                    "telegram_bot._run_agent", new_callable=AsyncMock
                ) as run_agent:
                    await telegram_bot.audio_message(self.update(message), SimpleNamespace())
                run_agent.assert_not_awaited()
                self.assertIn(expected, message.reply_text.await_args.args[0].lower())

        media = self.media(self.OGG)
        media.get_file.side_effect = RuntimeError("telegram unavailable")
        message = SimpleNamespace(voice=media, audio=None, document=None, caption=None, reply_text=AsyncMock())
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._run_agent", new_callable=AsyncMock
        ) as run_agent:
            await telegram_bot.audio_message(self.update(message), SimpleNamespace())
        run_agent.assert_not_awaited()
        self.assertIn("download", message.reply_text.await_args.args[0].lower())

    async def test_unauthorized_and_busy_audio_are_not_downloaded(self) -> None:
        for user_id, running in ((7, False), (42, True)):
            with self.subTest(user_id=user_id, running=running):
                voice = self.media(self.OGG)
                message = SimpleNamespace(voice=voice, audio=None, document=None, caption=None, reply_text=AsyncMock())
                if running:
                    telegram_bot.SESSIONS[42] = ConversationSession(running=True)
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                    "telegram_bot._run_agent", new_callable=AsyncMock
                ) as run_agent:
                    await telegram_bot.audio_message(self.update(message, user_id), SimpleNamespace())
                voice.get_file.assert_not_awaited()
                run_agent.assert_not_awaited()

    def test_audio_validation_accepts_supported_signatures(self) -> None:
        expected = (
            (self.OGG, ("audio.ogg", "audio/ogg")),
            (self.MP3, ("audio.mp3", "audio/mpeg")),
            (b"fLaC" + b"\x00" * 32, ("audio.flac", "audio/flac")),
            (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 16, ("audio.wav", "audio/wav")),
            (b"\x1aE\xdf\xa3" + b"\x00" * 32, ("audio.webm", "audio/webm")),
            (b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 20, ("audio.m4a", "audio/mp4")),
            (b"\xff\xfb" + b"\x00" * 32, ("audio.mp3", "audio/mpeg")),
        )
        for payload, result in expected:
            with self.subTest(result=result):
                self.assertEqual(telegram_bot._validate_audio(payload), result)


class AudioTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def inline_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def test_audio_is_transcribed_then_caption_directs_agent(self) -> None:
        transcription = SimpleNamespace(
            text="The server returns error 500.",
            model="gpt-4o-mini-transcribe",
            usage=SimpleNamespace(input_tokens=12, output_tokens=5),
        )
        create = Mock(return_value=transcription)
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        agent = SimpleNamespace(client=client, respond=Mock(return_value="Fixed it"))
        persisted: list[ModelUsage] = []
        session = ConversationSession(agent=telegram_bot.AgentSession(usage=SessionUsage(recorder=persisted.append)))
        telegram_bot.SESSIONS[42] = session
        message = SimpleNamespace(reply_text=AsyncMock())
        audio = telegram_bot.AudioInput(b"OggSdata", "audio.ogg", "audio/ogg")

        with patch.dict(os.environ, {"OPENAI_TRANSCRIPTION_MODEL": "gpt-4o-mini-transcribe"}), patch(
            "telegram_bot.Agent", return_value=agent
        ), patch("telegram_bot.asyncio.to_thread", new=self.inline_thread):
            await telegram_bot._run_agent(message, session, "Diagnose and fix it", 42, audio=audio)

        create.assert_called_once_with(
            model="gpt-4o-mini-transcribe",
            file=("audio.ogg", b"OggSdata", "audio/ogg"),
            response_format="json",
        )
        task = agent.respond.call_args.args[1]
        self.assertIn("User instruction:\nDiagnose and fix it", task)
        self.assertIn("Audio transcript", task)
        self.assertIn("The server returns error 500.", task)
        self.assertNotIn(b"OggSdata", repr(session.agent.input_items).encode())
        replies = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertRegex(replies[0], r"Starting · turn [0-9a-f]{8}")
        self.assertEqual(replies[-1], "Fixed it")
        activity = message.reply_text.return_value
        edited = [call.args[0] for call in activity.edit_text.await_args_list]
        self.assertTrue(any(value.startswith("Transcribing · turn ") for value in edited))
        self.assertTrue(any(value.startswith("Completed · turn ") for value in edited))
        self.assertEqual(message.reply_text.await_args_list[-1].kwargs["parse_mode"], "HTML")
        self.assertIn("gpt-4o-mini-transcribe", session.agent.usage.by_model)
        self.assertEqual([item.phase for item in persisted], ["transcription"])

    async def test_captionless_audio_uses_transcript_directly(self) -> None:
        transcription = SimpleNamespace(text="Run the tests", usage=None)
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=Mock(return_value=transcription))))
        agent = SimpleNamespace(client=client, respond=Mock(return_value="Done"))
        session = ConversationSession()
        telegram_bot.SESSIONS[42] = session
        message = SimpleNamespace(reply_text=AsyncMock())

        with patch("telegram_bot.Agent", return_value=agent), patch(
            "telegram_bot.asyncio.to_thread", new=self.inline_thread
        ):
            await telegram_bot._run_agent(
                message,
                session,
                "",
                42,
                audio=telegram_bot.AudioInput(b"OggSdata", "audio.ogg", "audio/ogg"),
            )

        self.assertEqual(agent.respond.call_args.args[1], "Run the tests")

    async def test_empty_or_failed_transcription_does_not_run_agent(self) -> None:
        for result in (SimpleNamespace(text="   ", usage=None), RuntimeError("secret provider detail")):
            with self.subTest(result=result):
                create = Mock(side_effect=result if isinstance(result, Exception) else None, return_value=None if isinstance(result, Exception) else result)
                client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
                agent = SimpleNamespace(client=client, respond=Mock(return_value="unused"))
                session = ConversationSession()
                telegram_bot.SESSIONS[42] = session
                message = SimpleNamespace(reply_text=AsyncMock())
                with patch("telegram_bot.Agent", return_value=agent), patch(
                    "telegram_bot.asyncio.to_thread", new=self.inline_thread
                ):
                    await telegram_bot._run_agent(
                        message,
                        session,
                        "",
                        42,
                        audio=telegram_bot.AudioInput(b"OggSdata", "audio.ogg", "audio/ogg"),
                    )
                agent.respond.assert_not_called()
                reply = message.reply_text.await_args_list[-1].args[0]
                self.assertNotIn("secret provider detail", reply)
                self.assertFalse(session.running)


class ApprovalReactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        telegram_bot.SESSIONS.clear()

    def tearDown(self) -> None:
        telegram_bot.SESSIONS.clear()

    @staticmethod
    def reaction_update(
        emoji: str | tuple[str, ...],
        *,
        user_id: int | None = 42,
        chat_id: int = 42,
        message_id: int = 100,
        old_emojis: tuple[str, ...] = (),
    ) -> SimpleNamespace:
        new_emojis = (emoji,) if isinstance(emoji, str) else emoji
        return SimpleNamespace(
            message_reaction=SimpleNamespace(
                user=SimpleNamespace(id=user_id) if user_id is not None else None,
                actor_chat=SimpleNamespace(id=chat_id) if user_id is None else None,
                chat=SimpleNamespace(id=chat_id),
                message_id=message_id,
                old_reaction=tuple(SimpleNamespace(emoji=value) for value in old_emojis),
                new_reaction=tuple(SimpleNamespace(emoji=value) for value in new_emojis),
            )
        )

    async def test_thumbs_up_approves_matching_prompt_and_confirms(self) -> None:
        session = ConversationSession()
        request = PendingApproval("approval-1", "write_file", "write notes.txt")
        session.pending_approval = request
        session.bind_approval_prompt(request.request_id, 42, 100)
        telegram_bot.SESSIONS[42] = session
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
            await telegram_bot.approval_reaction(self.reaction_update("👍"), context)

        self.assertTrue(request.approved)
        context.bot.send_message.assert_awaited_once_with(chat_id=42, text="Approved. I’ll continue.")

    async def test_usage_command_reports_current_session_totals(self) -> None:
        session = ConversationSession()
        session.agent.usage.add(ModelUsage("gpt-5.6-luna", "answer", input_tokens=100, output_tokens=20))
        telegram_bot.SESSIONS[42] = session
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)

        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                "telegram_bot.configured_usage_store", return_value=store
            ):
                await telegram_bot.usage_command(update, SimpleNamespace())

        report = message.reply_text.await_args.args[0]
        self.assertIn("Current session", report)
        self.assertIn("gpt-5.6-luna", report)
        self.assertIn("Today (UTC)\nNo model usage recorded.", report)

    async def test_usage_command_reports_durable_usage_without_active_session(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            store.record(42, ModelUsage("gpt-5.6-luna", "answer", input_tokens=100, output_tokens=20))
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                "telegram_bot.configured_usage_store", return_value=store
            ):
                await telegram_bot.usage_command(update, SimpleNamespace())

        report = message.reply_text.await_args.args[0]
        self.assertIn("Current session\nNo model usage recorded.", report)
        self.assertIn("Today (UTC)\nrequests: 1", report)
        self.assertIn("All recorded usage\nrequests: 1", report)

    async def test_usage_command_reports_durable_store_failure(self) -> None:
        store = Mock()
        store.summary.side_effect = OSError("disk unavailable")
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot.configured_usage_store", return_value=store
        ), self.assertLogs("telegram_bot", level="ERROR"):
            await telegram_bot.usage_command(update, SimpleNamespace())

        self.assertIn("Durable usage\nUnavailable", message.reply_text.await_args.args[0])

    async def test_project_selection_resets_usage_totals(self) -> None:
        session = ConversationSession()
        session.agent.usage.add(ModelUsage("gpt-5.6-luna", "answer", input_tokens=100))
        telegram_bot.SESSIONS[42] = session
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"TELEGRAM_ALLOWED_USER_ID": "42", "AGENT_WORKSPACE_ROOT": directory},
        ):
            (Path(directory) / "demo").mkdir()
            await telegram_bot.select_project(update, SimpleNamespace(args=["demo"]))

        self.assertEqual(telegram_bot.SESSIONS[42].agent.usage.format(), "No model usage in the current session.")

    async def test_project_selection_retains_durable_usage_totals(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            (workspace / "demo").mkdir()
            store = UsageStore(Path(directory) / "usage.sqlite3")
            with patch.dict(
                os.environ,
                {"TELEGRAM_ALLOWED_USER_ID": "42", "AGENT_WORKSPACE_ROOT": str(workspace)},
            ), patch("telegram_bot.configured_usage_store", return_value=store):
                session = telegram_bot.session_for(42)
                session.agent.usage.add(ModelUsage("gpt-5.6-luna", "answer", input_tokens=100))
                await telegram_bot.select_project(update, SimpleNamespace(args=["demo"]))

            durable = store.summary(42)

        self.assertEqual(telegram_bot.SESSIONS[42].agent.usage.format(), "No model usage in the current session.")
        self.assertEqual(durable.by_model["gpt-5.6-luna"].requests, 1)

    async def test_new_and_stop_clear_sessions_without_deleting_durable_usage(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                "telegram_bot.configured_usage_store", return_value=store
            ):
                telegram_bot.session_for(42).agent.usage.add(
                    ModelUsage("gpt-5.6-luna", "answer", input_tokens=100)
                )
                await telegram_bot.new_session(update, SimpleNamespace())
                self.assertNotIn(42, telegram_bot.SESSIONS)
                telegram_bot.session_for(42)
                await telegram_bot.stop_session(update, SimpleNamespace())

            durable = store.summary(42)

        self.assertNotIn(42, telegram_bot.SESSIONS)
        self.assertEqual(durable.by_model["gpt-5.6-luna"].requests, 1)

    async def test_thumbs_down_rejects_matching_prompt_and_confirms(self) -> None:
        session = ConversationSession()
        request = PendingApproval("approval-1", "write_file", "write notes.txt")
        session.pending_approval = request
        session.bind_approval_prompt(request.request_id, 42, 100)
        telegram_bot.SESSIONS[42] = session
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
            await telegram_bot.approval_reaction(self.reaction_update("👎"), context)

        self.assertFalse(request.approved)
        context.bot.send_message.assert_awaited_once_with(chat_id=42, text="Rejected. I’ll leave it unchanged.")

    async def test_reaction_must_be_new_authorized_and_on_exact_prompt(self) -> None:
        ignored_updates = (
            self.reaction_update("👍", user_id=7),
            self.reaction_update("👍", user_id=None),
            self.reaction_update("👍", chat_id=7),
            self.reaction_update("👍", message_id=99),
            self.reaction_update("👍", old_emojis=("👍",)),
            self.reaction_update(("👍", "👎")),
            self.reaction_update("❤️"),
        )

        for update in ignored_updates:
            with self.subTest(update=update):
                session = ConversationSession()
                request = PendingApproval("approval-1", "write_file", "write notes.txt")
                session.pending_approval = request
                session.bind_approval_prompt(request.request_id, 42, 100)
                telegram_bot.SESSIONS[42] = session
                context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
                    await telegram_bot.approval_reaction(update, context)
                self.assertIsNone(request.approved)
                context.bot.send_message.assert_not_awaited()

    def test_main_explicitly_requests_message_reaction_updates(self) -> None:
        application = SimpleNamespace(run_polling=unittest.mock.Mock())
        with patch("telegram_bot.build_application", return_value=application), patch.dict(
            os.environ, {"AGENT_BACKEND": "responses"}
        ):
            self.assertEqual(telegram_bot.main([]), 0)

        application.run_polling.assert_called_once_with(allowed_updates=("message", "message_reaction"))

    def test_application_registers_reaction_handler(self) -> None:
        from telegram.ext import CommandHandler, MessageHandler, MessageReactionHandler

        application = telegram_bot.build_application(
            {
                "TELEGRAM_BOT_TOKEN": "123:token",
                "TELEGRAM_ALLOWED_USER_ID": "42",
                "AGENT_BACKEND": "responses",
            }
        )
        handlers = [handler for group in application.handlers.values() for handler in group]

        reaction_handlers = [handler for handler in handlers if isinstance(handler, MessageReactionHandler)]
        self.assertEqual(len(reaction_handlers), 1)
        self.assertIs(reaction_handlers[0].callback, telegram_bot.approval_reaction)
        command_callbacks = {handler.callback for handler in handlers if isinstance(handler, CommandHandler)}
        self.assertIn(telegram_bot.usage_command, command_callbacks)
        self.assertIn(telegram_bot.prompt_command, command_callbacks)
        self.assertIn(telegram_bot.trace_command, command_callbacks)
        self.assertIn(telegram_bot.traces_command, command_callbacks)
        message_callbacks = {handler.callback for handler in handlers if isinstance(handler, MessageHandler)}
        self.assertIn(telegram_bot.image_message, message_callbacks)
        self.assertIn(telegram_bot.audio_message, message_callbacks)


class TransparencyCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        telegram_bot.SESSIONS.clear()

    def tearDown(self) -> None:
        telegram_bot.SESSIONS.clear()

    @staticmethod
    def update(message, user_id=42):
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=message)

    async def test_natural_prompt_request_deterministically_exports_live_prompt(self) -> None:
        message = SimpleNamespace(text="What's your prompt?", reply_text=AsyncMock(), reply_document=AsyncMock())
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"TELEGRAM_ALLOWED_USER_ID": "42", "TRACE_DB_PATH": str(Path(directory) / "traces.sqlite3")},
        ), patch("telegram_bot._run_agent", new_callable=AsyncMock) as run_agent:
            await telegram_bot.conversational_message(self.update(message), SimpleNamespace())

        run_agent.assert_not_awaited()
        document = message.reply_document.await_args.kwargs["document"]
        exported = json.loads(document.getvalue())
        self.assertIn("You are a personal computer agent", exported["system_prompt"])
        self.assertIn("conservative request router", exported["router_prompt"])
        self.assertIn("tool_definitions", exported)
        self.assertIn("provider_limits", exported)

    async def test_trace_export_is_owner_only_and_can_export_running_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from owner_trace import TraceStore
            store = TraceStore(Path(directory) / "traces.sqlite3")
            store.start_turn("abc12345", 42, project="demo", kind="conversation")
            allowed_message = SimpleNamespace(reply_text=AsyncMock(), reply_document=AsyncMock())
            denied_message = SimpleNamespace(reply_text=AsyncMock(), reply_document=AsyncMock())
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                "telegram_bot.configured_trace_store", return_value=store
            ):
                await telegram_bot.trace_command(self.update(allowed_message), SimpleNamespace(args=["abc12345"]))
                await telegram_bot.trace_command(self.update(denied_message, 7), SimpleNamespace(args=["abc12345"]))

        compressed = allowed_message.reply_document.await_args.kwargs["document"].getvalue()
        exported = json.loads(gzip.decompress(compressed))
        self.assertEqual(exported["turn"]["status"], "running")
        denied_message.reply_document.assert_not_awaited()
        denied_message.reply_text.assert_not_awaited()

    async def test_large_trace_export_is_split_without_dropping_bytes(self) -> None:
        payload = {
            "turn": {"turn_id": "split123"},
            "events": [{"sequence": 1, "data": "value" * 1000}],
        }
        expected = gzip.compress(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode(), mtime=0)
        message = SimpleNamespace(reply_document=AsyncMock())
        with patch.dict(os.environ, {"TRACE_EXPORT_PART_BYTES": "17"}):
            await telegram_bot._send_trace_document(message, payload)

        parts = [call.kwargs["document"].getvalue() for call in message.reply_document.await_args_list]
        names = [call.kwargs["filename"] for call in message.reply_document.await_args_list]
        self.assertGreater(len(parts), 1)
        self.assertEqual(b"".join(parts), expected)
        self.assertTrue(all("part" in name for name in names))


if __name__ == "__main__":
    unittest.main()
