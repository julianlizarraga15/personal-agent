# Project status

Last updated: 2026-08-01

## Current state

- A repository-scoped Git publication path is published and deployed but not yet configured for a target repository. A required keyless MCP tool calls a bot-side Unix-socket gateway that validates one configured clean `HEAD`, requests exact-commit Telegram approval, rechecks it, creates a bundle in a credential-free networkless Bubblewrap, and performs the non-force GitHub SSH push only from a fresh bare repository with a dedicated deploy key unavailable to project commands or project Git configuration. Without the optional target/key mount, the healthy gateway returns a setup error.
- A credential-safe in-process API-Football gateway serves `/run/api-football.sock`. A required keyless stdio MCP tool gives Codex approved read-only access while project shell networking stays disabled; the bot alone holds `API_FOOTBALL_KEY` and attaches `x-apisports-key` to a fixed TLS-verified API-Sports origin. The trusted container-diagnostic CLI uses the same bounded protocol.
- The gateway rejects full URLs, traversal, non-GET and unknown endpoints, malformed or excessive parameters, odds, bookmakers, and predictions. Requests and JSON responses are bounded, proxy inheritance is ignored, errors are sanitized, and the credential is recursively removed from any returned value.
- A protected atomic counter in `trace-state` permits 100 attempted upstream calls per UTC day. Invalid local requests do not count and the first version has no cache. Missing configuration leaves the bot healthy and returns a clear helper error.
- Pass 1 defaults Telegram conversation to pinned `openai-codex==0.144.4` through one application-scoped `AsyncCodex` instance.
- Authorized users receive ephemeral, memory-only Codex threads with the selected directory as `cwd`, a named permissions profile extending `:workspace`, and no application model/personality/instruction overrides.
- System Bubblewrap now runs under a narrow Moby-derived seccomp profile, fixing the Docker namespace failure without added capabilities, privileged mode, or unconfined security settings. The image also includes pinned `uv`.
- A sanitized launcher prevents Codex from inheriting bot/API/Git/proxy secrets. Sandboxed commands cannot read `/codex-home`, `/trace-state`, or workspace `.env` files.
- Network is denied by default. Exact public HTTPS port-443 requests can be allowed once by the configured Telegram owner; requests expire after five minutes and are cancelled by `/new`, `/project`, and `/stop`. All other escalation fails closed.
- Codex mode accepts text and registers `/project`, `/new`, `/stop`, and `/help`. It debounces one coarse activity message, renders the completed agent message through the existing Markdown renderer, and can return up to four requested workspace images after path containment, protected-path, size, and real-format validation. Incoming media remains unsupported.
- `/new` resets to the workspace root, `/project` validates beneath it and starts fresh, and `/stop` interrupts and discards an active session. Restart loses conversations but preserves ChatGPT authentication in the private `codex-state` volume.
- The bot service no longer receives the Docker socket, Git SSH key, Git SSH command, OpenAI API key, or deployment queue. Pass-1 deployment is manual.
- The optional deployer is disabled by default, has no restart policy, and stores control state in a deployer-only volume. It rejects any commit that does not equal the configured published HTTPS remote ref and a clean local `HEAD`.
- A `codex-login` management service performs one-time ChatGPT device-code login into `CODEX_HOME` without baking authentication into the image.
- `AGENT_BACKEND=responses` retains the previous implementation as dormant rollback code. Its legacy Telegram commands and media handlers are not registered in Codex mode.

## Dormant Responses rollback baseline

- Repository setup and initial project structure are present.
- A model-backed conversational agent prototype is present in `src/agent.py`.
- Telegram integration can handle ordinary messages, validated image turns, transcribed voice/audio turns, `/project`, `/new`, `/stop`, `/usage`, and legacy `/run` in the default workspace.
- File writes and dedicated Git commit/push actions now pause for Telegram approval via 👍/👎 reactions on the exact prompt or `/approve <id>` and `/reject <id>`.
- The conversational agent now has the OpenAI Responses API hosted `web_search` tool for current external information and source citations.
- A configurable cost-aware router uses GPT-5 nano for high-confidence simple answers, Luna for ordinary reasoning/web work, Terra for routine computer work, and Sol for difficult, ambiguous, high-stakes, or deployment work.
- Main-model requests use explicit effort, verbosity, output limits, selected capabilities, and server-side compaction. File listings, reads, command output, and exact-fragment edits are designed to reduce repeated tool-context cost.
- Per-response token/cache/search usage is logged and appended without message content to a host-persistent SQLite ledger; `/usage` shows current-session, current UTC day, and all recorded totals with a dated estimated cost, while unusually large turns warn without being interrupted.
- Owner-transparent execution tracing records ordered, recursively redacted application-observable prompts, model/tool activity, approvals, complete tool output, and deployment stages for seven days by default. `/prompt`, `/traces`, and `/trace` are owner-only, and live activity messages carry the matching turn ID.
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

