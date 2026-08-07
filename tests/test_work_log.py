import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from work_log import record_work


class WorkLogTests(unittest.TestCase):
    def test_record_work_appends_a_concise_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            result = record_work(
                project, source="Telegram", request="edit the README\nignore this heading",
                outcome="completed", response="Done\nwith details",
            )
            self.assertEqual(result, project / "WORK_LOG.md")
            content = (project / "WORK_LOG.md").read_text(encoding="utf-8")
            self.assertIn("## ", content)
            self.assertIn("Request: edit the README ignore this heading", content)
            self.assertIn("Outcome: completed", content)
            self.assertIn("Git status: unavailable", content)

    def test_filename_can_be_configured_but_cannot_escape_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with patch.dict("os.environ", {"AGENT_WORK_LOG_FILENAME": "notes.md"}):
                record_work(project, source="chat", request="task", outcome="completed")
            self.assertTrue((project / "notes.md").exists())
            with patch.dict("os.environ", {"AGENT_WORK_LOG_FILENAME": "../outside.md"}):
                record_work(project, source="chat", request="task", outcome="completed")
            self.assertTrue((project / "WORK_LOG.md").exists())
