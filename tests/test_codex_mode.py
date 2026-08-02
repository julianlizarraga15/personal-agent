import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, MessageReactionHandler

import telegram_bot
from codex_backend import CodexBusyError, CodexTurnReservation, NetworkApprovalRequest


class CodexModeTests(unittest.IsolatedAsyncioTestCase):
    def test_codex_is_default_and_only_pass_one_handlers_are_registered(self):
        application = telegram_bot.build_application(
            {"TELEGRAM_BOT_TOKEN": "123:token", "TELEGRAM_ALLOWED_USER_ID": "42"}
        )
        handlers = [handler for group in application.handlers.values() for handler in group]
        command_callbacks = {handler.callback for handler in handlers if isinstance(handler, CommandHandler)}
        message_callbacks = {handler.callback for handler in handlers if isinstance(handler, MessageHandler)}
        callback_callbacks = {handler.callback for handler in handlers if isinstance(handler, CallbackQueryHandler)}

        self.assertEqual(
            command_callbacks,
            {
                telegram_bot.codex_select_project,
                telegram_bot.codex_new_session,
                telegram_bot.codex_stop_session,
                telegram_bot.codex_help_command,
            },
        )
        self.assertEqual(
            message_callbacks,
            {
                telegram_bot.codex_media_message,
                telegram_bot.codex_audio_message,
                telegram_bot.codex_conversational_message,
            },
        )
        self.assertFalse(any(isinstance(handler, MessageReactionHandler) for handler in handlers))
        self.assertEqual(callback_callbacks, {telegram_bot.codex_network_approval})

    def test_invalid_backend_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "AGENT_BACKEND"):
            telegram_bot.selected_backend({"AGENT_BACKEND": "unknown"})

    async def test_media_is_rejected_without_download(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
            await telegram_bot.codex_media_message(update, SimpleNamespace())
        message.reply_text.assert_awaited_once()
        self.assertIn("images are not supported", message.reply_text.await_args.args[0])

    def test_compose_isolates_codex_bot(self):
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        bot = compose.split("  bot:", 1)[1].split("  deployer:", 1)[0]
        self.assertIn("AGENT_BACKEND: ${AGENT_BACKEND:-codex}", bot)
        self.assertIn("codex-state:/codex-home", bot)
        self.assertNotIn("/var/run/docker.sock", bot)
        self.assertNotIn("github_key", bot)
        self.assertNotIn("GIT_SSH_COMMAND", bot)
        self.assertNotIn("OPENAI_API_KEY", bot)
        self.assertIn("API_FOOTBALL_KEY: ${API_FOOTBALL_KEY:-}", bot)
        self.assertIn("GIT_PUBLISH_REPOSITORY: ${GIT_PUBLISH_REPOSITORY:-}", bot)
        self.assertNotIn("deploy-key", bot.split("volumes:", 1)[1] if "volumes:" in bot else "")
        self.assertNotIn("DEPLOYMENT_STATE_DIR", bot)
        self.assertIn("seccomp=./security/codex-bwrap-seccomp.json", bot)
        self.assertIn("trace-state:/trace-state", bot)
        self.assertNotIn("cap_add", bot)
        self.assertNotIn("privileged:", bot)
        self.assertNotIn("unconfined", bot)

        backend = Path("src/codex_backend.py").read_text(encoding="utf-8")
        self.assertNotIn("dangerously_allow_all_unix_sockets", backend)
        self.assertNotIn("network.unix_sockets", backend)
        self.assertIn("api-football-mcp", backend)
        self.assertIn("git-publish-mcp", backend)
        self.assertIn('mcp_servers.git-publish.tools.publish.approval_mode="approve"', backend)
        self.assertIn("network.enabled=false", backend)
        self.assertIn('"/openai-transcription-secrets"="deny"', backend)

        transcription = Path("docker-compose.transcription.example.yml").read_text(encoding="utf-8")
        self.assertIn("OPENAI_TRANSCRIPTION_SECRETS_DIR", transcription)
        self.assertIn("target: /openai-transcription-secrets", transcription)
        self.assertIn("read_only: true", transcription)

        deployer = compose.split("  deployer:", 1)[1].split("  codex-login:", 1)[0]
        self.assertIn('profiles: ["manual-deployer"]', deployer)
        self.assertIn("deployer-state:/deployment-state", deployer)
        self.assertIn("DEPLOYMENT_STATE_DIR: /deployment-state", deployer)
        self.assertIn("DEPLOY_REMOTE_URL:", deployer)
        self.assertNotIn("/workspace/.personal-agent-state", deployer)
        self.assertNotIn("API_FOOTBALL_KEY", deployer)

        login = compose.split("  codex-login:", 1)[1].split("volumes:", 1)[0]
        self.assertNotIn("API_FOOTBALL_KEY", login)

    async def test_codex_startup_starts_gateway_before_backend_and_cleans_up_on_failure(self):
        order = []
        gateway = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: order.append("gateway-start")),
            close=AsyncMock(side_effect=lambda: order.append("gateway-close")),
        )
        publish_gateway = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: order.append("publish-start")),
            close=AsyncMock(side_effect=lambda: order.append("publish-close")),
        )
        backend = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("failed")))
        application = SimpleNamespace(
            bot_data={
                telegram_bot.API_FOOTBALL_GATEWAY_KEY: gateway,
                telegram_bot.GIT_PUBLISH_GATEWAY_KEY: publish_gateway,
                telegram_bot.CODEX_BACKEND_KEY: backend,
            },
            bot=SimpleNamespace(set_my_commands=AsyncMock()),
        )

        with self.assertRaisesRegex(RuntimeError, "failed"):
            await telegram_bot.start_codex_application(application)

        self.assertEqual(order, ["gateway-start", "publish-start", "publish-close", "gateway-close"])
        backend.start.assert_awaited_once()

    async def test_network_approval_is_owner_bound_and_one_shot(self):
        prompt_message = SimpleNamespace(edit_text=AsyncMock())
        source_message = SimpleNamespace(reply_text=AsyncMock(return_value=prompt_message))
        broker = telegram_bot.CodexApprovalBroker()
        request = NetworkApprovalRequest(
            method="item/commandExecution/requestApproval",
            thread_id="thread-1",
            turn_id="turn-1",
            host="pypi.org",
            protocol="https",
            port=443,
            reason="Install declared dependencies.",
            cwd="/workspace/project",
        )

        task = asyncio.create_task(broker.request(source_message, 42, request))
        await asyncio.sleep(0)
        token = next(iter(broker.pending))
        self.assertFalse(broker.resolve(token, 7, True))
        self.assertTrue(broker.resolve(token, 42, True))
        self.assertTrue(await task)
        self.assertFalse(broker.resolve(token, 42, True))
        self.assertIn("https://pypi.org:443", source_message.reply_text.await_args.args[0])


