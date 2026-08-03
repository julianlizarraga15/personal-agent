import unittest
from unittest.mock import AsyncMock, patch

from public_download_mcp import INSTRUCTIONS, TOOL_NAME, handle_request


class PublicDownloadMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_advertises_write_and_open_world_boundary(self):
        initialized = await handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
        )
        self.assertEqual(initialized["result"]["instructions"], INSTRUCTIONS)
        listing = await handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = listing["result"]["tools"][0]
        self.assertEqual(tool["name"], TOOL_NAME)
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertTrue(tool["annotations"]["openWorldHint"])

    async def test_tool_calls_gateway_with_exact_url_and_destination(self):
        arguments = {"url": "https://example.com/file.csv", "destination": "data/raw/file.csv"}
        with patch(
            "public_download_mcp.request_gateway",
            AsyncMock(return_value={"ok": True, "data": {"path": arguments["destination"], "bytes": 10}}),
        ) as gateway:
            response = await handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": TOOL_NAME, "arguments": arguments},
                }
            )
        gateway.assert_awaited_once_with(arguments)
        self.assertFalse(response["result"]["isError"])
        self.assertIn("data/raw/file.csv", response["result"]["content"][0]["text"])

    async def test_tool_rejects_extra_arguments_and_surfaces_stable_gateway_error(self):
        response = await handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": TOOL_NAME, "arguments": {"url": "https://example.com", "destination": "x", "headers": {}}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        with patch(
            "public_download_mcp.request_gateway",
            AsyncMock(return_value={"ok": False, "error": "Public download was rejected or approval expired."}),
        ):
            response = await handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": TOOL_NAME, "arguments": {"url": "https://example.com", "destination": "x"}},
                }
            )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("rejected", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
