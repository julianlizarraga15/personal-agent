# Project status

Last updated: 2026-07-23

## Current state

- Repository setup and initial project structure are present.
- A model-backed conversational agent prototype is present in `src/agent.py`.
- Telegram integration can handle ordinary messages in the default workspace, `/project`, `/new`, `/stop`, and legacy `/run`.
- File writes and dedicated Git commit/push actions now pause for Telegram approval via `/approve <id>` or `/reject <id>`.
- ADR 0003 and the architecture, security, README, and runbook documentation describe the Responses API and constrained computer tools.
- The initial project files are committed locally on `codex/initial-project` (`af3b7c1`). The branch has not been pushed because no `origin` remote is configured and the available GitHub CLI token is invalid.
- The live Telegram/Docker deployment has not been verified because no Docker daemon is available in this environment.

## What we are building

- A Telegram front end and Dockerized worker for a personal computer agent running on a VM.
- The desired product is a friend-like agent: normal chat by default, with local file and command execution when requested.

## Next steps

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

- Current validation: `PYTHONPATH=src python3 -m unittest discover -s tests` passes (16 tests).
- The required literal `python -m pytest` validation is unavailable because `python` is not installed in the environment.
- Python compilation and `docker compose config` pass with placeholder validation environment values.
- No live Docker build, bot launch, Telegram exchange, model call, or push has been validated here.
