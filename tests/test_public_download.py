import asyncio
import ipaddress
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from public_download import (
    DEFAULT_MAX_BYTES,
    DownloadRequest,
    PublicDownloadError,
    PublicDownloadGateway,
    _download,
    _resolve_public_address,
    _store,
    request_gateway,
    validate_destination,
    validate_request,
    validate_url,
)


class FakeResponse:
    def __init__(self, body=b"payload", *, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.offset = 0

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, amount):
        chunk = self.body[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk


class FakeConnection:
    responses = []
    instances = []

    def __init__(self, host, address, *, timeout):
        self.host = host
        self.address = address
        self.timeout = timeout
        self.sock = None
        self.request_call = None
        self.closed = False
        type(self).instances.append(self)

    def request(self, method, target, headers):
        self.request_call = (method, target, headers)

    def getresponse(self):
        return type(self).responses.pop(0)

    def close(self):
        self.closed = True


class ValidationTests(unittest.TestCase):
    def test_accepts_only_credential_free_public_https_url(self):
        self.assertEqual(
            validate_url("HTTPS://Files.Example.com/report.csv?year=2026"),
            "https://files.example.com/report.csv?year=2026",
        )
        invalid = (
            "http://example.com/file",
            "https://user:pass@example.com/file",
            "https://example.com:8443/file",
            "https://127.0.0.1/file",
            "https://localhost/file",
            "https://example.com/file?access_token=secret",
            "https://example.com/file#section",
            "https://example.com\\file",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(PublicDownloadError):
                validate_url(value)

    def test_destination_is_relative_and_credential_safe(self):
        self.assertEqual(validate_destination("data/raw/report.csv"), "data/raw/report.csv")
        invalid = (
            "/tmp/file",
            "../file",
            "data/../file",
            ".git/config",
            ".env",
            "config/.env.production",
            "secrets.json",
            "keys/id_rsa",
            "folder/",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(PublicDownloadError):
                validate_destination(value)

    def test_protocol_requires_exact_keys(self):
        request = validate_request({"url": "https://example.com/file", "destination": "data/file"})
        self.assertEqual(request, DownloadRequest("https://example.com/file", "data/file"))
        with self.assertRaises(PublicDownloadError):
            validate_request({"url": "https://example.com/file", "destination": "data/file", "headers": {}})


class NetworkTests(unittest.TestCase):
    def setUp(self):
        FakeConnection.responses = []
        FakeConnection.instances = []

    def test_dns_rejects_if_any_answer_is_not_public(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]
        with patch("public_download.socket.getaddrinfo", return_value=answers), self.assertRaisesRegex(
            PublicDownloadError, "non-public"
        ):
            _resolve_public_address("example.com")

        multicast = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("224.0.0.1", 443))
        ]
        with patch("public_download.socket.getaddrinfo", return_value=multicast), self.assertRaisesRegex(
            PublicDownloadError, "non-public"
        ):
            _resolve_public_address("example.com")

    def test_download_pins_validated_address_and_bounds_body(self):
        FakeConnection.responses = [
            FakeResponse(b"hello", headers={"Content-Length": "5", "Content-Type": "text/plain; charset=utf-8"})
        ]
        with patch("public_download._resolve_public_address", return_value="93.184.216.34"), patch(
            "public_download._PinnedHTTPSConnection", FakeConnection
        ):
            body, final_url, content_type = _download("https://example.com/files/a.txt?x=1", 10)
        self.assertEqual(body, b"hello")
        self.assertEqual(final_url, "https://example.com/files/a.txt?x=1")
        self.assertEqual(content_type, "text/plain")
        connection = FakeConnection.instances[0]
        self.assertEqual((connection.host, connection.address), ("example.com", "93.184.216.34"))
        self.assertEqual(connection.request_call[0:2], ("GET", "/files/a.txt?x=1"))
        self.assertTrue(connection.closed)

    def test_cross_host_redirect_is_rejected(self):
        FakeConnection.responses = [FakeResponse(status=302, headers={"Location": "https://cdn.example.net/file"})]
        with patch("public_download._resolve_public_address", return_value="93.184.216.34"), patch(
            "public_download._PinnedHTTPSConnection", FakeConnection
        ), self.assertRaisesRegex(PublicDownloadError, "another host"):
            _download("https://example.com/file", 10)

    def test_declared_and_actual_size_limits_are_enforced(self):
        for response in (
            FakeResponse(b"small", headers={"Content-Length": "11"}),
            FakeResponse(b"01234567890"),
        ):
            FakeConnection.responses = [response]
            with self.subTest(headers=response.headers), patch(
                "public_download._resolve_public_address", return_value="93.184.216.34"
            ), patch("public_download._PinnedHTTPSConnection", FakeConnection), self.assertRaisesRegex(
                PublicDownloadError, "size limit"
            ):
                _download("https://example.com/file", 10)


class StorageTests(unittest.TestCase):
    def test_atomic_nested_store_refuses_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            _store(project, "data/raw/report.csv", b"first")
            with self.assertRaisesRegex(PublicDownloadError, "already exists"):
                _store(project, "data/raw/report.csv", b"second")
            self.assertEqual((project / "data/raw/report.csv").read_bytes(), b"first")
            self.assertEqual(list((project / "data/raw").iterdir()), [project / "data/raw/report.csv"])

    def test_symlinked_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            project = Path(directory)
            (project / "data").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(PublicDownloadError, "unsafe"):
                _store(project, "data/file.bin", b"payload")
            self.assertEqual(list(Path(outside).iterdir()), [])


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _call_now(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def test_requires_active_turn_and_exact_owner_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            downloader = unittest.mock.Mock(return_value=(b"payload", "https://example.com/file", "text/plain"))
            gateway = PublicDownloadGateway(workspace=root, max_bytes=100, downloader=downloader)
            request = DownloadRequest("https://example.com/file", "downloads/file.txt")
            with self.assertRaisesRegex(PublicDownloadError, "active owner turn"):
                await gateway.download(request)
            approval = AsyncMock(return_value=True)
            lease = gateway.bind_turn(project, approval)
            with patch("public_download.asyncio.to_thread", side_effect=self._call_now):
                result = await gateway.download(request)
            gateway.unbind_turn(lease)
            approval.assert_awaited_once()
            self.assertEqual(approval.await_args.args[0].url, request.url)
            self.assertEqual(result["path"], request.destination)
            self.assertEqual(result["bytes"], 7)
            self.assertEqual((project / request.destination).read_bytes(), b"payload")

    async def test_rejection_performs_no_network_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            downloader = unittest.mock.Mock()
            gateway = PublicDownloadGateway(workspace=root, downloader=downloader)
            lease = gateway.bind_turn(project, AsyncMock(return_value=False))
            with patch("public_download.asyncio.to_thread", side_effect=self._call_now), self.assertRaisesRegex(
                PublicDownloadError, "rejected"
            ):
                await gateway.download(DownloadRequest("https://example.com/file", "file"))
            gateway.unbind_turn(lease)
            downloader.assert_not_called()
            self.assertEqual(list(project.iterdir()), [])

    async def test_socket_protocol_saves_after_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            gateway = PublicDownloadGateway(
                socket_path=root / "download.sock",
                workspace=root,
                max_bytes=100,
                downloader=lambda url, limit: (b"ok", url, "application/octet-stream"),
            )
            try:
                await gateway.start()
            except PermissionError as exc:
                if exc.errno == 1:
                    self.skipTest("test sandbox does not permit binding Unix sockets")
                raise
            lease = gateway.bind_turn(project, AsyncMock(return_value=True))
            try:
                with patch("public_download.asyncio.to_thread", side_effect=self._call_now):
                    response = await request_gateway(
                        {"url": "https://example.com/file", "destination": "downloads/file.bin"},
                        gateway.socket_path,
                    )
            finally:
                gateway.unbind_turn(lease)
                await gateway.close()
            self.assertTrue(response["ok"])
            self.assertEqual(json.loads(json.dumps(response))["data"]["bytes"], 2)

    def test_maximum_cannot_be_configured_above_hard_ceiling(self):
        with self.assertRaises(ValueError):
            PublicDownloadGateway(max_bytes=DEFAULT_MAX_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
