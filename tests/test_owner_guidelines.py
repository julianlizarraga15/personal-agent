import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OwnerGuidelineTests(unittest.TestCase):
    def test_agent_prompt_identifies_owner_as_julian(self) -> None:
        source = (ROOT / "src/agent.py").read_text(encoding="utf-8")
        self.assertIn("owner's name is Julián", source)
        self.assertIn("call him Julián", source)

    def test_router_prompt_identifies_owner_as_julian(self) -> None:
        source = (ROOT / "src/router.py").read_text(encoding="utf-8")
        self.assertIn("owner's name is Julián", source)
        self.assertIn("call him Julián", source)

    def test_task_start_acknowledgements_say_working(self) -> None:
        source = (ROOT / "src/telegram_bot.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('reply_text("Working...")'), 2)
        self.assertNotIn('reply_text("Task received.")', source)


if __name__ == "__main__":
    unittest.main()
