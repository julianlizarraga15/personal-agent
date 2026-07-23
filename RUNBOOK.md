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
- A successful legacy worker reports a `codex/*` branch, commit, and test status.
- The conversational agent requires `OPENAI_API_KEY` and operates only inside the mounted `workspace/` directory.
- The bot keeps the selected project and recent conversation in memory; restart the bot or use `/new` to clear it.

## Common failures

- Missing environment variables: check `.env` and run `docker compose config`.
- Docker worker cannot start: verify Docker Engine is running and the socket mount is available to the bot.
- Clone or push failure: verify repository access and the Git credential used by the worker.
- Codex cannot start: verify the CLI and its authentication are available inside the worker runtime.
- Test failure: use the worker output to reproduce the project test command in an isolated checkout.
- Agent tool failure: verify the project exists under `workspace/`, the OpenAI API key is configured, and the bot image has been rebuilt after dependency changes.

## Stop and recover

Stop services with `docker compose down`. Failed tasks should not publish a branch; inspect logs before retrying. For a partially-created remote branch, review it first, then delete it through the Git host only after confirming it is safe to remove.

## Maintenance checklist

- Review dependency and base-image updates.
- Rotate tokens and verify the allowed Telegram user list.
- Check disk space consumed by Docker images and build cache.
- Periodically review `projects.yaml` and remove repositories no longer in scope.
