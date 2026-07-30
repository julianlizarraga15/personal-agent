import unittest
from unittest.mock import patch

from codex_runtime import sanitized_environment


class CodexRuntimeTests(unittest.TestCase):
    def test_launcher_does_not_inherit_bot_secrets_or_proxy_settings(self):
        source = {
            "CODEX_HOME": "/codex-home",
            "LANG": "C.UTF-8",
            "TELEGRAM_BOT_TOKEN": "secret",
            "OPENAI_API_KEY": "secret",
            "GITHUB_TOKEN": "secret",
            "HTTPS_PROXY": "http://secret-proxy",
        }
        with patch("codex_runtime.bundled_path_dir", return_value="/opt/codex"):
            clean = sanitized_environment(source)

        self.assertEqual(clean["CODEX_HOME"], "/codex-home")
        self.assertEqual(clean["HOME"], "/tmp/codex-runtime-home")
        self.assertTrue(clean["PATH"].startswith("/opt/codex:"))
        for key in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "GITHUB_TOKEN", "HTTPS_PROXY"):
            self.assertNotIn(key, clean)


if __name__ == "__main__":
    unittest.main()
