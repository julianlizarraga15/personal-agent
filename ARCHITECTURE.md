# Personal Agent

## Goal

An autonomous coding agent controlled through Telegram.

## Worker responsibilities

- Receive a text task, validated image turn, or transcribed audio turn
- Clone or open a project repository
- Inspect existing code
- Run a coding agent
- Execute tests
- Commit and push changes
- Return a summary and generated files

## Current architecture

Telegram long polling → text, validated image, or transcribed audio input → in-memory user session → cost-aware nano router → small answer or Luna/Terra/Sol Responses API → selected web/computer tools

The session stores the optional legacy worker project, conversational computer context, compactable model input items, six short messages of router context, usage totals, and at most one pending approval. Ordinary messages can use constrained computer tools in the explicitly-mounted workspace. `/project` narrows that context to a directory below the workspace, `/new` starts over in the workspace root, `/usage` reports the current session's tokens and estimated cost, and `/stop` forgets the session. The legacy worker remains available for the one-shot `/run` flow and future stronger isolation.

Telegram photos and supported image documents are downloaded into memory, checked by declared and actual byte size, decoded to verify their real format, and passed to the Responses API as Base64 image input at high detail. The router sees only the caption and an attachment marker and cannot directly answer an image turn. Image data remains available across tool calls in that turn, then is replaced by a text marker so it is not replayed in later turns.

Telegram voice notes, audio attachments, and supported audio documents follow the same authorization, busy-session, declared-size, and actual-size admission checks. File signatures are mapped to safe upload names without trusting Telegram MIME metadata, and reported duration is capped before download. Validated bytes are sent in memory to the OpenAI transcription endpoint off the event loop. A caption becomes an instruction over the transcript; otherwise the transcript is the message. Only that text enters the router and conversation history, and the audio bytes are discarded before the agent turn continues.

The hosted web-search tool supplies current external information and source citations. File writes and dedicated Git commit/push tools call back to Telegram for exact-action approval. The delivered approval prompt's chat and message IDs are bound to the pending request, so a newly added 👍 or 👎 reaction on that exact message can resolve it; `/approve <id>` and `/reject <id>` remain available. The model tool loop waits on a thread-safe event, and unanswered requests expire after five minutes.

The optional GPT-5 nano router receives the latest message and limited recent conversation metadata. It directly answers only clearly simple requests with high confidence. It sends ordinary reasoning and web work to Luna, routine repository work to Terra, and difficult, high-stakes, ambiguous, failed-routing, or deployment work to Sol. Its capability result limits each main-model request to the required web and/or computer tools. Routing is a latency and cost optimization, not a security boundary; all path constraints and approval controls remain in the main agent.

Main-model calls set explicit reasoning effort, response verbosity, and output limits by tier. Server-side compaction replaces older replay items after 32,000 rendered tokens by default. File listings, file reads, and command output are bounded, and exact-fragment edits avoid full-file generation when possible. Each response records input, cached input, cache writes, output, reasoning, and web-search use; a high-usage turn produces an operational warning but is not interrupted.

When the selected project is the configured self-repository, the agent also has a dedicated self-deployment tool. It runs tests, publishes `main`, and atomically queues the published commit under the host-persistent state directory. A separate deployer service holds rollback authority and survives bot replacement; it builds only the bot image, resolves the Compose service container dynamically, waits through a startup stability window, and restores the prior image when verification fails. The replacement or rolled-back bot reports the durable outcome to the owner before marking it healthy. Deployment-controller upgrades are intentionally host-managed and are not part of normal bot self-deployment.

## Principles

- Project code lives in separate repositories
- Execution happens in containers
- The agent has broad permissions only inside an isolated Docker scope
- Every task should leave logs and Git history
