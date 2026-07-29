import copy
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent import Agent, AgentSession, Computer, ImageInput, ProjectContext, _is_non_fast_forward, _output_items, _requests_self_deploy, _self_deploy_retryable, tool_definitions
from router import RouteDecision, Router
from owner_trace import TraceStore
from usage import ModelUsage, SessionUsage


class ComputerToolTests(unittest.TestCase):
    def test_writes_do_not_require_approval_and_stay_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = ProjectContext("demo", root)
            computer = Computer(project)
            requested: list[tuple[str, str]] = []
            computer.call(
                "write_file",
                {"path": "notes.txt", "content": "hello"},
                lambda action, summary: requested.append((action, summary)) or False,
            )
            read_result = json.loads(computer.call("read_file", {"path": "notes.txt"}))
            self.assertEqual(read_result["content"], "hello")
            self.assertEqual(requested, [])
            with self.assertRaises(ValueError):
                computer.call("read_file", {"path": "../secret.txt"})

    def test_dangerous_commands_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = Computer(ProjectContext("demo", Path(directory))).call(
                "run_command", {"command": "git push origin main"}
            )
            self.assertIn("blocked", result)

    def test_env_files_cannot_be_read_written_or_printed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
            (root / ".env.example").write_text("OPENAI_API_KEY=placeholder", encoding="utf-8")
            computer = Computer(ProjectContext("demo", root))
            for name, arguments in (
                ("read_file", {"path": ".env"}),
                ("write_file", {"path": ".env.local", "content": "SAFE=no"}),
            ):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "protected"):
                    computer.call(name, arguments)
            self.assertIn("blocked", computer.call("run_command", {"command": "cat .env"}))
            self.assertIn("placeholder", json.loads(computer.call("read_file", {"path": ".env.example"}))["content"])
            self.assertNotIn("blocked", computer.call("run_command", {"command": "cat .env.example"}))

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
            {"list_files", "read_file", "edit_file", "write_file", "run_command", "git_status", "git_diff", "git_commit", "git_push"},
        )
        write_file = next(tool for tool in tool_definitions() if tool["name"] == "write_file")
        self.assertNotIn("approval", write_file["description"].lower())

    def test_self_deploy_tool_is_opt_in(self) -> None:
        self.assertNotIn("self_deploy", {tool["name"] for tool in tool_definitions()})
        self.assertIn("self_deploy", {tool["name"] for tool in tool_definitions(True)})

    def test_bounded_file_tools_and_exact_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            computer = Computer(ProjectContext("demo", root))

            result = json.loads(computer.call("read_file", {"path": "notes.txt", "start_line": 2, "end_line": 2}))
            self.assertEqual(result["content"], "two\n")
            self.assertTrue(result["truncated"])
            self.assertEqual(result["next_start_line"], 3)

            self.assertIn("edited notes.txt", computer.call("edit_file", {"path": "notes.txt", "old_text": "two", "new_text": "TWO"}))
            self.assertEqual((root / "notes.txt").read_text(encoding="utf-8"), "one\nTWO\nthree\n")
            ambiguous = computer.call("edit_file", {"path": "notes.txt", "old_text": "e", "new_text": "E"})
            self.assertIn("matched", ambiguous)
            self.assertIn("times", ambiguous)

    def test_list_and_command_outputs_report_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "top.txt").write_text("x", encoding="utf-8")
            nested = root / "one" / "two" / "three" / "four"
            nested.mkdir(parents=True)
            (nested / "deep.txt").write_text("x", encoding="utf-8")
            computer = Computer(ProjectContext("demo", root))

            listed = json.loads(computer.call("list_files", {"max_depth": 2}))
            self.assertEqual(listed["files"], ["top.txt"])
            command = json.loads(computer.call("run_command", {"command": "python3 -c 'print(\"x\" * 7000)'"}))
            self.assertTrue(command["stdout_truncated"])
            self.assertLessEqual(len(command["stdout"]), 6000)

    def test_trace_keeps_complete_command_output_while_model_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TraceStore(root / "traces.sqlite3")
            trace = store.start_turn("toolturn", 42, project="demo", kind="conversation")
            computer = Computer(ProjectContext("demo", root), trace)

            model_result = json.loads(computer.call("run_command", {"command": "python3 -c 'print(\"x\" * 7000)'"}))
            exported = store.export_turn(42, "toolturn")

        finished = next(event for event in exported["events"] if event["type"] == "tool.finished")
        self.assertTrue(model_result["stdout_truncated"])
        self.assertLessEqual(len(model_result["stdout"]), 6000)
        self.assertGreater(len(finished["data"]["complete_result"]["stdout"]), 7000)
        self.assertTrue(finished["data"]["model_result_bounded"])

    def test_self_deploy_request_detection_is_explicit(self) -> None:
        self.assertTrue(_requests_self_deploy("modify itself and deploy itself"))
        self.assertTrue(_requests_self_deploy("prepare a self-deployment"))
        self.assertTrue(_requests_self_deploy("change the prompt and redeploy"))
        self.assertFalse(_requests_self_deploy("update the README and run tests"))

    def test_self_deploy_can_retry_after_no_changes(self) -> None:
        self.assertTrue(_self_deploy_retryable("self-deployment found no uncommitted changes; continue editing before retrying deployment"))
        self.assertFalse(_self_deploy_retryable('{"stage":"push","exit_code":1}'))

    def test_non_fast_forward_pushes_are_retryable(self) -> None:
        self.assertTrue(_is_non_fast_forward("! [rejected] main -> main (fetch first)"))
        self.assertTrue(_is_non_fast_forward("non-fast-forward"))
        self.assertFalse(_is_non_fast_forward("Permission denied (publickey)."))

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

    def test_output_items_remove_response_only_metadata(self) -> None:
        class FakeHostedToolCall:
            def model_dump(self):
                return {
                    "type": "web_search_call",
                    "status": "completed",
                    "id": "call_1",
                }

        class FakeCompactionItem:
            def model_dump(self):
                return {
                    "type": "compaction",
                    "id": "cmp_1",
                    "encrypted_content": "opaque",
                    "created_by": "server",
                }

        output = _output_items(SimpleNamespace(output=[FakeHostedToolCall(), FakeCompactionItem()]))

        self.assertEqual(
            output,
            [
                {"type": "web_search_call", "id": "call_1"},
                {"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque"},
            ],
        )

    def test_agent_sanitizes_existing_replay_items_before_request(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.input = None

            def create(self, **kwargs):
                self.input = copy.deepcopy(kwargs["input"])
                return SimpleNamespace(output=[], output_text="ok")

        class EconomyRouter:
            def decide(self, message, context):
                return RouteDecision("economy", confidence=0.95)

        client = SimpleNamespace(responses=FakeResponses())
        session = AgentSession(
            input_items=[
                {
                    "type": "compaction",
                    "id": "cmp_1",
                    "encrypted_content": "opaque",
                    "created_by": "server",
                }
            ]
        )

        Agent(client=client, router=EconomyRouter()).respond(session, "continue")

        self.assertNotIn("created_by", client.responses.input[0])
        self.assertNotIn("created_by", session.input_items[0])


class ImageInputTests(unittest.TestCase):
    class EconomyRouter:
        def decide(self, message, context):
            return RouteDecision("economy", confidence=0.95)

    def test_agent_sends_multimodal_image_at_high_detail_then_scrubs_it(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.request = None

            def create(self, **kwargs):
                self.request = copy.deepcopy(kwargs)
                return SimpleNamespace(output=[], output_text="vision answer")

        client = SimpleNamespace(responses=FakeResponses())
        session = AgentSession()

        result = Agent(client=client, router=self.EconomyRouter()).respond(
            session,
            "Read the error",
            image=ImageInput(b"image-bytes", "image/jpeg"),
        )

        self.assertEqual(result, "vision answer")
        content = client.responses.request["input"][-1]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "Read the error"})
        self.assertEqual(
            content[1],
            {
                "type": "input_image",
                "image_url": "data:image/jpeg;base64,aW1hZ2UtYnl0ZXM=",
                "detail": "high",
            },
        )
        self.assertNotIn("context_management", client.responses.request)
        self.assertNotIn("input_image", json.dumps(session.input_items))
        self.assertIn("no longer available", json.dumps(session.input_items))

    def test_image_turn_cannot_return_a_text_only_small_router_answer(self) -> None:
        class SmallRouter:
            def __init__(self) -> None:
                self.message = None

            def decide(self, message, context):
                self.message = message
                return RouteDecision("small", "I cannot see it", 0.99)

        class FakeResponses:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return SimpleNamespace(output=[], output_text="I can see it")

        router = SmallRouter()
        client = SimpleNamespace(responses=FakeResponses())

        result = Agent(client=client, router=router).respond(
            AgentSession(),
            "What is this?",
            image=ImageInput(b"image", "image/png"),
        )

        self.assertEqual(result, "I can see it")
        self.assertEqual(client.responses.calls, 1)
        self.assertIn("image attached", router.message.lower())

    def test_image_remains_available_through_the_current_tool_loop(self) -> None:
        class FunctionCall:
            type = "function_call"
            name = "git_status"
            arguments = "{}"
            call_id = "call-1"

            def model_dump(self):
                return {"type": self.type, "name": self.name, "arguments": self.arguments, "call_id": self.call_id}

        class FakeResponses:
            def __init__(self) -> None:
                self.inputs = []

            def create(self, **kwargs):
                self.inputs.append(copy.deepcopy(kwargs["input"]))
                if len(self.inputs) == 1:
                    return SimpleNamespace(output=[FunctionCall()], output_text="")
                return SimpleNamespace(output=[], output_text="done")

        class MediumRouter:
            def decide(self, message, context):
                return RouteDecision("medium", confidence=0.95, capabilities=frozenset({"computer"}))

        client = SimpleNamespace(responses=FakeResponses())
        with tempfile.TemporaryDirectory() as directory:
            session = AgentSession(ProjectContext("demo", Path(directory)))
            result = Agent(client=client, router=MediumRouter()).respond(
                session,
                "Use this screenshot",
                image=ImageInput(b"image", "image/png"),
            )

        self.assertEqual(result, "done")
        self.assertEqual(len(client.responses.inputs), 2)
        self.assertTrue(all("input_image" in json.dumps(items) for items in client.responses.inputs))
        self.assertNotIn("input_image", json.dumps(session.input_items))

    def test_image_is_scrubbed_when_the_model_request_fails(self) -> None:
        class FailingResponses:
            def create(self, **kwargs):
                raise RuntimeError("provider unavailable")

        session = AgentSession()
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            Agent(client=SimpleNamespace(responses=FailingResponses()), router=self.EconomyRouter()).respond(
                session,
                "Analyze",
                image=ImageInput(b"image", "image/webp"),
            )

        self.assertNotIn("input_image", json.dumps(session.input_items))
        self.assertIn("no longer available", json.dumps(session.input_items))


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
        router = Router(self.FakeClient('{"route":"small","answer":"Hello!","confidence":0.98,"capabilities":[]}'), "small")
        decision = router.decide("hello", {"project_selected": False})
        self.assertEqual((decision.route, decision.answer, decision.confidence, decision.capabilities), ("small", "Hello!", 0.98, frozenset()))
        request = router.client.responses.calls[0]
        self.assertEqual(request["reasoning"], {"effort": "minimal"})
        self.assertEqual(request["max_output_tokens"], 512)
        capabilities_schema = request["text"]["format"]["schema"]["properties"]["capabilities"]
        self.assertNotIn("uniqueItems", capabilities_schema)

    def test_accepts_medium_route(self) -> None:
        router = Router(self.FakeClient('{"route":"medium","answer":"","confidence":0.95,"capabilities":["computer"]}'), "small")
        decision = router.decide("inspect this code", {})
        self.assertEqual(decision.route, "medium")
        self.assertEqual(decision.capabilities, frozenset({"computer"}))

    def test_promotes_computer_work_out_of_economy_tier(self) -> None:
        router = Router(self.FakeClient('{"route":"economy","answer":"","confidence":0.95,"capabilities":["computer"]}'), "small")
        decision = router.decide("inspect this file", {})
        self.assertEqual(decision.route, "medium")
        self.assertEqual(decision.capabilities, frozenset({"computer"}))

    def test_low_confidence_small_route_falls_back_to_large(self) -> None:
        router = Router(self.FakeClient('{"route":"small","answer":"Maybe","confidence":0.6,"capabilities":[]}'), "small")
        decision = router.decide("do that", {})
        self.assertEqual(decision.route, "large")
        self.assertEqual(decision.capabilities, frozenset({"web", "computer"}))

    def test_low_confidence_economy_route_falls_back_to_large(self) -> None:
        router = Router(self.FakeClient('{"route":"economy","answer":"","confidence":0.5,"capabilities":["web"]}'), "small")
        decision = router.decide("uncertain request", {})
        self.assertEqual(decision.route, "large")
        self.assertEqual(decision.capabilities, frozenset({"web", "computer"}))

    def test_invalid_router_output_falls_back_to_large(self) -> None:
        router = Router(self.FakeClient("not json"), "small")
        self.assertEqual(router.decide("anything", {}).route, "large")

    def test_small_route_never_grants_model_requested_capabilities(self) -> None:
        router = Router(
            self.FakeClient('{"route":"small","answer":"Done","confidence":0.99,"capabilities":["web","computer"]}'),
            "small",
        )

        decision = router.decide("hello", {})

        self.assertEqual(decision.route, "small")
        self.assertEqual(decision.capabilities, frozenset())

    def test_duplicate_router_capabilities_fall_back_to_large(self) -> None:
        router = Router(
            self.FakeClient('{"route":"economy","answer":"","confidence":0.95,"capabilities":["web","web"]}'),
            "small",
        )

        decision = router.decide("latest news", {})

        self.assertEqual(decision.route, "large")
        self.assertEqual(decision.capabilities, frozenset({"web", "computer"}))

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

    def test_production_small_route_creates_complete_router_trace(self) -> None:
        client = self.FakeClient('{"route":"small","answer":"Hello!","confidence":0.98,"capabilities":[]}')
        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(Path(directory) / "traces.sqlite3")
            trace = store.start_turn("smallturn", 42, project=None, kind="conversation")
            result = Agent(client=client, router=Router(client, "small")).respond(AgentSession(), "hello", trace=trace)
            exported = store.export_turn(42, "smallturn")

        self.assertEqual(result, "Hello!")
        event_types = [event["type"] for event in exported["events"]]
        self.assertIn("router.request", event_types)
        self.assertIn("router.response", event_types)
        self.assertIn("router.decision", event_types)
        self.assertIn("conversation.output", event_types)

    def test_production_router_failure_is_traced_before_main_fallback(self) -> None:
        class Responses:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(output_text="not json", output=[])
                return SimpleNamespace(output_text="fallback answer", output=[], usage=None)

        client = SimpleNamespace(responses=Responses())
        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(Path(directory) / "traces.sqlite3")
            trace = store.start_turn("fallback", 42, project=None, kind="conversation")
            result = Agent(client=client, router=Router(client, "small")).respond(AgentSession(), "ambiguous", trace=trace)
            exported = store.export_turn(42, "fallback")

        self.assertEqual(result, "fallback answer")
        self.assertIn("router.failed", [event["type"] for event in exported["events"]])
        self.assertIn("model.response", [event["type"] for event in exported["events"]])

    def test_router_context_excludes_latest_message_and_tracks_router_usage(self) -> None:
        class CapturingRouter:
            def __init__(self) -> None:
                self.context = None

            def decide(self, message, context):
                self.context = context
                return RouteDecision(
                    "small",
                    "answer",
                    0.99,
                    usage=ModelUsage("gpt-5-nano", "router", input_tokens=10, output_tokens=2),
                )

        router = CapturingRouter()
        recorded: list[ModelUsage] = []
        session = AgentSession(
            routing_history=[{"role": "user", "content": "previous"}],
            usage=SessionUsage(recorder=recorded.append),
        )
        Agent(client=object(), router=router).respond(session, "latest")

        self.assertEqual(router.context["recent_conversation"], [{"role": "user", "content": "previous"}])
        self.assertEqual(session.usage.billed_tokens(), 12)
        self.assertEqual([item.phase for item in recorded], ["router"])

    def test_agent_sends_medium_route_to_intermediate_model(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.models = []
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                self.models.append(kwargs["model"])
                return SimpleNamespace(output=[], output_text="medium answer")

        class FakeClient:
            def __init__(self) -> None:
                self.responses = FakeResponses()

        class MediumRouter:
            def decide(self, message, context):
                return RouteDecision("medium", confidence=0.95, capabilities=frozenset({"computer"}))

        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            result = Agent(client=client, intermediate_model="middle", router=MediumRouter()).respond(
                AgentSession(ProjectContext("demo", Path(directory))), "inspect the file"
            )
        self.assertEqual(result, "medium answer")
        self.assertEqual(client.responses.models, ["middle"])
        self.assertNotIn({"type": "web_search"}, client.responses.kwargs["tools"])

    def test_agent_sends_economy_route_to_luna_with_only_requested_tools(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(output=[], output_text="economy answer")

        class EconomyRouter:
            def decide(self, message, context):
                return RouteDecision("economy", confidence=0.95, capabilities=frozenset({"web"}))

        client = SimpleNamespace(responses=FakeResponses())
        result = Agent(client=client, economy_model="cheap", router=EconomyRouter()).respond(AgentSession(), "latest news")

        self.assertEqual(result, "economy answer")
        self.assertEqual(client.responses.kwargs["model"], "cheap")
        self.assertEqual(client.responses.kwargs["reasoning"], {"effort": "low", "summary": "auto"})
        self.assertEqual(client.responses.kwargs["max_output_tokens"], 4096)
        self.assertEqual(client.responses.kwargs["tools"], [{"type": "web_search"}])
        self.assertEqual(client.responses.kwargs["context_management"][0]["compact_threshold"], 32000)

    def test_agent_omits_tools_when_router_requests_no_capabilities(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(output=[], output_text="answer")

        class EconomyRouter:
            def decide(self, message, context):
                return RouteDecision("economy", confidence=0.95)

        client = SimpleNamespace(responses=FakeResponses())
        Agent(client=client, router=EconomyRouter()).respond(AgentSession(), "think about this")
        self.assertNotIn("tools", client.responses.kwargs)

    def test_reasoning_summary_is_retried_and_limitation_is_traced_when_unsupported(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise ValueError("reasoning summary is not supported by this model")
                return SimpleNamespace(output=[], output_text="answer", usage=None)

        class EconomyRouter:
            def decide(self, message, context):
                return RouteDecision("economy", confidence=0.95)

        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(Path(directory) / "traces.sqlite3")
            trace = store.start_turn("turn1", 42, project=None, kind="conversation")
            responses = FakeResponses()
            result = Agent(client=SimpleNamespace(responses=responses), router=EconomyRouter()).respond(
                AgentSession(), "think", trace=trace
            )
            exported = store.export_turn(42, "turn1")

        self.assertEqual(result, "answer")
        self.assertEqual(responses.calls[0]["reasoning"], {"effort": "low", "summary": "auto"})
        self.assertEqual(responses.calls[1]["reasoning"], {"effort": "low"})
        self.assertIn("reasoning_summary.unavailable", [event["type"] for event in exported["events"]])

    def test_agent_prunes_context_before_latest_compaction_item(self) -> None:
        class CompactionItem:
            type = "compaction"

            def model_dump(self):
                return {"type": "compaction", "encrypted_content": "opaque"}

        class FakeResponses:
            def create(self, **kwargs):
                return SimpleNamespace(output=[CompactionItem()], output_text="compacted")

        class EconomyRouter:
            def decide(self, message, context):
                return RouteDecision("economy", confidence=0.95)

        session = AgentSession(input_items=[{"role": "user", "content": "old"}])
        Agent(client=SimpleNamespace(responses=FakeResponses()), router=EconomyRouter()).respond(session, "new")
        self.assertEqual(session.input_items, [{"type": "compaction", "encrypted_content": "opaque"}])

    def test_agent_records_one_warning_for_a_high_usage_turn(self) -> None:
        class FakeResponses:
            def create(self, **kwargs):
                return SimpleNamespace(
                    model="gpt-5.6-luna",
                    output=[],
                    output_text="answer",
                    usage=SimpleNamespace(
                        input_tokens=60,
                        input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                        output_tokens=10,
                        output_tokens_details=SimpleNamespace(reasoning_tokens=2),
                    ),
                )

        class EconomyRouter:
            def decide(self, message, context):
                return RouteDecision("economy", confidence=0.95)

        recorded: list[ModelUsage] = []
        session = AgentSession(usage=SessionUsage(recorder=recorded.append))
        with patch.dict(os.environ, {"OPENAI_TURN_WARNING_TOKENS": "50"}):
            Agent(client=SimpleNamespace(responses=FakeResponses()), router=EconomyRouter()).respond(session, "think")

        self.assertEqual(session.usage.warning_turns, 1)
        self.assertEqual([item.phase for item in recorded], ["answer"])

    def test_agent_sends_large_route_to_existing_model(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.models = []

            def create(self, **kwargs):
                self.models.append(kwargs["model"])
                return SimpleNamespace(output=[], output_text="large answer")

        class FakeClient:
            def __init__(self) -> None:
                self.responses = FakeResponses()

        class LargeRouter:
            def decide(self, message, context):
                return RouteDecision("large", confidence=0.7)

        client = FakeClient()
        result = Agent(client=client, model="large", router=LargeRouter()).respond(AgentSession(), "edit the file")
        self.assertEqual(result, "large answer")
        self.assertEqual(client.responses.models, ["large"])
