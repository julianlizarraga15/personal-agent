import asyncio
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from git_publish import (
    GitPublishError,
    GitPublishGateway,
    _create_source_bundle,
    _publish_bundle,
    _inspect_source,
    _source_sandbox_command,
    validate_request,
)


COMMIT = "a" * 40


class GitPublishValidationTests(unittest.TestCase):
    def test_request_requires_exact_full_commit(self):
        self.assertEqual(validate_request({"commit": COMMIT}), COMMIT)
        for value in ({}, {"commit": "abc"}, {"commit": COMMIT, "branch": "main"}, {"commit": "A" * 40}):
            with self.subTest(value=value), self.assertRaises(GitPublishError):
                validate_request(value)

    def test_repository_must_be_internal_and_reject_url_rewrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            repository = workspace / "mental-models"
            git_dir = repository / ".git"
            git_dir.mkdir(parents=True)
            (git_dir / "config").write_text('[url "ssh://attacker.invalid/"]\n\tinsteadOf = git@github.com:\n')
            secrets = root / "secrets"
            secrets.mkdir()
            key = secrets / "deploy-key"
            known_hosts = secrets / "known_hosts"
            key.write_text("private")
            key.chmod(0o600)
            known_hosts.write_text("github.com key")
            gateway = GitPublishGateway(
                repository,
                "git@github.com:julianlizarraga15/mental-models.git",
                "main",
                workspace=workspace,
                secrets_root=secrets,
                key_path=key,
                known_hosts_path=known_hosts,
            )
            with self.assertRaisesRegex(GitPublishError, "disallowed URL"):
                gateway._configuration()

    def test_secrets_must_be_outside_workspace_with_private_key_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            repository = workspace / "project"
            git_dir = repository / ".git"
            git_dir.mkdir(parents=True)
            (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
            (git_dir / "objects" / "info").mkdir(parents=True)
            secrets = workspace / "secrets"
            secrets.mkdir()
            key = secrets / "deploy-key"
            known_hosts = secrets / "known_hosts"
            key.write_text("private")
            key.chmod(0o600)
            known_hosts.write_text("github.com key")
            gateway = GitPublishGateway(
                repository,
                "git@github.com:owner/project.git",
                "main",
                workspace=workspace,
                secrets_root=secrets,
                key_path=key,
                known_hosts_path=known_hosts,
            )
            with self.assertRaisesRegex(GitPublishError, "outside the workspace"):
                gateway._configuration()


class GitPublishGatewayTests(unittest.IsolatedAsyncioTestCase):
    def _configured_gateway(self, directory: str) -> GitPublishGateway:
        root = Path(directory)
        workspace = root / "workspace"
        repository = workspace / "mental-models"
        git_dir = repository / ".git"
        git_dir.mkdir(parents=True)
        (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
        (git_dir / "objects" / "info").mkdir(parents=True)
        secrets = root / "secrets"
        secrets.mkdir()
        key = secrets / "deploy-key"
        known_hosts = secrets / "known_hosts"
        key.write_text("private")
        key.chmod(0o600)
        known_hosts.write_text("github.com key")
        return GitPublishGateway(
            repository,
            "git@github.com:julianlizarraga15/mental-models.git",
            "main",
            workspace=workspace,
            secrets_root=secrets,
            key_path=key,
            known_hosts_path=known_hosts,
        )

    async def test_publish_requires_clean_exact_head_and_owner_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self._configured_gateway(directory)
            approval = AsyncMock(return_value=True)
            lease = gateway.bind_approval(approval)
            inspect = unittest.mock.Mock(side_effect=[(COMMIT, ""), (COMMIT, "")])
            create_bundle = unittest.mock.Mock()
            publish_bundle = unittest.mock.Mock()
            async def call_now(function, *args, **kwargs):
                return function(*args, **kwargs)
            with (
                patch("git_publish._inspect_source", inspect),
                patch("git_publish._create_source_bundle", create_bundle),
                patch("git_publish._publish_bundle", publish_bundle),
                patch("git_publish.asyncio.to_thread", side_effect=call_now),
            ):
                result = await gateway.publish(COMMIT)
            gateway.unbind_approval(lease)

            self.assertEqual(result["commit"], COMMIT)
            approval.assert_awaited_once()
            self.assertEqual(inspect.call_count, 2)
            create_bundle.assert_called_once()
            publish_bundle.assert_called_once()
            self.assertEqual(publish_bundle.call_args.args[1:5], (
                COMMIT,
                "git@github.com:julianlizarraga15/mental-models.git",
                "main",
                gateway.key_path.resolve(),
            ))

    async def test_publish_rejects_dirty_or_changed_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = self._configured_gateway(directory)
            lease = gateway.bind_approval(AsyncMock(return_value=True))
            async def call_now(function, *args, **kwargs):
                return function(*args, **kwargs)
            with patch("git_publish._inspect_source", return_value=(COMMIT, " M index.html")), patch("git_publish.asyncio.to_thread", side_effect=call_now):
                with self.assertRaisesRegex(GitPublishError, "Commit all"):
                    await gateway.publish(COMMIT)
            with patch("git_publish._inspect_source", return_value=("b" * 40, "")), patch("git_publish.asyncio.to_thread", side_effect=call_now):
                with self.assertRaisesRegex(GitPublishError, "changed"):
                    await gateway.publish(COMMIT)
            gateway.unbind_approval(lease)

    def test_source_git_is_networkless_and_masks_the_secrets_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "workspace" / "project"
            repository.mkdir(parents=True)
            secrets = root / "secrets"
            secrets.mkdir()
            command = _source_sandbox_command(
                repository,
                ["status", "--porcelain=v1"],
                secrets_root=secrets,
            )

            self.assertIn("--unshare-all", command)
            self.assertIn("--clearenv", command)
            self.assertIn("/run", command)
            masked = [Path(command[index + 1]) for index, value in enumerate(command[:-1]) if value == "--tmpfs"]
            self.assertTrue(any(secrets.is_relative_to(path) for path in masked))
            self.assertNotIn("GIT_SSH_COMMAND", command)
            self.assertIn(f"safe.directory={repository}", command)
            self.assertEqual(command[-2:], ["status", "--porcelain=v1"])

    def test_credentials_are_used_only_with_a_fresh_bare_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "source.bundle"
            bundle.write_bytes(b"bundle")
            key = root / "deploy-key"
            known_hosts = root / "known_hosts"
            key.write_text("private")
            known_hosts.write_text("github.com key")
            run_git = unittest.mock.Mock(side_effect=["", COMMIT, ""])
            with patch("git_publish._run_git", run_git):
                _publish_bundle(
                    bundle,
                    COMMIT,
                    "git@github.com:owner/project.git",
                    "main",
                    key,
                    known_hosts,
                )

            clone, verify, push = run_git.call_args_list
            self.assertNotIn("GIT_SSH_COMMAND", clone.kwargs["env"])
            self.assertNotIn("GIT_SSH_COMMAND", verify.kwargs["env"])
            self.assertIn("GIT_SSH_COMMAND", push.kwargs["env"])
            self.assertNotEqual(push.args[0], bundle.parent)
            self.assertIn("--no-verify", push.args[1])

    def test_bundle_is_rehomed_and_exact_commit_is_pushed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()

            def git(cwd: Path, *args: str) -> str:
                return subprocess.run(
                    ["/usr/bin/git", *args],
                    cwd=cwd,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).stdout.strip()

            git(source, "init", "-b", "main")
            git(source, "config", "user.name", "Test")
            git(source, "config", "user.email", "test@example.invalid")
            (source / "README.md").write_text("safe\n")
            git(source, "add", "README.md")
            git(source, "commit", "-m", "initial")
            commit = git(source, "rev-parse", "HEAD")
            bundle = root / "repository.bundle"
            git(source, "bundle", "create", str(bundle), "HEAD")
            remote = root / "remote.git"
            git(root, "init", "--bare", str(remote))
            key = root / "deploy-key"
            known_hosts = root / "known_hosts"
            key.write_text("unused-local-test-key")
            known_hosts.write_text("unused local test host")

            _publish_bundle(bundle, commit, str(remote), "main", key, known_hosts)

            self.assertEqual(git(root, "--git-dir", str(remote), "rev-parse", "refs/heads/main"), commit)

    @unittest.skipUnless(
        Path("/usr/bin/bwrap").exists()
        and Path("/workspace").is_dir()
        and Path("/git-publish-secrets").is_dir(),
        "requires the built bot container and its publication mounts",
    )
    def test_source_filter_cannot_read_the_deploy_key_in_bubblewrap(self):
        with tempfile.TemporaryDirectory(dir="/workspace") as workspace_directory:
            workspace = Path(workspace_directory)
            repository = workspace / "project"
            repository.mkdir()
            key = Path("/git-publish-secrets/deploy-key")
            key.write_text("test-private-key")
            key.chmod(0o600)

            def git(*args: str) -> None:
                subprocess.run(
                    ["/usr/bin/git", *args],
                    cwd=repository,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            git("init", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repository / "payload.txt").write_text("safe\n")
            (repository / ".gitattributes").write_text("payload.txt filter=leak\n")
            git("add", ".")
            git("commit", "-m", "initial")
            git(
                "config",
                "filter.leak.clean",
                f"/bin/sh -c 'if test -r {key}; then exit 91; fi; cat'",
            )
            git("config", "filter.leak.required", "true")

            head, status = _inspect_source(repository, Path("/git-publish-secrets"))
            stage = workspace / "stage"
            stage.mkdir()
            bundle = stage / "repository.bundle"
            _create_source_bundle(repository, Path("/git-publish-secrets"), bundle)
            safe_repository = workspace / "safe.git"
            subprocess.run(
                ["/usr/bin/git", "clone", "--bare", "--no-local", str(bundle), str(safe_repository)],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            bundled_head = subprocess.run(
                ["/usr/bin/git", "--git-dir", str(safe_repository), "rev-parse", "HEAD"],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            ).stdout.strip()

            self.assertRegex(head, r"^[0-9a-f]{40}$")
            self.assertEqual(status, "")
            self.assertEqual(bundled_head, head)

    async def test_missing_configuration_is_a_safe_degraded_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = GitPublishGateway(None, None, None, socket_path=Path(directory) / "publish.sock")
            try:
                await gateway.start()
            except PermissionError as exc:
                if exc.errno == 1:
                    self.skipTest("test sandbox does not permit binding Unix sockets")
                raise
            try:
                reader, writer = await asyncio.open_unix_connection(str(gateway.socket_path))
                writer.write(f'{{"commit":"{COMMIT}"}}\n'.encode())
                await writer.drain()
                response = await reader.readline()
                writer.close()
                await writer.wait_closed()
            finally:
                await gateway.close()
            self.assertIn(b"Git publication is not configured", response)


if __name__ == "__main__":
    unittest.main()
