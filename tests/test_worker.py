import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker import CommandResult, execute_workflow, repository_tree, run, run_workflow


class WorkerTests(unittest.TestCase):
    def test_repository_tree_excludes_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            tmp_path = Path(workspace)
            (tmp_path / "README.md").write_text("hello")
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text("print('hi')")
            (tmp_path / ".git").mkdir()
            (tmp_path / ".git" / "config").write_text("internal")

            self.assertEqual(repository_tree(tmp_path), ["README.md", "src/main.py"])


    def test_run_clones_and_formats_task_and_tree(self) -> None:
        def fake_clone(_repo: str, destination: Path) -> None:
            destination.mkdir()
            (destination / "app.py").write_text("pass")
            (destination / "docs").mkdir()
            (destination / "docs" / "notes.md").write_text("notes")

        with patch("worker.clone_repository", side_effect=fake_clone) as clone:
            output = run("inspect this", "https://example.com/repo.git")

        clone.assert_called_once()
        self.assertIn("Task: inspect this", output)
        self.assertIn("Repository: https://example.com/repo.git", output)
        self.assertIn("- app.py", output)
        self.assertIn("- docs/notes.md", output)

    def test_workflow_runs_codex_tests_and_publishes_diff(self) -> None:
        events: list[str] = []

        def fake_clone(_repo: str, destination: Path) -> None:
            events.append("clone")
            destination.mkdir()

        def fake_codex(_task: str, _repository: Path) -> CommandResult:
            events.append("codex")
            return CommandResult(["codex"], "", "")

        def fake_tests(_repository: Path) -> CommandResult:
            events.append("tests")
            return CommandResult(["pytest"], "passed", "")

        def fake_diff(_repository: Path) -> str:
            events.append("diff")
            return "diff --git a/app.py b/app.py"

        def fake_publish(_repository: Path, _task: str) -> tuple[str, str]:
            events.append("publish")
            return "main", "abc123"

        with patch("worker.clone_repository", side_effect=fake_clone), patch(
            "worker.invoke_codex", side_effect=fake_codex
        ), patch("worker.run_project_tests", side_effect=fake_tests), patch(
            "worker.git_diff", side_effect=fake_diff
        ), patch("worker.publish_changes", side_effect=fake_publish):
            output = run_workflow("add feature", "local-repo")

        self.assertEqual(events, ["clone", "codex", "tests", "diff", "publish"])
        self.assertIn("Branch: main", output)
        self.assertIn("Diff:\ndiff --git a/app.py b/app.py", output)

    def test_execute_workflow_reports_major_statuses_and_summary_fields(self) -> None:
        statuses: list[str] = []
        with patch("worker.clone_repository"), patch("worker.invoke_codex"), patch(
            "worker.run_project_tests", return_value=CommandResult(["pytest"], "passed", "")
        ), patch("worker.git_diff", return_value="diff"), patch(
            "worker.publish_changes", return_value=("main", "deadbeef")
        ):
            result = execute_workflow("task", "repo", statuses.append)

        self.assertEqual(
            statuses,
            ["cloning repository", "running agent", "running tests", "pushing branch", "finished"],
        )
        self.assertEqual(result.branch, "main")
        self.assertEqual(result.commit, "deadbeef")
        self.assertEqual(result.tests, "passed")


if __name__ == "__main__":
    unittest.main()
