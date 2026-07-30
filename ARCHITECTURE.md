# Personal Agent

## Goal

An autonomous coding agent controlled through Telegram.

## Worker responsibilities

- Receive a text task (Codex pass 1); the dormant Responses path also supports validated image and transcribed audio turns
- Clone or open a project repository
- Inspect existing code
- Run a coding agent
- Execute tests
- Commit and push changes
- Return a summary and generated files

## Current architecture

Telegram long polling → authorized text input → ephemeral in-memory Telegram session → official Codex Python SDK thread → Bubblewrap workspace permissions → optional exact-host HTTPS approval → debounced activity + rendered final answer

One application-scoped `AsyncCodex` process serves all authorized turns. Each Telegram user has at most one ephemeral thread and active turn. A thread starts with the selected project as `cwd` and a named permissions profile extending `:workspace`, without application overrides for model, personality, base instructions, or developer instructions. System Bubblewrap enforces filesystem and network isolation inside the bot container. A dedicated seccomp allowlist permits only the namespace and mount syscalls Bubblewrap needs; the container receives no added capability, privileged mode, or unconfined security setting.

The Codex app-server starts through a launcher that replaces, rather than extends, the bot environment. Commands inherit no Telegram token, OpenAI/Git credentials, or upstream proxy. The permissions profile denies `/codex-home`, `/trace-state`, and workspace `.env` files, while keeping the selected workspace writable. Network starts disabled. Only a blocked public HTTPS request on port 443 can reach the in-memory Telegram approval broker; every other approval category fails closed. An owner button grants that one destination for the current turn, expires after five minutes, and is cancelled by `/new`, `/project`, or `/stop`.

Thread mappings and approvals are never persisted. `CODEX_HOME` is a private volume used by the unsandboxed app-server for authentication and Codex configuration, but is unreadable to sandboxed project commands.

The bot container can write the mounted workspace and includes `uv`, but has no Docker socket, SSH key, OpenAI API key, Git push credential, or deployment-queue mount. The optional deployer is behind the `manual-deployer` Compose profile, has no restart policy, and stores its queue in a named volume mounted only into that service. Before any build, it resolves the configured HTTPS remote ref and requires the request, clean local `HEAD`, and published commit to match exactly. Codex-mode Telegram registration includes text, callback queries, `/project`, `/new`, `/stop`, and `/help`; media and legacy operational commands are unavailable. Deployment is a manual host operation in pass 1.

The previous Responses architecture below is dormant rollback code selected only with `AGENT_BACKEND=responses`.

The session stores the optional legacy worker project, conversational computer context, compactable model input items, six short messages of router context, current-session usage totals, the active trace handle, and at most one pending approval. Each direct OpenAI response also appends content-free usage metadata to a SQLite ledger in the host-persistent state directory. A separate owner-only SQLite trace database in a dedicated Docker volume stores ordered redacted events for seven days by default and is mounted only into bot and deployer. Ordinary messages can use constrained computer tools in the explicitly-mounted workspace. `/project` narrows that context to a directory below the workspace, `/new` starts over in the workspace root, `/usage` reports current-session, current UTC day, and lifetime recorded totals, `/prompt` exports live application instructions, `/traces` and `/trace` expose retained traces, and `/stop` forgets the session. Session changes never delete either durable store. The legacy worker remains available for the one-shot `/run` flow and future stronger isolation; its Codex CLI consumption is outside the usage ledger but its observable execution is traced.

Telegram photos and supported image documents are downloaded into memory, checked by declared and actual byte size, decoded to verify their real format, and passed to the Responses API as Base64 image input at high detail. The router sees only the caption and an attachment marker and cannot directly answer an image turn. Image data remains available across tool calls in that turn, then is replaced by a text marker so it is not replayed in later turns.

Telegram voice notes, audio attachments, and supported audio documents follow the same authorization, busy-session, declared-size, and actual-size admission checks. File signatures are mapped to safe upload names without trusting Telegram MIME metadata, and reported duration is capped before download. Validated bytes are sent in memory to the OpenAI transcription endpoint off the event loop. A caption becomes an instruction over the transcript; otherwise the transcript is the message. Only that text enters the router and conversation history, and the audio bytes are discarded before the agent turn continues.

The hosted web-search tool supplies current external information and source citations. File writes and dedicated Git commit/push tools call back to Telegram for exact-action approval. The delivered approval prompt's chat and message IDs are bound to the pending request, so a newly added 👍 or 👎 reaction on that exact message can resolve it; `/approve <id>` and `/reject <id>` remain available. The model tool loop waits on a thread-safe event, and unanswered requests expire after five minutes.

The optional GPT-5 nano router receives the latest message and limited recent conversation metadata. It directly answers only clearly simple requests with high confidence. It sends ordinary reasoning and web work to Luna, routine repository work to Terra, and difficult, high-stakes, ambiguous, failed-routing, or deployment work to Sol. Its capability result limits each main-model request to the required web and/or computer tools. Routing is a latency and cost optimization, not a security boundary; all path constraints and approval controls remain in the main agent.

Main-model calls set explicit reasoning effort, response verbosity, and output limits by tier and request `reasoning.summary=auto`. If the selected model rejects that option, the limitation is traced and the request is retried once without it. Server-side compaction replaces older replay items after 32,000 rendered tokens by default. File listings, file reads, and command output are bounded for model replay, while the full application-observed redacted values are written to the trace before bounding. Exact-fragment edits avoid full-file generation. Each router, main-model, and transcription response records input, cached input, cache writes, output, reasoning, and web-search use in memory and in the durable usage ledger. Trace events additionally retain exact application prompts/settings, API output items, reasoning summaries, tool arguments/results, approvals, errors, timing, truncation, and compaction. Both telemetry stores fail open so a storage fault does not discard a model reply, but the fault is logged. A high-usage turn produces an operational warning but is not interrupted.

In dormant Responses code, the selected self-repository still has a self-deployment tool, but Compose no longer connects its workspace queue to the deployer. The optional deployer can be started explicitly for host-managed recovery workflows; its queue is private and published-commit verification is mandatory. Normal pass-1 deployment remains host-managed.

Trace ordering is allocated transactionally per turn, so concurrent Telegram turns and the separate deployer process can safely append to the same trace. The queued deployment carries its originating turn ID across bot replacement. Exports include running turns and are owner-scoped by numeric Telegram ID. Large gzip streams are split without deleting events. Recursive redaction removes secret-shaped fields and credential/token patterns; raw media becomes type/size/SHA-256 metadata, and `.env` paths are denied at the computer boundary.

## Principles

- Project code lives in separate repositories
- Execution happens in containers
- The agent has broad permissions only inside an isolated Docker scope
- Every task should leave logs and Git history
