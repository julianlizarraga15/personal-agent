import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from codex_backend import (
    CODEX_CAPABILITY_INSTRUCTION,
    CodexBackend,
    CodexBackendError,
    CodexBusyError,
    CodexTurnDiscarded,
    event_status,
    final_message_from_items,
    network_approval_request,
    telegram_images_from_message,
    translate_codex_error,
)


def event(method, *, item=None, turn=None):
    payload = SimpleNamespace()
    if item is not None:
        payload.item = SimpleNamespace(root=item)
    if turn is not None:
        payload.turn = turn
    return SimpleNamespace(method=method, payload=payload)


class FakeTurnHandle:
    def __init__(self, events):
        self.events = events
        self.interrupt = AsyncMock()

    async def stream(self):
        for value in self.events:
            await asyncio.sleep(0)
            yield value


class FakeThread:
    def __init__(self, turns):
        self.id = "thread-1"
        self.turns = list(turns)
        self.prompts = []

    async def turn(self, prompt):
        self.prompts.append(prompt)
        return self.turns.pop(0)


class FakeClient:
    def __init__(self, threads):
        self.threads = list(threads)
        self.start_calls = []
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *args):
        self.exited += 1

    async def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.threads.pop(0)


def successful_handle(text):
    item = SimpleNamespace(type="agentMessage", text=text, phase=SimpleNamespace(value="final_answer"))
    turn = SimpleNamespace(status=SimpleNamespace(value="completed"), error=None)
    return FakeTurnHandle([event("turn/started"), event("item/completed", item=item), event("turn/completed", turn=turn)])


