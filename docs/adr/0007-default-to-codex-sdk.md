# ADR 0007: Default Telegram conversation to the Codex SDK

- Status: Accepted
- Date: 2026-07-30
- Amended by: ADR 0009

## Context

The Responses implementation duplicated agent behavior, model routing, computer tools, approvals, and telemetry in application code. Pass 1 needs the standard Codex agent experience backed by a signed-in ChatGPT subscription while reducing the bot's host authority.

## Decision

Use pinned `openai-codex==0.144.4` as the default `AGENT_BACKEND=codex`. Keep one application-scoped `AsyncCodex` client and one ephemeral thread per Telegram session. Threads use the selected project as `cwd`, workspace-write sandboxing, and deny-all approvals, with no model, personality, or instruction overrides. Telegram exposes text, `/project`, `/new`, `/stop`, and `/help`; media and legacy operational commands are unavailable.

Persist only Codex authentication and configuration in a private `CODEX_HOME` volume. Do not give the bot a Docker socket, SSH key, Git push credential, or OpenAI API key. Keep the Responses implementation dormant behind `AGENT_BACKEND=responses` for manual rollback, and keep deployment host-initiated.

## Consequences

Codex may modify files inside the selected workspace without per-edit Telegram approval, while attempted escalation fails. Conversations disappear on restart but ChatGPT authentication survives. Pass 1 has no media, durable thread mapping, direct-API usage commands, trace commands, `/run`, or self-deployment surface. The beta SDK is version-pinned and must be deliberately reviewed before upgrades.
