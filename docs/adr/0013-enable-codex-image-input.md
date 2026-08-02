# ADR 0013: Enable incoming images in Codex mode

- Status: Accepted
- Date: 2026-08-02

## Context

The default Telegram backend supports text and transcribed audio, while its dormant Responses implementation already contains bounded image download and content validation. The pinned Codex SDK accepts a turn input list containing text and an image data URL. Incoming image support should reuse the established validation boundary without adding an OpenAI Platform key or placing user media in a project workspace.

## Decision

Register the existing photo and image-document filter with a Codex image handler. Reserve the same exclusive per-user turn before download, enforce declared and actual 10 MiB limits, and use Pillow to reject invalid, decompression-bomb, unsupported, or animated content. Accept verified JPEG, PNG, WEBP, and single-frame GIF data.

Pass the caption, or a safe default visual-inspection prompt, plus the verified image as an in-memory Base64 data URL through the pinned SDK's text and image input types. Do not write incoming media into the selected workspace, log its contents, add credentials, or relax the command sandbox. Keep image context in the ephemeral Codex thread so follow-up turns can refer to it; `/new`, `/project`, `/stop`, bot replacement, and process exit discard that thread.

## Consequences

The owner can send one supported image per Telegram message for visual analysis or image-guided coding work in the normal Codex conversation. Image payloads increase turn input size and are visible to the Codex service as model input. Albums, stickers, animated images, video, and PDFs remain unsupported. The pinned SDK image-input contract is a compatibility surface that must be revalidated before upgrades.
