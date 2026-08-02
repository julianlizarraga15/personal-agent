# Operations runbook

## Start locally

1. Copy `.env.example` to `.env` and fill in the Telegram token and allowed user ID. To enable football analytics, add the API-Sports dashboard value as `API_FOOTBALL_KEY`; never send it through Telegram. To enable audio, create a dedicated OpenAI Platform key, store it as an owner-readable `api-key` file outside the workspace, set `OPENAI_TRANSCRIPTION_SECRETS_DIR` to its directory, and copy `docker-compose.transcription.example.yml` to the ignored `docker-compose.transcription.yml`.
2. Build the bot and complete one-time ChatGPT device login:

   ```bash
   docker compose build bot
   docker compose run --rm codex-login
   ```

3. Start the bot with `docker compose up -d bot`. The Docker-privileged deployer is disabled by default.
4. Put a checkout under `workspace/`, send `/project <directory-name>` from the configured Telegram account, then send ordinary text or supported voice/audio. `/run` and deployment commands are unavailable.

## Health checks

- Confirm both images build successfully with `docker compose build`.
- Confirm the test suite with `python -m pytest` (or the development environment equivalent).
- Inspect `docker compose logs -f bot` while submitting a task.
- Confirm `/help` lists only `/project`, `/new`, `/stop`, and `/help`.
- Confirm a normal text turn, continued context, repository read/edit/test work, `/new`, `/project`, `/stop`, progress editing, and Markdown rendering.
- With the transcription override enabled, confirm one captionless voice note becomes the Codex request and one captioned note applies the caption as its instruction. During a longer transcription, issue `/new` and confirm the old result never enters the fresh conversation.
- Confirm `/openai-transcription-secrets` is mounted read-only, is denied inside a Codex command, and `OPENAI_API_KEY`, `OPENAI_TRANSCRIPTION_KEY_PATH`, and `OPENAI_TRANSCRIPTION_MODEL` are absent from the sanitized Codex launcher environment. Inspect logs only for content-free success/failure metadata, then confirm usage in the OpenAI dashboard.
- Restart the bot and confirm the conversation is fresh while ChatGPT authentication still works.
- Attempt an outside-workspace write, shell network access, Docker access, and Git push; each must fail without an approval prompt.
- Ask Codex to use the API-Football MCP tool for `status`. With a key it should return status JSON without a network approval; without one it should clearly report that API-Football is not configured. Confirm sandboxed commands cannot read the key, `/trace-state`, `.env`, any Unix socket, or direct network. Use `api-football get status` only as a trusted container-side diagnostic.

## Common failures

- Missing environment variables: check `.env` and run `docker compose config`.
- Codex sign-in missing: rerun `docker compose run --rm codex-login` and protect the `codex-state` volume.
- Codex transport failure: retry once, then restart the bot and inspect content-free container logs.
- Subscription limit: wait for the ChatGPT Codex limit to reset; pass 1 does not fall back to API billing.
- Sandbox denial: keep the request inside the selected workspace and do not expect an approval prompt.
- Test failure: use the worker output to reproduce the project test command in an isolated checkout.
- Agent tool failure: verify the selected project exists under `workspace/` and rebuild after dependency changes.
- Image rejected: incoming images remain unsupported in Codex mode.
- Audio transcription unavailable: verify the local transcription override is included, the private directory contains a non-empty `api-key`, and the mount is read-only at `/openai-transcription-secrets`. Do not print the key. Missing configuration is non-fatal; provider failures return a stable retry message.
- Trace unavailable: verify the `trace-state` Docker volume, `TRACE_DB_PATH`, free space, and database ownership. Tracing resumes on later writes after storage recovers; stdout deliberately does not contain the omitted private content.
- API-Football unavailable: verify the bot owns `/run/api-football.sock`, the private host `.env` contains the dashboard key, and `/trace-state/api-football-quota.json` is writable. Do not print either file. A missing key is non-fatal; an insecure socket startup or unavailable quota store fails requests safely.
- Deployment remains queued: inspect deployer logs and verify its heartbeat under `workspace/.personal-agent-state`.
- State storage full/unavailable: free space or restore the mount. The deployer retains an active request when failure state cannot be persisted and resumes it after restart. The bot continues answering when the usage ledger or trace database cannot be written, reports durable usage totals as unavailable or incomplete, logs trace write loss without private content, and resumes recording future requests when storage recovers.

## Stop and recover

Stop services with `docker compose down`. Failed tasks should not publish a branch; inspect logs before retrying. For a partially-created remote branch, review it first, then delete it through the Git host only after confirming it is safe to remove.

## Manual deployment and Responses rollback

Before building on the trusted host, fetch `origin/main`, require local `HEAD` to match it, and require a clean worktree. Then build and restart the bot with the base Compose file plus the existing Git-publication override and the local transcription override; the Telegram process must not publish, rebuild, or restart itself. For emergency Responses rollback, set `AGENT_BACKEND=responses` and inject `OPENAI_API_KEY` through a separate private local Compose override. Never reconnect the bot to the Docker socket or deployer queue.

## Optional deployer recovery

The deployer uses a private `deployer-state` volume, verifies the requested commit against `DEPLOY_REMOTE_URL` and `DEPLOY_REMOTE_REF`, and is behind the `manual-deployer` profile with no restart policy. Start it only for a trusted recovery procedure and stop it afterward.

## Historical self-deployment recovery

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
- Confirm `TRACE_RETENTION_DAYS` matches policy (seven days by default) and that access to the `trace-state` Docker volume is restricted to the operator.
- Periodically review `projects.yaml` and remove repositories no longer in scope.
