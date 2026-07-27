import base64
from io import BytesIO
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

from PIL import Image

import telegram_bot
from telegram_bot import DEFAULT_IMAGE_PROMPT, ConversationSession, PendingApproval, WorkerExecutionError, _deployment_report, _monitor_deployment, _queued_deployment, _validate_image, required_settings, run_docker_worker, workspace_project_path
from usage import ModelUsage


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

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
            await telegram_bot.usage_command(update, SimpleNamespace())

        report = message.reply_text.await_args.args[0]
        self.assertIn("Usage for this session", report)
        self.assertIn("gpt-5.6-luna", report)

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
        with patch("telegram_bot.build_application", return_value=application):
            self.assertEqual(telegram_bot.main([]), 0)

        application.run_polling.assert_called_once_with(allowed_updates=("message", "message_reaction"))

    def test_application_registers_reaction_handler(self) -> None:
        from telegram.ext import CommandHandler, MessageHandler, MessageReactionHandler

        application = telegram_bot.build_application(
            {"TELEGRAM_BOT_TOKEN": "123:token", "TELEGRAM_ALLOWED_USER_ID": "42"}
        )
        handlers = [handler for group in application.handlers.values() for handler in group]

        reaction_handlers = [handler for handler in handlers if isinstance(handler, MessageReactionHandler)]
        self.assertEqual(len(reaction_handlers), 1)
        self.assertIs(reaction_handlers[0].callback, telegram_bot.approval_reaction)
        command_callbacks = {handler.callback for handler in handlers if isinstance(handler, CommandHandler)}
        self.assertIn(telegram_bot.usage_command, command_callbacks)
        message_callbacks = {handler.callback for handler in handlers if isinstance(handler, MessageHandler)}
        self.assertIn(telegram_bot.image_message, message_callbacks)


if __name__ == "__main__":
    unittest.main()
