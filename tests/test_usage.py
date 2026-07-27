import unittest
from types import SimpleNamespace

from usage import ModelUsage, SessionUsage


class UsageTests(unittest.TestCase):
    def test_extracts_response_usage_and_estimates_cached_cost(self) -> None:
        response = SimpleNamespace(
            model="gpt-5.6-luna",
            output=[SimpleNamespace(type="web_search_call"), SimpleNamespace(type="message")],
            usage=SimpleNamespace(
                input_tokens=1000,
                input_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=100),
                output_tokens=100,
                output_tokens_details=SimpleNamespace(reasoning_tokens=20),
            ),
        )

        usage = ModelUsage.from_response(response, "requested", "answer")

        self.assertEqual(usage.input_tokens, 1000)
        self.assertEqual(usage.cached_input_tokens, 200)
        self.assertEqual(usage.cache_write_tokens, 100)
        self.assertEqual(usage.reasoning_tokens, 20)
        self.assertEqual(usage.web_search_calls, 1)
        self.assertAlmostEqual(usage.estimated_cost_usd, 0.011445)

    def test_accepts_dictionary_usage_and_model_snapshots(self) -> None:
        response = {
            "model": "gpt-5-nano-2025-08-07",
            "output": [],
            "usage": {
                "input_tokens": 1_000_000,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {},
            },
        }
        usage = ModelUsage.from_response(response, "requested", "router")
        self.assertAlmostEqual(usage.estimated_cost_usd, 0.05)

    def test_prices_dated_default_model_as_sol(self) -> None:
        usage = ModelUsage("gpt-5.6-2026-07-01", "answer", input_tokens=1_000_000)
        self.assertAlmostEqual(usage.estimated_cost_usd, 5.0)

    def test_transcription_tokens_are_counted_with_unknown_pricing(self) -> None:
        response = SimpleNamespace(
            model="gpt-4o-mini-transcribe",
            usage=SimpleNamespace(input_tokens=120, output_tokens=18),
            output=(),
        )

        usage = ModelUsage.from_response(response, "gpt-4o-mini-transcribe", "transcription")
        session = SessionUsage()
        session.add(usage)

        self.assertEqual((usage.phase, usage.input_tokens, usage.output_tokens), ("transcription", 120, 18))
        self.assertIn("unknown pricing", session.format())

    def test_session_format_reports_unknown_prices_and_warnings(self) -> None:
        session = SessionUsage()
        session.add(ModelUsage("custom-model", "answer", input_tokens=10, output_tokens=2))
        session.mark_warning()

        formatted = session.format()

        self.assertIn("requests: 1", formatted)
        self.assertIn("unknown pricing", formatted)
        self.assertIn("high-usage turn warnings: 1", formatted)


if __name__ == "__main__":
    unittest.main()
