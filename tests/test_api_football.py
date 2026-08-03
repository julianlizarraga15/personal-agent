import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch

from api_football import (
    API_HOST,
    ALLOWED_ENDPOINTS,
    MEDIA_HOST,
    ApiFootballError,
    ApiFootballGateway,
    DailyQuota,
    MAX_RESPONSE_BYTES,
    _store_logo,
    _upstream_get,
    _upstream_logo,
    redact_secret,
    validate_logo_request,
    validate_request,
)
from api_football_cli import request_gateway


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, declared: str | None = None):
        self.body = body
        self.status = status
        self.declared = declared

    def getheader(self, name):
        return self.declared if name == "Content-Length" else None

    def read(self, amount):
        return self.body[:amount]


class FakeConnection:
    response = FakeResponse(b'{"response":[]}')
    error = None
    instance = None

    def __init__(self, host, port, *, timeout, context):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.request_call = None
        self.closed = False
        type(self).instance = self

    def request(self, method, path, headers):
        self.request_call = (method, path, headers)
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ValidationTests(unittest.TestCase):
    def test_allowed_analytics_endpoints_exclude_betting_and_predictions(self):
        self.assertIn("fixtures/statistics", ALLOWED_ENDPOINTS)
        self.assertIn("players/topscorers", ALLOWED_ENDPOINTS)
        self.assertIn("coachs", ALLOWED_ENDPOINTS)
        for endpoint in ("odds", "bookmakers", "predictions"):
            self.assertNotIn(endpoint, ALLOWED_ENDPOINTS)

    def test_valid_request_is_normalized(self):
        endpoint, params = validate_request(
            {"method": "GET", "endpoint": "standings", "params": {"league": 128, "season": "2026"}}
        )
        self.assertEqual(endpoint, "standings")
        self.assertEqual(params, {"league": "128", "season": "2026"})

    def test_rejects_full_urls_traversal_methods_and_unknown_endpoints(self):
        invalid = [
            {"method": "GET", "endpoint": "https://example.com", "params": {}},
            {"method": "GET", "endpoint": "../status", "params": {}},
            {"method": "POST", "endpoint": "status", "params": {}},
            {"method": "GET", "endpoint": "odds", "params": {}},
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ApiFootballError):
                validate_request(request)

    def test_rejects_unknown_malformed_and_excessive_parameters(self):
        invalid = [
            {"method": "GET", "endpoint": "status", "params": {"key": "value"}},
            {"method": "GET", "endpoint": "teams", "params": {"id": "not-an-id"}},
            {"method": "GET", "endpoint": "countries", "params": {"search": "https://bad.test"}},
            {"method": "GET", "endpoint": "countries", "params": {f"x{i}": "a" for i in range(21)}},
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ApiFootballError):
                validate_request(request)

    def test_redacts_secret_recursively(self):
        value = {"value": "before-secret-after", "nested": ["secret", {"secret-key": "safe"}]}
        redacted = redact_secret(value, "secret")
        self.assertNotIn("secret", json.dumps(redacted))

    def test_logo_request_accepts_only_one_positive_team_id(self):
        self.assertEqual(validate_logo_request({"method": "DOWNLOAD_TEAM_LOGO", "team_id": 435}), 435)
        for request in (
            {"method": "DOWNLOAD_TEAM_LOGO", "team_id": 0},
            {"method": "DOWNLOAD_TEAM_LOGO", "team_id": True},
            {"method": "DOWNLOAD_TEAM_LOGO", "team_id": "../435"},
            {"method": "DOWNLOAD_TEAM_LOGO", "team_id": 435, "path": "elsewhere"},
        ):
            with self.subTest(request=request), self.assertRaises(ApiFootballError):
                validate_logo_request(request)


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        FakeConnection.response = FakeResponse(b'{"response":[]}')
        FakeConnection.error = None
        FakeConnection.instance = None

    def test_fixed_tls_host_auth_header_and_proxy_independence(self):
        with patch("api_football.http.client.HTTPSConnection", FakeConnection), patch.dict(
            os.environ, {"HTTPS_PROXY": "http://attacker.invalid:8888"}
        ):
            result = _upstream_get("status", {}, "top-secret")
        connection = FakeConnection.instance
        self.assertEqual(result, {"response": []})
        self.assertEqual((connection.host, connection.port), (API_HOST, 443))
        self.assertIsInstance(connection.context, ssl.SSLContext)
        self.assertEqual(connection.request_call, ("GET", "/status", {"x-apisports-key": "top-secret", "Accept": "application/json"}))
        self.assertTrue(connection.closed)

    def test_response_secret_is_redacted(self):
        FakeConnection.response = FakeResponse(b'{"echo":"top-secret"}')
        with patch("api_football.http.client.HTTPSConnection", FakeConnection):
            result = _upstream_get("status", {}, "top-secret")
        self.assertEqual(result, {"echo": "[REDACTED]"})

    def test_declared_and_actual_response_limits(self):
        for response in (
            FakeResponse(b"{}", declared=str(MAX_RESPONSE_BYTES + 1)),
            FakeResponse(b"{" + b" " * MAX_RESPONSE_BYTES + b"}"),
        ):
            FakeConnection.response = response
            with self.subTest(declared=response.declared), patch(
                "api_football.http.client.HTTPSConnection", FakeConnection
            ), self.assertRaisesRegex(ApiFootballError, "size limit"):
                _upstream_get("status", {}, "secret")

    def test_timeout_and_upstream_http_errors_are_sanitized(self):
        FakeConnection.error = socket.timeout("secret details")
        with patch("api_football.http.client.HTTPSConnection", FakeConnection), self.assertRaisesRegex(
            ApiFootballError, "timed out"
        ) as timeout_error:
            _upstream_get("status", {}, "secret")
        self.assertNotIn("details", str(timeout_error.exception))
        FakeConnection.error = None
        FakeConnection.response = FakeResponse(b'{"errors":{"token":"secret"}}', status=500)
        with patch("api_football.http.client.HTTPSConnection", FakeConnection), self.assertRaisesRegex(
            ApiFootballError, "HTTP 500"
        ) as http_error:
            _upstream_get("status", {}, "secret")
        self.assertNotIn("secret", str(http_error.exception))

    def test_logo_uses_fixed_media_host_path_and_requires_png(self):
        png = b"\x89PNG\r\n\x1a\nlogo"
        FakeConnection.response = FakeResponse(png)
        with patch("api_football.http.client.HTTPSConnection", FakeConnection):
            self.assertEqual(_upstream_logo(435), png)
        connection = FakeConnection.instance
        self.assertEqual((connection.host, connection.port), (MEDIA_HOST, 443))
        self.assertEqual(connection.request_call[0:2], ("GET", "/football/teams/435.png"))
        self.assertNotIn("x-apisports-key", connection.request_call[2])

        FakeConnection.response = FakeResponse(b"not a png")
        with patch("api_football.http.client.HTTPSConnection", FakeConnection), self.assertRaisesRegex(
            ApiFootballError, "invalid logo"
        ):
            _upstream_logo(435)