- Repository-scoped Git publication is credential-boundary hardened, published on `main` at `5a9194c`, and deployed. The production bot is healthy on image `sha256:6980ead49256f5e67291a2fc4d0e8e45f6cbbe90f76ac0475f2b8d63cba062c1` with zero restarts and the existing workspace, `codex-state`, and `trace-state` mounts retained. Its live gateway reports the expected unconfigured error because no target repository or deploy-key mount has been provisioned. The previous image remains tagged `personal-agent-bot:rollback-pre-git-publish-5a9194c`.

## Next steps

- Clone `mental-models` beneath the production workspace, generate a dedicated deploy key outside that workspace, add its public half to only that GitHub repository with write access, create verified GitHub `known_hosts`, and configure the documented local Compose override.
- After provisioning the repository-scoped key and local Compose override, exercise rejection and approval of one non-sensitive `mental-models` commit through Telegram.
- The configured API plan identifies Liga Profesional Argentina as league `128` with current season `2026`, but reports standings unavailable because accessible season data stops at 2024. Upgrade the API plan or use another authorized data source before expecting a current top-five table.
- Keep the project-command network profile disabled and retain the MCP boundary; the latest available `openai-codex==0.144.4` does not safely enforce its documented Unix-socket allowlist when profile networking is enabled.
- Verify authentication survives restart and that restart begins a fresh conversation.
- Exercise normal chat, repository read/edit/test work, `/new`, `/stop`, `/project`, progress editing, and formatted final responses.
- Exercise a dependency install through Telegram: reject once, approve the exact public HTTPS destination once, and confirm a later turn asks again.
- Confirm outside-workspace writing, auth/trace reads, Docker access, and credential-backed Git push remain denied and cannot produce an approval button.
- Keep `AGENT_BACKEND=responses` rollback dormant until pass 1 has been validated live; remove it only in pass 2.

### Historical Responses follow-ups

- Exercise `/usage` after a non-sensitive model turn, then verify its daily and lifetime totals remain after a later bot restart.
- Exercise `/prompt`, `/traces`, and partial/completed `/trace` exports with non-sensitive live content, verify live activity editing, and confirm seven-day retention on the persistent mount.
- Exercise each model route, compaction, and a bounded file-read continuation in a non-sensitive live Telegram session.
- Send a non-sensitive photo and image document through the live bot after deployment, including a captionless image and an oversize/unsupported rejection case.
- Publish and deploy audio support, then exercise a non-sensitive voice note and audio attachment with and without a caption plus an oversize/unsupported rejection case.
- Make the model provider configurable (`OPENAI_BASE_URL`, API key, and model), keeping direct OpenAI as the default and adding OpenRouter as an optional backend.
- Verify the bot end-to-end with Docker and Telegram using a non-sensitive workspace checkout.
- Move tool execution behind a persistent sandbox container and remove the bot's direct Docker-socket dependency when practical.

## Decisions and assumptions

- Keep changes focused and preserve user changes.
- Do not expose secrets or weaken the isolation boundary.
- Text input, bounded owner-requested output images, plain Codex behavior, ephemeral conversations, coarse progress, and manual deployment are intentional for pass 1; incoming media remains deferred.
- Codex uses the signed-in ChatGPT subscription and included limits, not API billing. `CODEX_HOME` persists only its authentication and configuration.
- Codex may edit the selected workspace automatically. Only exact public HTTPS port-443 network access can prompt the Telegram owner; all other escalation is denied.
- The statements below describe the dormant Responses implementation rather than the default backend.
- The OpenAI model is hosted remotely and is called through `OPENAI_API_KEY`; the local bot program receives tool requests and executes them in its allowed container workspace.
- Conversation sessions are in memory and are lost when the bot restarts; direct OpenAI usage metadata persists outside Git at `/workspace/.personal-agent-state/usage.sqlite3` by default.
- The conversational agent uses the mounted workspace as its default `computer` context; `/project` can narrow it to a subdirectory.
- Direct OpenAI remains the default provider; OpenRouter is a possible compatibility/fallback backend, not yet implemented.
- Approval is exact-action, single-pending-request, bound to its Telegram prompt for reactions, and expires after five minutes; text commands remain available.
- Cost estimates use standard OpenAI short-context prices as of 2026-07-27; provider billing remains authoritative, and custom model prices are reported as unknown.
- The high-usage threshold is advisory: it records one warning per expensive turn but does not stop an active task.
- Image turns accept one photo or supported image document up to 10 MiB by default, use high-detail vision, and discard image bytes after the current turn.
- Audio turns accept Telegram voice notes, audio attachments, and supported audio documents up to 20 MB and a reported 10-minute duration by default. They use `gpt-4o-mini-transcribe`, treat an optional caption as the instruction over the transcript, retain only text in session history, and report transcription cost as unknown until a verified price is added.
- Trace visibility does not expand execution authority. Provider-hidden controls, unreturned raw reasoning, unreturned hosted-search internals, raw media, `.env` contents, and credential values remain unavailable or protected.

