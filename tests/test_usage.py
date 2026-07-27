from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace

from usage import ModelUsage, SessionUsage, UsageStore


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

    def test_store_survives_reopen_and_filters_by_user_and_utc_time(self) -> None:
        now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            store = UsageStore(path)
            store.record(42, ModelUsage("gpt-5.6-luna", "answer", input_tokens=100, output_tokens=20), recorded_at=now)
            store.record(42, ModelUsage("gpt-5-nano", "router", input_tokens=10, output_tokens=2), recorded_at=now - timedelta(days=1))
            store.record(7, ModelUsage("gpt-5.6-sol", "answer", input_tokens=999), recorded_at=now)

            reopened = UsageStore(path)
            recent = reopened.summary(42, since=now.replace(hour=0))
            lifetime = reopened.summary(42)

        self.assertEqual(recent.by_model["gpt-5.6-luna"].requests, 1)
        self.assertNotIn("gpt-5-nano", recent.by_model)
        self.assertEqual(set(lifetime.by_model), {"gpt-5-nano", "gpt-5.6-luna"})
        self.assertEqual(sum(item.requests for item in lifetime.by_model.values()), 2)

    def test_store_preserves_unknown_pricing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            store.record(42, ModelUsage("custom-model", "answer", input_tokens=10))

            report = store.summary(42).format("All recorded usage")

        self.assertIn("unknown pricing", report)

    def test_store_accepts_concurrent_writes_without_losing_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = UsageStore(Path(directory) / "usage.sqlite3")
            errors: list[Exception] = []

            def record() -> None:
                try:
                    store.record(42, ModelUsage("gpt-5-nano", "router", input_tokens=1))
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [threading.Thread(target=record) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            summary = store.summary(42)

        self.assertEqual(errors, [])
        self.assertEqual(summary.by_model["gpt-5-nano"].requests, 20)

    def test_session_usage_keeps_counting_when_persistence_fails(self) -> None:
        def fail(_usage: ModelUsage) -> None:
            raise OSError("disk unavailable")

        session = SessionUsage(recorder=fail)
        with self.assertLogs("usage", level="ERROR"):
            session.add(ModelUsage("gpt-5.6-luna", "answer", input_tokens=10))

        self.assertEqual(session.billed_tokens(), 10)
        self.assertEqual(session.persistence_errors, 1)


if __name__ == "__main__":
    unittest.main()
