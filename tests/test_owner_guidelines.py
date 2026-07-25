import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OwnerGuidelineTests(unittest.TestCase):
    def test_agent_prompt_identifies_owner_as_julian(self) -> None:
        source = (ROOT / "src/agent.py").read_text(encoding="utf-8")
        self.assertIn("owner's name is Julian", source)
        self.assertIn("call him Julian", source)

    def test_router_prompt_identifies_owner_as_julian(self) -> None:
        source = (ROOT / "src/router.py").read_text(encoding="utf-8")
        self.assertIn("owner's name is Julian", source)
        self.assertIn("call him Julian", source)


if __name__ == "__main__":
    unittest.main()
