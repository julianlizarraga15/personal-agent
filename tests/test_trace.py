import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from owner_trace import TraceStore, binary_metadata, redact


class RedactionTests(unittest.TestCase):
    def test_recursively_redacts_credentials_tokens_env_and_binary(self) -> None:
        payload = {
            "api_key": "sk-proj-supersecretvalue",
            "headers": {"Authorization": "Bearer abcdefghijklmnop"},
            "message": "token sk-proj-abcdefghijklmnop and 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc",
            "file": {"path": ".env", "content": "OPENAI_API_KEY=never-store-this\nSAFE=yes"},
            "image": b"raw-image",
        }

        cleaned = redact(payload)
        serialized = json.dumps(cleaned)

        self.assertNotIn("supersecretvalue", serialized)
        self.assertNotIn("abcdefghijklmnop", serialized)
        self.assertNotIn("never-store-this", serialized)
        self.assertEqual(cleaned["api_key"], "[REDACTED]")
        self.assertEqual(cleaned["file"]["content"], "[REDACTED .env content]")
        self.assertEqual(cleaned["image"], binary_metadata(b"raw-image"))


class TraceStoreTests(unittest.TestCase):
    def test_persists_ordered_partial_and_completed_owner_scoped_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.sqlite3"
            store = TraceStore(path)
            recorder = store.start_turn("turn1", 42, project="demo", kind="conversation", data={"input": "hello"})
            recorder.event("router.decision", {"route": "small"}, route="small", model="nano")

            partial = TraceStore(path).export_turn(42, "turn1")
            self.assertEqual(partial["turn"]["status"], "running")
            self.assertEqual([event["sequence"] for event in partial["events"]], [1, 2])
            self.assertIsNone(store.export_turn(7, "turn1"))

            recorder.finish("completed")
            exported = store.export_turn(42, "turn1")
            self.assertEqual(exported["turn"]["status"], "completed")
            self.assertEqual(exported["turn"]["models"], ["nano"])
            self.assertEqual([event["type"] for event in exported["events"]], ["turn.started", "router.decision", "turn.finished"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_events_receive_unique_ordered_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(Path(directory) / "traces.sqlite3")
            recorder = store.start_turn("parallel", 42, project=None, kind="conversation")
            threads = [threading.Thread(target=recorder.event, args=("event", {"index": index})) for index in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            exported = store.export_turn(42, "parallel")
            sequences = [event["sequence"] for event in exported["events"]]
            self.assertEqual(sequences, list(range(1, 22)))

    def test_expired_turns_are_purged_during_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.sqlite3"
            store = TraceStore(path, retention_days=7)
            store.start_turn("old", 42, project=None, kind="conversation")
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE trace_turns SET started_at='2000-01-01T00:00:00+00:00' WHERE turn_id='old'")
            current = store.start_turn("current", 42, project=None, kind="conversation")
            current.event("event")

            self.assertIsNone(store.export_turn(42, "old"))
            self.assertEqual([item["turn_id"] for item in store.list_turns(42)], ["current"])


if __name__ == "__main__":
    unittest.main()