class CodexAudioTests(unittest.IsolatedAsyncioTestCase):
    OGG = b"OggS" + b"\x00" * 32

    @staticmethod
    def media(data: bytes, *, size: int | None = None, duration: int | None = 8) -> SimpleNamespace:
        downloaded = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
        return SimpleNamespace(
            file_size=len(data) if size is None else size,
            duration=duration,
            get_file=AsyncMock(return_value=downloaded),
        )

    @staticmethod
    def setup(media: SimpleNamespace, *, caption: str | None = None):
        activity = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(
            voice=media,
            audio=None,
            document=None,
            caption=caption,
            chat_id=42,
            reply_text=AsyncMock(return_value=activity),
        )
        session = SimpleNamespace(turn_lock=asyncio.Lock())
        reservation = CodexTurnReservation(42, session)
        backend = SimpleNamespace(
            sessions={42: session},
            reserve_turn=AsyncMock(return_value=reservation),
            release_turn=Mock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={telegram_bot.CODEX_BACKEND_KEY: backend})
        )
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
        return update, context, message, backend, reservation

    async def test_captioned_and_captionless_audio_submit_only_transcript_text(self):
        for caption, expected in (
            ("Summarize this", "User instruction:\nSummarize this\n\nAudio transcript (user-provided speech):\nhello"),
            (None, "hello"),
        ):
            with self.subTest(caption=caption):
                media = self.media(self.OGG)
                update, context, _, backend, reservation = self.setup(media, caption=caption)
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
                    "telegram_bot._codex_transcription_client", return_value=object()
                ), patch(
                    "telegram_bot.asyncio.to_thread", new_callable=AsyncMock,
                    return_value=SimpleNamespace(text="hello"),
                ), patch("telegram_bot._run_codex_turn", new_callable=AsyncMock) as run, self.assertLogs(
                    "telegram_bot", level="INFO"
                ) as logs:
                    await telegram_bot.codex_audio_message(update, context)

                media.get_file.assert_awaited_once()
                self.assertEqual(run.await_args.args[3], expected)
                self.assertNotIn(self.OGG, run.await_args.args)
                self.assertNotIn("hello", "\n".join(logs.output))
                self.assertNotIn(repr(self.OGG), "\n".join(logs.output))
                self.assertIs(run.await_args.kwargs["reservation"], reservation)
                backend.release_turn.assert_called_once_with(reservation)

    async def test_limits_unauthorized_and_busy_reject_before_download(self):
        cases = (
            (7, self.media(self.OGG), None, False),
            (42, self.media(self.OGG, size=11), {"TELEGRAM_MAX_AUDIO_BYTES": "10"}, False),
            (42, self.media(self.OGG, duration=11), {"TELEGRAM_MAX_AUDIO_SECONDS": "10"}, False),
            (42, self.media(self.OGG), None, True),
        )
        for user_id, media, extra, busy in cases:
            with self.subTest(user_id=user_id, busy=busy, extra=extra):
                update, context, message, backend, _ = self.setup(media)
                update.effective_user.id = user_id
                if busy:
                    backend.reserve_turn.side_effect = CodexBusyError("I’m still working on your previous request.")
                environment = {"TELEGRAM_ALLOWED_USER_ID": "42", **(extra or {})}
                with patch.dict(os.environ, environment, clear=False):
                    await telegram_bot.codex_audio_message(update, context)
                media.get_file.assert_not_awaited()
                if user_id == 7:
                    backend.reserve_turn.assert_not_awaited()
                    message.reply_text.assert_not_awaited()
                elif busy:
                    self.assertIn("still working", message.reply_text.await_args.args[0])

    async def test_actual_limit_invalid_format_and_download_failure_are_stable(self):
        cases = (
            (self.media(self.OGG, size=1), {"TELEGRAM_MAX_AUDIO_BYTES": "10"}, "too large"),
            (self.media(b"not audio"), {}, "couldn’t use"),
        )
        for media, extra, expected in cases:
            with self.subTest(expected=expected):
                update, context, message, _, _ = self.setup(media)
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42", **extra}):
                    await telegram_bot.codex_audio_message(update, context)
                self.assertIn(expected, message.reply_text.await_args.args[0].lower())

        media = self.media(self.OGG)
        media.get_file.side_effect = RuntimeError("private telegram failure")
        update, context, message, _, _ = self.setup(media)
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
            await telegram_bot.codex_audio_message(update, context)
        self.assertNotIn("private telegram failure", message.reply_text.await_args.args[0])

    async def test_missing_key_provider_failure_and_empty_speech_are_stable(self):
        cases = (
            (ValueError("missing secret"), None, "isn’t configured"),
            (None, RuntimeError("raw provider secret"), "couldn’t transcribe"),
            (None, SimpleNamespace(text="  "), "couldn’t detect"),
        )
        for client_error, transcription, expected in cases:
            with self.subTest(expected=expected):
                update, context, message, _, _ = self.setup(self.media(self.OGG))
                client_patch = patch("telegram_bot._codex_transcription_client")
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), client_patch as client, patch(
                    "telegram_bot._run_codex_turn", new_callable=AsyncMock
                ) as run, self.assertLogs("telegram_bot", level="INFO") as logs:
                    if client_error is not None:
                        client.side_effect = client_error
                    else:
                        client.return_value = object()
                    with patch("telegram_bot.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
                        if isinstance(transcription, Exception):
                            to_thread.side_effect = transcription
                        else:
                            to_thread.return_value = transcription
                        await telegram_bot.codex_audio_message(update, context)
                reply_texts = [call.args[0] for call in message.reply_text.await_args_list]
                self.assertTrue(any(expected in value for value in reply_texts))
                self.assertNotIn("raw provider secret", "\n".join(logs.output))
                run.assert_not_awaited()

    async def test_reset_during_transcription_discards_result(self):
        update, context, _, backend, _ = self.setup(self.media(self.OGG))

        async def reset_during_call(*args):
            backend.sessions[42] = SimpleNamespace(turn_lock=asyncio.Lock())
            return SimpleNamespace(text="do not submit")

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}), patch(
            "telegram_bot._codex_transcription_client", return_value=object()
        ), patch("telegram_bot.asyncio.to_thread", new=reset_during_call), patch(
            "telegram_bot._run_codex_turn", new_callable=AsyncMock
        ) as run:
            await telegram_bot.codex_audio_message(update, context)
        run.assert_not_awaited()

    def test_dedicated_key_file_constructs_client_without_environment_key(self):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "api-key"
            key_path.write_text("sk-test-dedicated\n", encoding="utf-8")
            with patch("telegram_bot.DEFAULT_TRANSCRIPTION_KEY_PATH", str(key_path)), patch.dict(
                os.environ, {"OPENAI_API_KEY": "wrong"}
            ), patch("openai.OpenAI", return_value=object()) as client:
                telegram_bot._codex_transcription_client()
        client.assert_called_once_with(api_key="sk-test-dedicated")


if __name__ == "__main__":
    unittest.main()
