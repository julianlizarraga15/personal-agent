import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, MessageReactionHandler

import telegram_bot
from codex_backend import NetworkApprovalRequest


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
            {telegram_bot.codex_media_message, telegram_bot.codex_conversational_message},
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


if __name__ == "__main__":
    unittest.main()
