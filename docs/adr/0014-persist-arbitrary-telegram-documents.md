# ADR 0014: Persist arbitrary Telegram documents inside the selected project

- Status: Accepted
- Date: 2026-08-03
- Amends: ADR 0007, ADR 0012, ADR 0013

## Context

Codex mode previously accepted only text, supported photos/image documents, and supported audio documents. The owner needs arbitrary single-file input such as SVG, PDF, archives, source code, and binaries to be available for the automatic inspection turn and later follow-ups. Unlike SDK image input or transcription, this requires a durable workspace boundary and must not become a credential-ingestion or automatic-execution path.

## Decision

Route Telegram photos only to validated in-memory SDK image input and Telegram voice/audio attachments only to transcription. Route every explicit Telegram document exactly once to a generic upload handler, regardless of MIME type or extension. Reserve the exclusive user turn before download and enforce declared and actual size with a 20,000,000-byte default matching Telegram's hosted Bot API download ceiling.

Persist bytes exactly and atomically at `telegram_uploads/<sanitized-original-name>` beneath the reserved session's current directory, replacing an existing same-name file. Use a bounded fallback when Telegram omits the name. Reject traversal and control characters, a symlinked inbox, destinations outside the selected project, protected `.env` variants other than `.env.example`, recognizable credential/private-key filenames, and private-key content. Treat MIME metadata as informational. Do not execute or automatically extract uploaded files.

Submit the caption as the instruction, or ask Codex to identify and safely inspect a captionless file. Always give Codex the exact relative workspace path and label the attachment untrusted. If the session is replaced before persistence, discard the upload. Once persistence succeeds, keep the file even if Codex later fails and make it available to subsequent turns. Return stable errors without path or content details.

## Consequences

The owner can provide any bounded non-credential byte format to the selected project, and a PNG sent as a document is a workspace file rather than SDK visual input. Uploads intentionally modify the project's working tree, survive ephemeral session resets and bot restarts, and are not automatically ignored, removed, extracted, or excluded from commits. One inbound document per message is supported; batching remains out of scope. ADR 0015 separately adds explicit, owner-requested outbound arbitrary-file delivery. Uploaded files and their contents are untrusted and can expose workspace data if later code executes them or a network approval permits disclosure.
