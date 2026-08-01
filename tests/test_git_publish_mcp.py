import unittest
from unittest.mock import AsyncMock, patch

from git_publish_mcp import INSTRUCTIONS, TOOL_NAME, handle_request


class GitPublishMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_is_write_capable_and_requires_exact_commit(self):
        listing = await handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool = listing["result"]["tools"][0]
        self.assertEqual(tool["name"], TOOL_NAME)
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertIn("Telegram owner", INSTRUCTIONS)

    async def test_tool_uses_gateway_and_reports_publication(self):
        commit = "a" * 40
        with patch(
            "git_publish_mcp.request_gateway",
            AsyncMock(return_value={
                "ok": True,
                "data": {
                    "commit": commit,
                    "remote": "git@github.com:julianlizarraga15/mental-models.git",
                    "branch": "main",
                },
            }),
        ) as gateway:
            response = await handle_request({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "publish", "arguments": {"commit": commit}},
            })
        gateway.assert_awaited_once_with({"commit": commit})
        self.assertFalse(response["result"]["isError"])
        self.assertIn(commit, response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
