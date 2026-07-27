# Project status

Last updated: 2026-07-27

## Current state

- Repository setup and initial project structure are present.
- A model-backed conversational agent prototype is present in `src/agent.py`.
- Telegram integration can handle ordinary messages in the default workspace, `/project`, `/new`, `/stop`, and legacy `/run`.
- File writes and dedicated Git commit/push actions now pause for Telegram approval via 👍/👎 reactions on the exact prompt or `/approve <id>` and `/reject <id>`.
- The conversational agent now has the OpenAI Responses API hosted `web_search` tool for current external information and source citations.
- A conservative configurable small-model router now answers high-confidence simple messages directly and falls back to the large model for coding, tools, web search, ambiguity, or routing failures.
- ADR 0003 and the architecture, security, README, and runbook documentation describe the Responses API and constrained computer tools.
- The outer and live deployment checkouts are on `main` and aligned with `origin/main`.
- Local reaction-handler edits that predated the deployment are preserved in the live checkout as the named stash `pre-reaction-deploy-local-edits`; the published implementation supersedes their behavior.
- The live Telegram/Docker deployment is verified with a healthy bot and persistent deployer service.
- Self-deployment now has durable restart state, single-flight locking, readiness verification, rollback, main/origin preflight, and `/pending` status reporting.
- Self-deployment now uses a persistent deployer service, so rollback authority survives bot replacement and clean published commits can be redeployed after interrupted attempts.

## What we are building

- A Telegram front end and Dockerized worker for a personal computer agent running on a VM.
- The desired product is a friend-like agent: normal chat by default, with local file and command execution when requested.

## Last stopping point

- Exact-message 👍 approval and 👎 rejection are published and deployed; the rebuilt bot and persistent deployer are healthy.

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
- Approval is exact-action, single-pending-request, bound to its Telegram prompt for reactions, and expires after five minutes; text commands remain available.

## Validation

- Current validation: 73 unit tests pass via `python -m pytest` in a temporary environment, including reaction authorization, exact-message matching, reaction handler registration, and polling configuration. Python compilation and `git diff --check` also pass.
- Earlier live deployment validation covered a queued deployment and controlled startup-failure rollback; the bot and deployer containers were healthy at that stopping point.
- The reaction change was pushed to `origin/main`; the bot image built successfully, the replacement bot reached Docker health, and the persistent deployer remained running. A live Telegram reaction exchange and model call have not been exercised.
