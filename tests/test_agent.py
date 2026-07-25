import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from agent import Agent, AgentSession, Computer, ProjectContext, _output_items, _requests_self_deploy, tool_definitions
from router import RouteDecision, Router


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

    def test_git_actions_require_approval_and_push_current_branch(self) -> None:
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
            result = computer.call(
                "git_push",
                {"branch": "main"},
                lambda action, summary: requested.append((action, summary)) or False,
            )
            self.assertIn("not pushed", result)
            self.assertEqual(requested[-1][0], "git_push")

    def test_tool_schemas_are_function_tools(self) -> None:
        names = {tool["name"] for tool in tool_definitions()}
        self.assertEqual(
            names,
            {"list_files", "read_file", "write_file", "run_command", "git_status", "git_diff", "git_commit", "git_push"},
        )

    def test_self_deploy_tool_is_opt_in(self) -> None:
        self.assertNotIn("self_deploy", {tool["name"] for tool in tool_definitions()})
        self.assertIn("self_deploy", {tool["name"] for tool in tool_definitions(True)})

    def test_self_deploy_request_detection_is_explicit(self) -> None:
        self.assertTrue(_requests_self_deploy("modify itself and deploy itself"))
        self.assertTrue(_requests_self_deploy("prepare a self-deployment"))
        self.assertFalse(_requests_self_deploy("update the README and run tests"))

    def test_self_deploy_callback_is_available_only_as_a_tool_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []
            computer = Computer(ProjectContext("self", Path(directory)))
            result = computer.call("self_deploy", {}, deploy_callback=lambda: calls.append("deployed") or "ok")
            self.assertEqual(result, "ok")
            self.assertEqual(calls, ["deployed"])

    def test_web_search_is_available_as_a_hosted_tool(self) -> None:
        from agent import WEB_SEARCH_TOOL

        self.assertEqual(WEB_SEARCH_TOOL, {"type": "web_search"})

    def test_agent_passes_web_search_to_responses_api(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(output=[], output_text="ok")

        class FakeClient:
            def __init__(self) -> None:
                self.responses = FakeResponses()

        client = FakeClient()
        result = Agent(client=client).respond(AgentSession(), "qué tiempo hace hoy en CABA?")

        self.assertEqual(result, "ok")
        self.assertIn({"type": "web_search"}, client.responses.kwargs["tools"])

    def test_output_items_remove_response_only_status_metadata(self) -> None:
        class FakeOutputItem:
            def model_dump(self):
                return {"type": "web_search_call", "status": "completed", "id": "call_1"}

        output = _output_items(SimpleNamespace(output=[FakeOutputItem()]))

        self.assertEqual(output, [{"type": "web_search_call", "id": "call_1"}])


class RouterTests(unittest.TestCase):
    class FakeResponses:
        def __init__(self, output_text: str) -> None:
            self.output_text = output_text
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(output_text=self.output_text)

    class FakeClient:
        def __init__(self, output_text: str) -> None:
            self.responses = RouterTests.FakeResponses(output_text)

    def test_accepts_confident_small_answer(self) -> None:
        router = Router(self.FakeClient('{"route":"small","answer":"Hello!","confidence":0.98}'), "small")
        decision = router.decide("hello", {"project_selected": False})
        self.assertEqual(decision, RouteDecision("small", "Hello!", 0.98))

    def test_low_confidence_small_route_falls_back_to_large(self) -> None:
        router = Router(self.FakeClient('{"route":"small","answer":"Maybe","confidence":0.6}'), "small")
        self.assertEqual(router.decide("do that", {}).route, "large")

    def test_invalid_router_output_falls_back_to_large(self) -> None:
        router = Router(self.FakeClient("not json"), "small")
        self.assertEqual(router.decide("anything", {}).route, "large")

    def test_agent_returns_small_answer_without_calling_large_model(self) -> None:
        class UnusedClient:
            class Responses:
                def create(self, **kwargs):
                    raise AssertionError("large model should not be called")

            responses = Responses()

        class SmallRouter:
            def decide(self, message, context):
                return RouteDecision("small", "A cheap answer", 0.99)

        result = Agent(client=UnusedClient(), router=SmallRouter()).respond(AgentSession(), "hello")
        self.assertEqual(result, "A cheap answer")

    def test_agent_sends_large_route_to_existing_model(self) -> None:
        class FakeResponses:
            def create(self, **kwargs):
                return SimpleNamespace(output=[], output_text="large answer")

        class FakeClient:
            responses = FakeResponses()

        class LargeRouter:
            def decide(self, message, context):
                return RouteDecision("large", confidence=0.7)

        result = Agent(client=FakeClient(), router=LargeRouter()).respond(AgentSession(), "edit the file")
        self.assertEqual(result, "large answer")
