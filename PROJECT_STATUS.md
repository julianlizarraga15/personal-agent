# Project status

Last updated: 2026-07-27

## Current state

- Repository setup and initial project structure are present.
- A model-backed conversational agent prototype is present in `src/agent.py`.
- Telegram integration can handle ordinary messages, validated image turns, transcribed voice/audio turns, `/project`, `/new`, `/stop`, `/usage`, and legacy `/run` in the default workspace.
- File writes and dedicated Git commit/push actions now pause for Telegram approval via 👍/👎 reactions on the exact prompt or `/approve <id>` and `/reject <id>`.
- The conversational agent now has the OpenAI Responses API hosted `web_search` tool for current external information and source citations.
- A configurable cost-aware router uses GPT-5 nano for high-confidence simple answers, Luna for ordinary reasoning/web work, Terra for routine computer work, and Sol for difficult, ambiguous, high-stakes, or deployment work.
- Main-model requests use explicit effort, verbosity, output limits, selected capabilities, and server-side compaction. File listings, reads, command output, and exact-fragment edits are designed to reduce repeated tool-context cost.
- Per-response token/cache/search usage is logged and appended without message content to a host-persistent SQLite ledger; `/usage` shows current-session, current UTC day, and all recorded totals with a dated estimated cost, while unusually large turns warn without being interrupted.
- ADR 0003 and the architecture, security, README, and runbook documentation describe the Responses API and constrained computer tools.
- The live deployment checkout is on `main` and aligned with `origin/main`; the separate outer deployment checkout remains at `a4d27eb` and is not the self-deployment source.
- Local reaction-handler edits that predated the deployment are preserved in the live checkout as the named stash `pre-reaction-deploy-local-edits`; the published implementation supersedes their behavior.
- The live Telegram/Docker deployment is verified with a healthy bot and persistent deployer service.
- Self-deployment now has durable restart state, single-flight locking, readiness verification, rollback, main/origin preflight, and `/pending` status reporting.
- Self-deployment now uses a persistent deployer service, so rollback authority survives bot replacement and clean published commits can be redeployed after interrupted attempts.

## What we are building

- A Telegram front end and Dockerized worker for a personal computer agent running on a VM.
- The desired product is a friend-like agent: normal chat by default, with local file and command execution when requested.

## Last stopping point

- Durable usage accounting is implemented, tested, published, and deployed. Historical usage from before this release was not recoverable; the new lifetime ledger starts with the first post-deployment direct OpenAI request.

## Next steps

- Continue normal product work; update the stable deployer image manually only when deployment infrastructure changes.
- Exercise `/usage` after a non-sensitive model turn, then verify its daily and lifetime totals remain after a later bot restart.
- Exercise each model route, compaction, and a bounded file-read continuation in a non-sensitive live Telegram session.
- Send a non-sensitive photo and image document through the live bot after deployment, including a captionless image and an oversize/unsupported rejection case.
- Publish and deploy audio support, then exercise a non-sensitive voice note and audio attachment with and without a caption plus an oversize/unsupported rejection case.
- Make the model provider configurable (`OPENAI_BASE_URL`, API key, and model), keeping direct OpenAI as the default and adding OpenRouter as an optional backend.
- Verify the bot end-to-end with Docker and Telegram using a non-sensitive workspace checkout.
- Move tool execution behind a persistent sandbox container and remove the bot's direct Docker-socket dependency when practical.

## Decisions and assumptions

- Keep changes focused and preserve user changes.
- Do not expose secrets or weaken the isolation boundary.
- The OpenAI model is hosted remotely and is called through `OPENAI_API_KEY`; the local bot program receives tool requests and executes them in its allowed container workspace.
- Conversation sessions are in memory and are lost when the bot restarts; direct OpenAI usage metadata persists outside Git at `/workspace/.personal-agent-state/usage.sqlite3` by default.
- The conversational agent uses the mounted workspace as its default `computer` context; `/project` can narrow it to a subdirectory.
- Direct OpenAI remains the default provider; OpenRouter is a possible compatibility/fallback backend, not yet implemented.
- Approval is exact-action, single-pending-request, bound to its Telegram prompt for reactions, and expires after five minutes; text commands remain available.
- Cost estimates use standard OpenAI short-context prices as of 2026-07-27; provider billing remains authoritative, and custom model prices are reported as unknown.
- The high-usage threshold is advisory: it records one warning per expensive turn but does not stop an active task.
- Image turns accept one photo or supported image document up to 10 MiB by default, use high-detail vision, and discard image bytes after the current turn.
- Audio turns accept Telegram voice notes, audio attachments, and supported audio documents up to 20 MB and a reported 10-minute duration by default. They use `gpt-4o-mini-transcribe`, treat an optional caption as the instruction over the transcript, retain only text in session history, and report transcription cost as unknown until a verified price is added.

## Validation

- Current validation: 125 unit tests pass via `python -m pytest` in a temporary environment, including durable usage reopen/restart behavior, concurrent recording, UTC daily and lifetime aggregation, failure handling, audio admission, signature validation, transcription semantics and failures, image routing, multimodal request shape, current-turn cleanup, cost-aware routing, request controls, compaction pruning, bounded tools, token/cost accounting, `/usage`, session resets, reaction authorization, and deployment behavior. Python compilation and `git diff --check` also pass.
- The audio- and image-capable bot image builds successfully; its production audio handler registers and synchronous model calls can be offloaded from the event loop. Neither media path has yet been exercised against live Telegram or OpenAI services.
- The durable-usage bot image built successfully and the replacement bot reached Docker health; a live post-deployment `/usage` exchange and model call have not yet been exercised.
- Earlier live deployment validation covered a queued deployment and controlled startup-failure rollback; the bot and deployer containers were healthy at that stopping point.
- The reaction change was pushed to `origin/main`; the bot image built successfully, the replacement bot reached Docker health, and the persistent deployer remained running. A live Telegram reaction exchange and model call have not been exercised.
