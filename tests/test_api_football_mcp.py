import unittest
from unittest.mock import AsyncMock, patch

from api_football_mcp import INSTRUCTIONS, TOOL_NAME, handle_request


class ApiFootballMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_advertises_safe_server_instructions(self):
        response = await handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["instructions"], INSTRUCTIONS)
        self.assertIn("Never attempt", INSTRUCTIONS)

    async def test_tool_is_read_only_and_uses_gateway_protocol(self):
        listing = await handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool = listing["result"]["tools"][0]
        self.assertEqual(tool["name"], TOOL_NAME)
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        with patch(
            "api_football_mcp.request_gateway",
            AsyncMock(return_value={"ok": True, "data": {"response": [{"name": "Liga Profesional"}]}}),
        ) as gateway:
            response = await handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get", "arguments": {"endpoint": "standings", "params": {"league": 128}}},
                }
            )
        gateway.assert_awaited_once_with(
            {"method": "GET", "endpoint": "standings", "params": {"league": 128}}
        )
        self.assertFalse(response["result"]["isError"])
        self.assertIn("Liga Profesional", response["result"]["content"][0]["text"])

    async def test_gateway_errors_are_tool_errors_without_exception_details(self):
        with patch(
            "api_football_mcp.request_gateway",
            AsyncMock(return_value={"ok": False, "error": "API-Football is not configured."}),
        ):
            response = await handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get", "arguments": {"endpoint": "status"}},
                }
            )
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"][0]["text"], "API-Football is not configured.")


if __name__ == "__main__":
    unittest.main()
