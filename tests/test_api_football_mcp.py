import unittest
from unittest.mock import AsyncMock, patch

from api_football_mcp import INSTRUCTIONS, LOGO_TOOL_NAME, TOOL_NAME, handle_request


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
        tools = {tool["name"]: tool for tool in listing["result"]["tools"]}
        tool = tools[TOOL_NAME]
        self.assertEqual(tool["name"], TOOL_NAME)
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tools[LOGO_TOOL_NAME]["annotations"]["readOnlyHint"])
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

    async def test_logo_tool_uses_fixed_gateway_operation(self):
        with patch(
            "api_football_mcp.request_gateway",
            AsyncMock(return_value={"ok": True, "data": {"team_id": 435, "path": "assets/team-crests/435.png"}}),
        ) as gateway:
            response = await handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": LOGO_TOOL_NAME, "arguments": {"team_id": 435}},
                }
            )
        gateway.assert_awaited_once_with({"method": "DOWNLOAD_TEAM_LOGO", "team_id": 435})
        self.assertFalse(response["result"]["isError"])
        self.assertIn("assets/team-crests/435.png", response["result"]["content"][0]["text"])

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