class LogoStorageTests(unittest.TestCase):
    def test_logo_is_atomically_stored_at_fixed_project_path(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            body = b"\x89PNG\r\n\x1a\nlogo"
            self.assertEqual(_store_logo(project, 435, body), "assets/team-crests/435.png")
            self.assertEqual((project / "assets/team-crests/435.png").read_bytes(), body)

    def test_logo_storage_rejects_symlinked_asset_directory(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            project = Path(directory)
            (project / "assets").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ApiFootballError, "unsafe"):
                _store_logo(project, 435, b"\x89PNG\r\n\x1a\nlogo")
            self.assertEqual(list(Path(outside).iterdir()), [])


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_persists_resets_by_utc_day_and_stops_at_100(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            quota = DailyQuota(path)
            results = await asyncio.gather(*(quota.consume(day="2026-07-31") for _ in range(100)))
            self.assertEqual(sorted(results), list(range(1, 101)))
            with self.assertRaisesRegex(ApiFootballError, "daily request limit"):
                await quota.consume(day="2026-07-31")
            self.assertEqual(await quota.consume(day="2026-08-01"), 1)
            self.assertEqual(json.loads(path.read_text()), {"date": "2026-08-01", "count": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_invalid_local_request_does_not_consume_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            quota = DailyQuota(path)
            with self.assertRaises(ApiFootballError):
                validate_request({"method": "GET", "endpoint": "odds", "params": {}})
            self.assertFalse(path.exists())


class GatewayProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def _start_or_skip(self, gateway):
        try:
            await gateway.start()
        except PermissionError as exc:
            if exc.errno == 1:
                self.skipTest("test sandbox does not permit binding Unix sockets")
            raise

    async def test_missing_key_is_healthy_and_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "gateway.sock"
            gateway = ApiFootballGateway(None, socket_path=socket_path)
            await self._start_or_skip(gateway)
            try:
                self.assertTrue(socket_path.exists())
                response = await request_gateway({"method": "GET", "endpoint": "status", "params": {}}, socket_path)
            finally:
                await gateway.close()
            self.assertEqual(response, {"ok": False, "error": "API-Football is not configured."})
            self.assertFalse(socket_path.exists())

    async def test_socket_protocol_success_and_key_never_returns(self):
        calls = []

        def upstream(endpoint, params, key):
            calls.append((endpoint, params, key))
            return {"response": [{"echo": key}]}

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            socket_path = directory_path / "gateway.sock"
            gateway = ApiFootballGateway(
                "top-secret",
                socket_path=socket_path,
                quota=DailyQuota(directory_path / "quota.json"),
                upstream=upstream,
            )
            await self._start_or_skip(gateway)
            try:
                response = await request_gateway(
                    {"method": "GET", "endpoint": "standings", "params": {"league": "128", "season": "2026"}},
                    socket_path,
                )
            finally:
                await gateway.close()
            self.assertEqual(calls, [("standings", {"league": "128", "season": "2026"}, "top-secret")])
            self.assertNotIn("top-secret", json.dumps(response))
            self.assertEqual(response["data"]["response"][0]["echo"], "[REDACTED]")

    async def test_existing_non_socket_path_fails_securely(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "gateway.sock"
            socket_path.write_text("do not replace", encoding="utf-8")
            gateway = ApiFootballGateway("secret", socket_path=socket_path)
            with self.assertRaisesRegex(RuntimeError, "not a socket"):
                await gateway.start()
            self.assertEqual(socket_path.read_text(), "do not replace")

    async def test_logo_download_requires_active_project_and_saves_fixed_png(self):
        png = b"\x89PNG\r\n\x1a\nlogo"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            gateway = ApiFootballGateway(
                None,
                socket_path=root / "gateway.sock",
                quota=DailyQuota(root / "quota.json"),
                logo_upstream=lambda team_id: png if team_id == 435 else b"",
                workspace=root,
            )
            await self._start_or_skip(gateway)
            try:
                unavailable = await request_gateway(
                    {"method": "DOWNLOAD_TEAM_LOGO", "team_id": 435}, gateway.socket_path
                )
                lease = gateway.bind_project(project)
                response = await request_gateway(
                    {"method": "DOWNLOAD_TEAM_LOGO", "team_id": 435}, gateway.socket_path
                )
                gateway.unbind_project(lease)
            finally:
                await gateway.close()
            self.assertFalse(unavailable["ok"])
            self.assertIn("active owner turn", unavailable["error"])
            self.assertEqual(response["data"], {"team_id": 435, "path": "assets/team-crests/435.png"})
            self.assertEqual((project / "assets/team-crests/435.png").read_bytes(), png)


if __name__ == "__main__":
    unittest.main()
