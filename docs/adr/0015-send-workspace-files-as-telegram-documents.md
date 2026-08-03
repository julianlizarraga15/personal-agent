# ADR 0015: Send safe workspace files as Telegram documents

- Status: Accepted
- Date: 2026-08-03
- Amends: ADR 0014

## Context

Codex can already request up to four verified workspace images for Telegram photo-style delivery. The owner also needs original PNGs and arbitrary project artifacts such as PDFs, archives, source, and binary files. Creating an archive merely to bypass the image-only contract changes the requested artifact and adds unnecessary work. ADR 0014 explicitly left outbound arbitrary-file delivery out of scope.

## Decision

Add a distinct `[[telegram_file:path]]` final-response marker alongside `[[telegram_image:path]]`. The image marker continues to mean photo-style delivery with format validation and document fallback. The file marker means document-only delivery even for PNG and JPEG content. Strip all markers from visible reply text and admit no more than four combined attachments per turn.

Resolve each candidate beneath the active project and require a regular file. Reject missing, outside-project, protected `.env`, recognizable credential/private-key, private-key-content, unreadable, and oversized candidates. Preserve the original leaf filename and bytes, send files individually with Telegram `sendDocument`, and never create an archive solely for transport. Set the outbound document default to 50,000,000 bytes through `TELEGRAM_MAX_OUTPUT_DOCUMENT_BYTES`, matching the hosted Telegram Bot API upload limit. Return and log only stable content-free failures.

## Consequences

An explicit owner request can return any safe bounded project artifact without changing its representation. Outbound document delivery is not a general data-export channel: it is model-marker-driven, project-confined, credential-screened, owner-only, and shares the existing four-attachment turn bound. The bot does not infer document intent directly from Telegram wording, batch files into a media group, archive them, or inspect their format beyond the credential safeguards.
