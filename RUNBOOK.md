# Operations runbook

## Start locally

1. Copy `.env.example` to `.env` and fill in the Telegram token and allowed user ID.
2. Build and start the services:

   ```bash
   docker compose up --build
   ```

3. Put a checkout under `workspace/`, send `/project <directory-name>` from the configured Telegram account, then send ordinary text. `/run <git-url> <task>` remains available for one-shot tasks.

## Health checks

- Confirm both images build successfully with `docker compose build`.
- Confirm the test suite with `python -m pytest` (or the development environment equivalent).
- Inspect `docker compose logs -f bot` while submitting a task.
- Send `/usage` to inspect session token totals, cache activity, web-search calls, and the dated cost estimate. Compare material spend with the OpenAI billing dashboard.
- A successful legacy worker reports a `codex/*` branch, commit, and test status.
- The conversational agent requires `OPENAI_API_KEY` and operates only inside the mounted `workspace/` directory.
- The bot keeps the selected project and recent conversation in memory; restart the bot or use `/new` to clear it.
- Self-deployment requires the repository at `workspace/personal-agent` and a running, healthy `deployer` service. `HOST_WORKSPACE_DIR`, `GIT_SSH_KEY_PATH`, and `GIT_KNOWN_HOSTS_PATH` must be absolute host paths so Compose can safely recreate the bot from inside the deployer.
- Confirm controller availability with `docker compose ps deployer` and `/pending` before requesting deployment.

## Common failures

- Missing environment variables: check `.env` and run `docker compose config`.
- Docker worker cannot start: verify Docker Engine is running and the socket mount is available to the bot.
- Clone or push failure: verify repository access and the Git credential used by the worker.
- Codex cannot start: verify the CLI and its authentication are available inside the worker runtime.
- Test failure: use the worker output to reproduce the project test command in an isolated checkout.
- Agent tool failure: verify the project exists under `workspace/`, the OpenAI API key is configured, and the bot image has been rebuilt after dependency changes.
- Image rejected: use a JPEG, PNG, WEBP, or non-animated GIF under `TELEGRAM_MAX_IMAGE_BYTES` (10 MiB by default). Resend the image for later visual follow-ups because image bytes are not retained after the turn.
- Audio rejected: use OGG, MP3/MPEG/MPGA, MP4/M4A, WAV, WebM, or FLAC under `TELEGRAM_MAX_AUDIO_BYTES` (20 MB by default) and `TELEGRAM_MAX_AUDIO_SECONDS` (10 minutes by default). Telegram documents need a supported extension or an `audio/*` MIME type to reach the handler. Check OpenAI connectivity when validation succeeds but transcription fails.
- Unexpected API cost: inspect per-response usage logs and `/usage`, confirm the intended route/model, and check whether repeated file or command output crossed the compaction or high-usage threshold.
- Deployment remains queued: inspect deployer logs and verify its heartbeat under `workspace/.personal-agent-state`.
- State storage full/unavailable: free space or restore the mount, then restart the deployer. It retains the active request when failure state cannot be persisted and resumes it after restart.

## Stop and recover

Stop services with `docker compose down`. Failed tasks should not publish a branch; inspect logs before retrying. For a partially-created remote branch, review it first, then delete it through the Git host only after confirming it is safe to remove.

## Self-deployment recovery

When the agent is asked to modify itself, keep the checkout on `main`. One approval covers tests, direct publication, queueing, rebuild, and restart. The persistent deployer tags the current image, rebuilds only the bot, verifies Docker health for a stability window, and rolls back automatically when startup fails. `/pending` reads the durable manifest even when conversational state has been lost.

Deployment-controller changes are host-managed. From the outer deployment checkout, build the separate controller image first, then recreate the bot from the live nested checkout:

```bash
docker compose --env-file .env -f workspace/personal-agent/docker-compose.yml up -d --build --force-recreate deployer
docker compose --env-file .env -f workspace/personal-agent/docker-compose.yml up -d --build --force-recreate bot
```

Do not recreate the deployer during ordinary self-deployment. If automatic rollback reports `rollback_failed`, inspect the manifest and deployer logs, retag the recorded `rollback_image` as `personal-agent-bot:latest`, and recreate only `bot` with the same Compose file.

## Maintenance checklist

- Review dependency and base-image updates.
- Rotate tokens and verify the allowed Telegram user list.
- Check disk space consumed by Docker images and build cache.
- Periodically review `projects.yaml` and remove repositories no longer in scope.
