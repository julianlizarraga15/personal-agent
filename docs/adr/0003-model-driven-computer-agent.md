# ADR 0003: Make the model the conversational agent

- Status: Accepted
- Date: 2026-07-23

## Context

The original Telegram interface treated each message as a one-shot coding job and delegated the entire job to an assumed Codex CLI inside a worker image. That did not provide a general conversation, did not expose the user's computer as tools, and did not actually configure the model runtime.

## Decision

The bot uses the OpenAI Responses API as the conversational model and enables its hosted web-search tool for current external information. It also exposes a small, explicit computer tool surface: list files, read files, write files, run non-destructive commands, inspect Git, and approved Git commit/push operations. A per-user in-memory session stores the conversational computer context, model input items, and one pending approval. The mounted workspace is the default computer context, and `/project` may narrow it to a directory below that workspace. The legacy worker's project selection remains separate for the `/run` compatibility path.

The legacy Docker worker remains available for the `/run` compatibility path. Future work may move the computer tool implementation into a persistent per-project sandbox container without changing the Telegram or model interfaces.

## Consequences

The bot needs an `OPENAI_API_KEY`, a controlled workspace mount, and an OpenAI model/account that supports web search. Search incurs provider tool usage/cost and returns external content that must be treated as untrusted data. Conversation state is currently lost on restart. Consequential actions now require an exact Telegram approval, but tool execution is still powerful even with command filters, so the deployment host and workspace must be trusted; persistent sandboxing remains follow-up work.
