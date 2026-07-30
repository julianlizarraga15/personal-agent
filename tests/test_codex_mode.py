import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram.ext import CommandHandler, MessageHandler, MessageReactionHandler

import telegram_bot


class CodexModeTests(unittest.IsolatedAsyncioTestCase):
    def test_codex_is_default_and_only_pass_one_handlers_are_registered(self):
        application = telegram_bot.build_application(
            {"TELEGRAM_BOT_TOKEN": "123:token", "TELEGRAM_ALLOWED_USER_ID": "42"}
        )
        handlers = [handler for group in application.handlers.values() for handler in group]
        command_callbacks = {handler.callback for handler in handlers if isinstance(handler, CommandHandler)}
        message_callbacks = {handler.callback for handler in handlers if isinstance(handler, MessageHandler)}

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
            {telegram_bot.codex_media_message, telegram_bot.codex_conversational_message},
        )
        self.assertFalse(any(isinstance(handler, MessageReactionHandler) for handler in handlers))

    def test_invalid_backend_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "AGENT_BACKEND"):
            telegram_bot.selected_backend({"AGENT_BACKEND": "unknown"})

    async def test_media_is_rejected_without_download(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=message)
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USER_ID": "42"}):
            await telegram_bot.codex_media_message(update, SimpleNamespace())
        message.reply_text.assert_awaited_once()
        self.assertIn("not supported in pass 1", message.reply_text.await_args.args[0])

    def test_compose_isolates_codex_bot(self):
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        bot = compose.split("  bot:", 1)[1].split("  deployer:", 1)[0]
        self.assertIn("AGENT_BACKEND: ${AGENT_BACKEND:-codex}", bot)
        self.assertIn("codex-state:/codex-home", bot)
        self.assertNotIn("/var/run/docker.sock", bot)
        self.assertNotIn("github_key", bot)
        self.assertNotIn("GIT_SSH_COMMAND", bot)
        self.assertNotIn("OPENAI_API_KEY", bot)


if __name__ == "__main__":
    unittest.main()
