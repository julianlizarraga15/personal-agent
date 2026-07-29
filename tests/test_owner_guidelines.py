import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OwnerGuidelineTests(unittest.TestCase):
    def test_agent_prompt_identifies_agent_as_cornelio(self) -> None:
        source = (ROOT / "src/agent.py").read_text(encoding="utf-8")
        self.assertIn("Your name is Cornelio", source)
        self.assertIn("identify yourself as Cornelio", source)

    def test_router_prompt_identifies_agent_as_cornelio(self) -> None:
        source = (ROOT / "src/router.py").read_text(encoding="utf-8")
        self.assertIn("agent's name is Cornelio", source)
        self.assertIn("answer Cornelio", source)

    def test_agent_prompt_identifies_owner_as_julian(self) -> None:
        source = (ROOT / "src/agent.py").read_text(encoding="utf-8")
        self.assertIn("owner's name is Julián", source)
        self.assertIn("call him Julián", source)
        self.assertNotIn("owner's name is Daniel", source)
        self.assertNotIn("call him Daniel", source)

    def test_router_prompt_identifies_owner_as_julian(self) -> None:
        source = (ROOT / "src/router.py").read_text(encoding="utf-8")
        self.assertIn("owner's name is Julián", source)
        self.assertIn("call him Julián", source)
        self.assertNotIn("owner's name is Daniel", source)
        self.assertNotIn("call him Daniel", source)

    def test_task_start_acknowledgements_use_live_turn_ids(self) -> None:
        source = (ROOT / "src/telegram_bot.py").read_text(encoding="utf-8")
        self.assertNotIn('reply_text("Working...")', source)
        self.assertIn('f"Starting · turn {turn_id}"', source)
        self.assertIn('f"Starting legacy worker · turn {turn_id}"', source)


if __name__ == "__main__":
    unittest.main()
