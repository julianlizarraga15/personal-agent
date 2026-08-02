# ADR 0012: Enable Codex audio through a separate transcription boundary

- Status: Accepted
- Date: 2026-08-02
- Amended by: ADR 0013

## Context

ADR 0007 made the initial Codex Telegram surface text-only and intentionally withheld OpenAI API credentials from the bot and command sandbox. The dormant Responses path already validates Telegram audio safely. The owner wants voice notes and supported audio files to become text turns in the current ephemeral Codex conversation without weakening ChatGPT authentication, sandboxing, publication approval, or secret isolation.

## Decision

Reuse the existing audio signature validation, supported formats, 20 MB and ten-minute defaults, caption composition, and in-memory transcription request. Acquire the per-user Codex turn reservation before download and hold it through transcription and SDK execution. Session replacement invalidates that reservation; a late provider result is discarded before it can reach another thread. Incoming images remain unsupported.

Use `gpt-4o-mini-transcribe` by default through a direct OpenAI Platform request. Store its dedicated key in an `api-key` file inside a private host directory mounted read-only at `/openai-transcription-secrets` by an ignored local Compose override. Do not use an environment key. Deny the mount in the Codex filesystem profile and exclude transcription settings from the launcher environment. Keep raw audio in memory only, submit only transcript text to Codex, and omit audio bytes, transcript text, key material, and raw provider errors from operational logs. Missing key configuration is a healthy degraded mode.

## Consequences

Voice/audio input now works in Codex mode while image input and legacy operational commands remain unavailable. Audio transcription is independently billed OpenAI Platform usage rather than ChatGPT subscription usage and must be monitored with its dedicated key. The bot process can use that narrow key, so host/container compromise can consume or revoke its Platform authority; the key must remain separate from API-Football and Git publication credentials. Transcription cannot be forcibly stopped once the synchronous provider call is running in a worker thread, but its result cannot cross a `/stop`, `/new`, or `/project` boundary.
