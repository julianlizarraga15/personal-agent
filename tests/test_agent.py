import tempfile
import unittest
from pathlib import Path

from agent import Computer, ProjectContext, tool_definitions


class ComputerToolTests(unittest.TestCase):
    def test_writes_require_approval_and_stay_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = ProjectContext("demo", root)
            computer = Computer(project)
            denied = computer.call(
                "write_file", {"path": "notes.txt", "content": "hello"}, lambda action, summary: False
            )
            self.assertIn("not changed", denied)
            self.assertFalse((root / "notes.txt").exists())
            computer.call("write_file", {"path": "notes.txt", "content": "hello"}, lambda action, summary: True)
            self.assertEqual(computer.call("read_file", {"path": "notes.txt"}), "hello")
            with self.assertRaises(ValueError):
                computer.call("read_file", {"path": "../secret.txt"})

    def test_dangerous_commands_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = Computer(ProjectContext("demo", Path(directory))).call(
                "run_command", {"command": "git push origin main"}
            )
            self.assertIn("blocked", result)

    def test_git_actions_require_approval_and_push_branch_is_restricted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            computer = Computer(ProjectContext("demo", Path(directory)))
            requested: list[tuple[str, str]] = []
            result = computer.call(
                "git_commit",
                {"message": "save changes"},
                lambda action, summary: requested.append((action, summary)) or False,
            )
            self.assertIn("not created", result)
            self.assertEqual(requested[0][0], "git_commit")
            self.assertIn("save changes", requested[0][1])
            self.assertIn("allowed only for codex/*", computer.call("git_push", {"branch": "main"}))

    def test_tool_schemas_are_function_tools(self) -> None:
        names = {tool["name"] for tool in tool_definitions()}
        self.assertEqual(
            names,
            {"list_files", "read_file", "write_file", "run_command", "git_status", "git_diff", "git_commit", "git_push"},
        )
