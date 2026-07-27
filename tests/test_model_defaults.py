import os
import unittest
from unittest.mock import patch

from agent import Agent


class ModelDefaultTests(unittest.TestCase):
    def test_cost_aware_models_have_code_defaults_without_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(client=object())

        self.assertEqual(agent.router.model, "gpt-5-nano")
        self.assertEqual(agent.economy_model, "gpt-5.6-luna")
        self.assertEqual(agent.intermediate_model, "gpt-5.6-terra")
        self.assertEqual(agent.model, "gpt-5.6")
        self.assertEqual(agent.economy_reasoning_effort, "low")
        self.assertEqual(agent.intermediate_reasoning_effort, "low")
        self.assertEqual(agent.reasoning_effort, "medium")
        self.assertEqual(agent.text_verbosity, "low")


if __name__ == "__main__":
    unittest.main()