## Validation

- Current Git-publication validation: 188 tests pass and four host-boundary cases skip via `python -m pytest`; `git diff --check` and the base-plus-publication Compose configuration pass. The updated `personal-agent-bot:latest` image builds as `sha256:6980ead49256f5e67291a2fc4d0e8e45f6cbbe90f76ac0475f2b8d63cba062c1`.
- A built-container adversarial test configures a project-controlled Git clean filter that fails if it can read the dummy deploy key. Source inspection and bundle creation succeed inside the production seccomp/Bubblewrap boundary, proving that the key path is masked; the bundle clones to the exact original commit. A separate disposable test imports a real bundle into a fresh bare repository and pushes the exact commit to a local bare remote.
- Production rollout validation: `main` and `origin/main` both reached `5a9194c`; the bot was recreated on image `sha256:6980ead49256f5e67291a2fc4d0e8e45f6cbbe90f76ac0475f2b8d63cba062c1`, became healthy with zero restarts, retained its persistent mounts, logged a clean Telegram application startup, and returned the expected safe unconfigured response from the live Git-publication gateway.
- All 178 Python tests pass; two host-local socket cases skip because the outer sandbox rejects Unix binds. API-Football coverage includes fixed-host authentication injection, proxy independence, secret redaction, endpoint/parameter validation, traversal and URL rejection, response bounds, timeouts, upstream errors, atomic concurrent quota updates, missing-key behavior, socket cleanup, MCP discovery/calls/errors, and startup failure cleanup. Outgoing Codex image coverage includes marker extraction, project containment, real-format validation, photo delivery, and document fallback. The real socket protocol and app-server/MCP boundary pass inside disposable built-image tests.
- Current validation: 178 tests pass and two skip via `python -m pytest`; `git diff --check` and Compose validation pass. The updated `personal-agent-bot:latest` image builds successfully.
- The production API-Football status request succeeds, returned JSON contains no credential, and the sanitized Codex/MCP environment does not inherit `API_FOOTBALL_KEY`. The Argentine Primera División MCP query completes without network approval and reports the plan-specific standings limitation. Three of 100 requests were consumed on 2026-08-01 UTC; the owner-only gateway socket remains mode `0600`.
- The bot image builds with pinned `openai-codex==0.144.4`, system Bubblewrap, and `uv==0.11.32`. Docker accepts the custom seccomp profile and successfully creates the inner Bubblewrap sandbox.
- Container smoke tests verify workspace write access and deny inherited Telegram secrets, `/codex-home` authentication reads, `/trace-state` reads, outside-workspace writes, direct shell networking, and Docker access.
- Commit `f722e0e` is published on `origin/main`. The production bot was recreated with image `sha256:6abe0410772b6b4f6330aeda28eca1595e049c4da07cfd12e31ee3ea2eda33de`, retained the original `personal-agent_codex-state` volume, reached healthy state, and logged a clean Telegram application startup.
- Live ChatGPT/Telegram approval-button behavior, authentication restart persistence, and credential-backed Git-push denial remain to be exercised through Telegram.
- Both the bot and deployer images build successfully with the tracing changes, their Python modules import inside the built images, and the recreated services report healthy/running after deployment.
- The audio- and image-capable bot image builds successfully; its production audio handler registers and synchronous model calls can be offloaded from the event loop. Neither media path has yet been exercised against live Telegram or OpenAI services.
- The durable-usage bot image built successfully and the replacement bot reached Docker health; a live post-deployment `/usage` exchange and model call have not yet been exercised.
- Earlier live deployment validation covered a queued deployment and controlled startup-failure rollback; the bot and deployer containers were healthy at that stopping point.
- The reaction change was pushed to `origin/main`; the bot image built successfully, the replacement bot reached Docker health, and the persistent deployer remained running. A live Telegram reaction exchange and model call have not been exercised.
