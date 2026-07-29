# ADR 0006: Retain owner-transparent execution traces

- Status: Accepted
- Date: 2026-07-29

## Context

Content-free operational logs are appropriate for routine container monitoring, but they do not let the authorized owner inspect the application prompts, routing decisions, model payloads, tool results, approvals, or deployment execution behind a Telegram turn. Provider APIs also do not expose every internal detail: raw chain-of-thought and provider-hidden controls are unavailable.

## Decision

Create one ordered, versioned trace for every conversational, media, legacy-worker, and deployment turn. Store traces in an owner-only SQLite database outside Git for seven days by default. Record exact application-observed prompts, settings, inputs, API output items, returned reasoning summaries, usage, complete redacted tool/command results, approvals, errors, timing, truncation, and compaction. Keep bounded tool results in model context while retaining the complete redacted result in the trace.

Request automatic reasoning summaries from compatible main models and retry without the option when a provider rejects it. Show concise live Telegram progress with the matching turn ID. Give only the configured Telegram owner `/prompt`, `/traces`, and `/trace` exports, splitting oversized gzip traces without dropping bytes or events.

Recursively redact credentials and token patterns. Deny `.env` access. Represent raw image/audio with media type, size, and SHA-256 rather than duplicating bytes. Keep detailed content out of stdout logs.

## Consequences

The owner can inspect partial or completed application-observable execution across restarts and deployment replacement. The persistent state directory now contains sensitive conversations and source/tool output and requires stronger access control and timely expiration. Redaction can intentionally remove values needed for debugging. Provider-hidden controls, unreturned raw reasoning, unreturned hosted-search internals, deleted media, and credential values remain unavailable; the UI must describe those limits accurately. Transparency does not expand execution authority.
