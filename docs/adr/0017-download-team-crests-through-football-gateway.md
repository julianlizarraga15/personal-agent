# ADR 0017: Download team crests through the football gateway

- Status: Accepted
- Date: 2026-08-03
- Supersedes: ADR 0016

## Context

The live retry after ADR 0016 still failed before Telegram approval. Reproduction against pinned Codex 0.144.4 established three distinct permission-profile behaviors: disabled networking fails at DNS before a managed-network approval exists; enabled networking without the proxy grants broad outbound access; and enabled networking with the proxy enforces configured domains as hard policy without surfacing an exact-host prompt through the existing application callback. Retaining `curl` could not provide both reliable downloads and the required least-privilege boundary.

API-Football already supplies team IDs and official crest URLs through a credential-safe fixed gateway. The crest URL format uses the separate official media host and does not require exposing the API credential.

## Decision

Extend the API-Football MCP server with `download_team_logo(team_id)`. The bot-side gateway accepts only one positive numeric team ID, constructs `https://media.api-sports.io/football/teams/<id>.png` itself, follows no redirects, verifies TLS, caps the response at 2 MiB, requires a PNG signature, and atomically saves it as `assets/team-crests/<id>.png` beneath the project bound to the active owner turn.

The model cannot choose the host, URL path, destination directory, or filename. The gateway rejects use outside an active turn and refuses symlinked asset directories. Logo attempts share the protected 100-attempt UTC daily API-Football counter. The MCP tool receives a narrow per-tool approval override because ordinary workspace writes are already permitted and the gateway itself fixes every external and filesystem target. Shell networking stays disabled.

Keep the neutral `Response sent.` activity label introduced by ADR 0016, but remove `curl` and the unverified literal-URL instruction from the bot image.

## Consequences

Official team crests can be downloaded without broad command networking, API credential exposure, or arbitrary URL fetching. The feature is deliberately specific to API-Football team PNGs; unrelated public assets remain unavailable. A live Telegram turn must confirm the MCP tool call, eight saved PNGs, regenerated charts, and requested Telegram delivery.

ADR 0018 subsequently adds a separate owner-approved gateway for unrelated credential-free public HTTPS files. The stricter fixed crest operation remains preferred for API-Football team logos.
