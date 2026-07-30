# Operations runbook

## Start locally

1. Copy `.env.example` to `.env` and fill in the Telegram token and allowed user ID.
2. Build the bot and complete one-time ChatGPT device login:

   ```bash
   docker compose build bot
   docker compose run --rm codex-login
   ```

3. Start the services with `docker compose up -d bot deployer`.
4. Put a checkout under `workspace/`, send `/project <directory-name>` from the configured Telegram account, then send ordinary text. Pass 1 is text-only; `/run` and deployment commands are unavailable.

## Health checks

- Confirm both images build successfully with `docker compose build`.
- Confirm the test suite with `python -m pytest` (or the development environment equivalent).
- Inspect `docker compose logs -f bot` while submitting a task.
- Confirm `/help` lists only `/project`, `/new`, `/stop`, and `/help`.
- Confirm a normal text turn, continued context, repository read/edit/test work, `/new`, `/project`, `/stop`, progress editing, and Markdown rendering.
- Restart the bot and confirm the conversation is fresh while ChatGPT authentication still works.
- Attempt an outside-workspace write, shell network access, Docker access, and Git push; each must fail without an approval prompt.

## Common failures

- Missing environment variables: check `.env` and run `docker compose config`.
- Codex sign-in missing: rerun `docker compose run --rm codex-login` and protect the `codex-state` volume.
- Codex transport failure: retry once, then restart the bot and inspect content-free container logs.
- Subscription limit: wait for the ChatGPT Codex limit to reset; pass 1 does not fall back to API billing.
- Sandbox denial: keep the request inside the selected workspace and do not expect an approval prompt.
- Test failure: use the worker output to reproduce the project test command in an isolated checkout.
- Agent tool failure: verify the selected project exists under `workspace/` and rebuild after dependency changes.
- Image/audio rejected: pass 1 accepts text only.
- Trace unavailable: verify the `trace-state` Docker volume, `TRACE_DB_PATH`, free space, and database ownership. Tracing resumes on later writes after storage recovers; stdout deliberately does not contain the omitted private content.
- Deployment remains queued: inspect deployer logs and verify its heartbeat under `workspace/.personal-agent-state`.
- State storage full/unavailable: free space or restore the mount. The deployer retains an active request when failure state cannot be persisted and resumes it after restart. The bot continues answering when the usage ledger or trace database cannot be written, reports durable usage totals as unavailable or incomplete, logs trace write loss without private content, and resumes recording future requests when storage recovers.

## Stop and recover

Stop services with `docker compose down`. Failed tasks should not publish a branch; inspect logs before retrying. For a partially-created remote branch, review it first, then delete it through the Git host only after confirming it is safe to remove.

## Manual deployment and Responses rollback

Build and restart from the trusted host; the Telegram bot must not publish, rebuild, or restart itself. For emergency rollback, set `AGENT_BACKEND=responses`, inject `OPENAI_API_KEY` and any legacy privileged mounts through a private local Compose override, then rebuild and recreate the bot. Never commit that override.

## Dormant self-deployment recovery

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
