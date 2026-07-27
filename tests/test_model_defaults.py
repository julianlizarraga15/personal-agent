import os
import unittest
from unittest.mock import patch

from agent import Agent


class ModelDefaultTests(unittest.TestCase):
    def test_three_tier_models_have_code_defaults_without_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(client=object(), router_enabled=False)

        self.assertEqual(agent.intermediate_model, "gpt-5.6-terra")
        self.assertEqual(agent.model, "gpt-5.6")


if __name__ == "__main__":
    unittest.main()