class CodexBackendTests(unittest.IsolatedAsyncioTestCase):
    def test_publication_instruction_requires_a_fresh_tool_result(self):
        self.assertIn("must call the Git publish tool exactly once", CODEX_CAPABILITY_INSTRUCTION)
        self.assertIn("Do not infer or repeat an approval result", CODEX_CAPABILITY_INSTRUCTION)
        self.assertIn("unless the current tool call returned", CODEX_CAPABILITY_INSTRUCTION)

    async def test_one_client_starts_and_closes_once(self):
        client = FakeClient([])
        backend = CodexBackend(client)

        await backend.start()
        await backend.start()
        await backend.close()

        self.assertEqual((client.entered, client.exited), (1, 1))

    async def test_thread_uses_ephemeral_managed_permissions(self):
        thread = FakeThread([])
        client = FakeClient([thread])
        backend = CodexBackend(client)
        await backend.new_session(42, Path("/tmp"))

        options = client.start_calls[0]
        self.assertTrue(options["ephemeral"])
        self.assertIsNone(options["sandbox"])
        self.assertEqual(options["approval_mode"], "telegram_user")
        self.assertEqual(options["cwd"], "/tmp")
        self.assertNotIn("model", options)
        self.assertNotIn("personality", options)
        self.assertNotIn("developer_instructions", options)

    async def test_continued_turns_reuse_one_ephemeral_thread(self):
        thread = FakeThread([successful_handle("first"), successful_handle("second")])
        client = FakeClient([thread])
        backend = CodexBackend(client)

        first = await backend.run_turn(42, "one", default_cwd=Path("/tmp"))
        second = await backend.run_turn(42, "two", default_cwd=Path("/tmp"))

        self.assertEqual((first.text, second.text), ("first", "second"))
        self.assertEqual((first.cwd, second.cwd), (Path("/tmp"), Path("/tmp")))
        self.assertEqual(thread.prompts, ["one", "two"])
        self.assertEqual(len(client.start_calls), 1)

    async def test_new_session_interrupts_and_replaces_active_thread(self):
        old_handle = successful_handle("unused")
        old_thread = FakeThread([])
        new_thread = FakeThread([])
        client = FakeClient([old_thread, new_thread])
        backend = CodexBackend(client)
        old = await backend.new_session(42, Path("/tmp"))
        old.active_turn = old_handle

        fresh = await backend.new_session(42, Path("/tmp/project"))

        old_handle.interrupt.assert_awaited_once()
        self.assertIs(backend.sessions[42], fresh)
        self.assertEqual(fresh.cwd, Path("/tmp/project"))

    async def test_stop_interrupts_and_discards_session(self):
        handle = successful_handle("unused")
        thread = FakeThread([])
        backend = CodexBackend(FakeClient([thread]))
        session = await backend.new_session(42, Path("/tmp"))
        session.active_turn = handle

        self.assertTrue(await backend.stop_session(42))

        handle.interrupt.assert_awaited_once()
        self.assertNotIn(42, backend.sessions)

    async def test_network_approval_bridge_returns_owner_decision(self):
        backend = CodexBackend(FakeClient([FakeThread([])]))
        session = await backend.new_session(42, Path("/tmp"))
        session.approval_callback = AsyncMock(return_value=True)
        session.event_loop = asyncio.get_running_loop()
        result = []
        thread = threading.Thread(
            target=lambda: result.append(
                backend._handle_approval(
                    "item/commandExecution/requestApproval",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "networkApprovalContext": {"host": "pypi.org", "protocol": "https"},
                    },
                )
            )
        )

        thread.start()
        while thread.is_alive():
            await asyncio.sleep(0.01)
        thread.join()

        self.assertEqual(result, [{"decision": "accept"}])
        session.approval_callback.assert_awaited_once()
        self.assertEqual(
            backend._handle_approval(
                "item/fileChange/requestApproval",
                {"threadId": "thread-1", "turnId": "turn-1"},
            ),
            {"decision": "decline"},
        )

    async def test_second_turn_is_rejected_while_first_is_starting(self):
        thread = FakeThread([])
        backend = CodexBackend(FakeClient([thread]))
        session = await backend.new_session(42, Path("/tmp"))
        await session.turn_lock.acquire()
        try:
            with self.assertRaisesRegex(CodexBusyError, "still working"):
                await backend.run_turn(42, "second", default_cwd=Path("/tmp"))
        finally:
            session.turn_lock.release()

    async def test_reservation_covers_preparation_and_is_invalidated_by_reset(self):
        old_thread = FakeThread([])
        new_thread = FakeThread([])
        backend = CodexBackend(FakeClient([old_thread, new_thread]))
        reservation = await backend.reserve_turn(42, Path("/tmp"))

        with self.assertRaisesRegex(CodexBusyError, "still working"):
            await backend.reserve_turn(42, Path("/tmp"))

        await backend.new_session(42, Path("/tmp/project"))
        with self.assertRaises(CodexTurnDiscarded):
            await backend.run_turn(
                42,
                "late transcript",
                default_cwd=Path("/tmp"),
                reservation=reservation,
            )
        self.assertEqual(old_thread.prompts, [])
        backend.release_turn(reservation)

    async def test_empty_final_response_has_specific_error(self):
        completed = event(
            "turn/completed",
            turn=SimpleNamespace(status=SimpleNamespace(value="completed"), error=None),
        )
        backend = CodexBackend(FakeClient([FakeThread([FakeTurnHandle([completed])])]))

        with self.assertRaisesRegex(CodexBackendError, "without returning a response"):
            await backend.run_turn(42, "hello", default_cwd=Path("/tmp"))

    def test_event_status_and_final_message_mapping(self):
        command = SimpleNamespace(type="commandExecution")
        change = SimpleNamespace(type="fileChange")
        self.assertEqual(event_status(event("turn/started")), "Thinking…")
        self.assertEqual(event_status(event("item/started", item=command)), "Running a command…")
        self.assertEqual(event_status(event("item/completed", item=change)), "Files changed…")

        draft = SimpleNamespace(type="agentMessage", text="draft", phase=None)
        final = SimpleNamespace(type="agentMessage", text="done", phase=SimpleNamespace(value="final_answer"))
        self.assertEqual(final_message_from_items([draft, final]), "done")

    def test_telegram_image_markers_are_removed_and_bounded(self):
        text, paths = telegram_images_from_message(
            "Done.\n[[telegram_image:plots/a.png]]\n[[telegram_image:/tmp/b.jpg]]"
        )

        self.assertEqual(text, "Done.")
        self.assertEqual(paths, ("plots/a.png", "/tmp/b.jpg"))

        only_marker, _ = telegram_images_from_message("[[telegram_image:plot.png]]")
        self.assertEqual(only_marker, "Here’s the image.")

    def test_error_translation_covers_operational_failures(self):
        cases = {
            "not authenticated": "isn’t signed in",
            "usage limit exceeded": "subscription limit",
            "sandbox permission denied": "sandbox denied",
            "websocket connection closed": "local connection",
        }
        for detail, expected in cases.items():
            with self.subTest(detail=detail):
                self.assertIn(expected, translate_codex_error(RuntimeError(detail)).user_message)

    def test_only_public_https_network_approval_is_exposed(self):
        base = {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "networkApprovalContext": {"host": "pypi.org", "protocol": "https", "port": 443},
        }
        request = network_approval_request("item/commandExecution/requestApproval", base)
        self.assertIsNotNone(request)
        self.assertEqual(request.destination, "https://pypi.org:443")
        self.assertEqual(request.response(True), {"decision": "accept"})

        for host, protocol, port in (
            ("localhost", "https", 443),
            ("127.0.0.1", "https", 443),
            ("metadata.internal", "https", 443),
            ("pypi.org", "http", 80),
            ("pypi.org", "https", 8443),
        ):
            params = dict(base)
            params["networkApprovalContext"] = {"host": host, "protocol": protocol, "port": port}
            with self.subTest(host=host, protocol=protocol, port=port):
                self.assertIsNone(network_approval_request("item/commandExecution/requestApproval", params))

        self.assertIsNone(network_approval_request("item/fileChange/requestApproval", base))
        self.assertIsNone(network_approval_request("item/permissions/requestApproval", base))


if __name__ == "__main__":
    unittest.main()
