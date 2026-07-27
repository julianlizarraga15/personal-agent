# Project status

Last updated: 2026-07-27

## Current state

- Repository setup and initial project structure are present.
- A model-backed conversational agent prototype is present in `src/agent.py`.
- Telegram integration can handle ordinary messages in the default workspace, `/project`, `/new`, `/stop`, `/usage`, and legacy `/run`.
- File writes and dedicated Git commit/push actions now pause for Telegram approval via 👍/👎 reactions on the exact prompt or `/approve <id>` and `/reject <id>`.
- The conversational agent now has the OpenAI Responses API hosted `web_search` tool for current external information and source citations.
- A configurable cost-aware router uses GPT-5 nano for high-confidence simple answers, Luna for ordinary reasoning/web work, Terra for routine computer work, and Sol for difficult, ambiguous, high-stakes, or deployment work.
- Main-model requests use explicit effort, verbosity, output limits, selected capabilities, and server-side compaction. File listings, reads, command output, and exact-fragment edits are designed to reduce repeated tool-context cost.
- Per-response token/cache/search usage is logged; `/usage` shows current-session totals and a dated estimated cost, while unusually large turns warn without being interrupted.
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

- Cost-aware model execution and usage reporting are published to `main` and deployed; the replacement bot reached Docker health.

## Next steps

- Continue normal product work; update the stable deployer image manually only when deployment infrastructure changes.
- Exercise `/usage`, each model route, compaction, and a bounded file-read continuation in a non-sensitive live Telegram session.
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
- Cost estimates use standard OpenAI short-context prices as of 2026-07-27; provider billing remains authoritative, and custom model prices are reported as unknown.
- The high-usage threshold is advisory: it records one warning per expensive turn but does not stop an active task.

## Validation

- Current validation: 88 unit tests pass via `python -m pytest` in a temporary environment, including cost-aware routing, request controls, compaction pruning, bounded tools, token/cost accounting, `/usage`, session resets, reaction authorization, and deployment behavior. Python compilation and `git diff --check` also pass.
- The cost-aware bot image built successfully and the replacement bot reached Docker health; a live `/usage` exchange and model call have not yet been exercised.
- Earlier live deployment validation covered a queued deployment and controlled startup-failure rollback; the bot and deployer containers were healthy at that stopping point.
- The reaction change was pushed to `origin/main`; the bot image built successfully, the replacement bot reached Docker health, and the persistent deployer remained running. A live Telegram reaction exchange and model call have not been exercised.
