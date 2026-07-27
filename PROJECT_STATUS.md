# Project status

Last updated: 2026-07-27

## Current state

- Repository setup and initial project structure are present.
- A model-backed conversational agent prototype is present in `src/agent.py`.
- Telegram integration can handle ordinary messages in the default workspace, `/project`, `/new`, `/stop`, and legacy `/run`.
- File writes and dedicated Git commit/push actions now pause for Telegram approval via `/approve <id>` or `/reject <id>`.
- The conversational agent now has the OpenAI Responses API hosted `web_search` tool for current external information and source citations.
- A conservative configurable small-model router now answers high-confidence simple messages directly and falls back to the large model for coding, tools, web search, ambiguity, or routing failures.
- ADR 0003 and the architecture, security, README, and runbook documentation describe the Responses API and constrained computer tools.
- The current outer checkout is on `main`, one commit ahead of `origin/main` (`30914bb`, commit subject `l`).
- An older, clean `codex/initial-project` checkout also exists under `workspace/personal-agent`; it was left untouched.
- The live Telegram/Docker deployment is verified with a healthy bot and persistent deployer service.
- Self-deployment now has durable restart state, single-flight locking, readiness verification, rollback, main/origin preflight, and `/pending` status reporting.
- Self-deployment now uses a persistent deployer service, so rollback authority survives bot replacement and clean published commits can be redeployed after interrupted attempts.

## What we are building

- A Telegram front end and Dockerized worker for a personal computer agent running on a VM.
- The desired product is a friend-like agent: normal chat by default, with local file and command execution when requested.

## Last stopping point

- Persistent-controller deployment hardening and the Cornelio identity change are deployed.

## Next steps

- Continue normal product work; update the stable deployer image manually only when deployment infrastructure changes.
- Make the model provider configurable (`OPENAI_BASE_URL`, API key, and model), keeping direct OpenAI as the default and adding OpenRouter as an optional backend.
- Verify the bot end-to-end with Docker and Telegram using a non-sensitive workspace checkout.
- Move tool execution behind a persistent sandbox container and remove the bot's direct Docker-socket dependency when practical.

## Decisions and assumptions

- Keep changes focused and preserve user changes.
- Do not expose secrets or weaken the isolation boundary.
- The OpenAI model is hosted remotely and is called through `OPENAI_API_KEY`; the local bot program receives tool requests and executes them in its allowed container workspace.
- Sessions are currently in memory and are lost when the bot restarts.
- The conversational agent uses the mounted workspace as its default `computer` context; `/project` can narrow it to a subdirectory.
- Direct OpenAI remains the default provider; OpenRouter is a possible compatibility/fallback backend, not yet implemented.
- Approval is exact-action, single-pending-request, text-command based, and expires after five minutes.

## Validation

- Current validation: 63 unit tests, Python compilation, `docker compose config`, and `git diff --check` pass. Coverage includes controller restart, Docker/build/recreate/rollback failures, storage failure, corrupt state, Git network/auth conflicts, and Telegram delivery retry. A live queued deployment and controlled startup-failure rollback both completed successfully; bot and deployer containers are healthy.
- The required literal `python -m pytest` validation is unavailable because `python` is not installed in the environment.
- `python3 -m pytest` is also unavailable because pytest is not installed.
- Python compilation and `docker compose config` pass with placeholder validation environment values.
- No live Docker build, bot launch, Telegram exchange, model call, or push has been validated here.
