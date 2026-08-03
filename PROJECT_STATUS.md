# Project status

Last updated: 2026-08-03

## Current state

- Live acceptance disproved the generic exact-host download design in ADR 0016. Pinned Codex 0.144.4 either blocks networking before approval, grants broad profile networking without the proxy, or applies managed-proxy domains as hard policy. Generic command networking remains disabled. ADR 0017 instead adds `download_team_logo(team_id)` to the fixed API-Football gateway: only the official media host/path and fixed `assets/team-crests/<id>.png` destination are possible. The neutral `Response sent.` activity label remains.
- The default Telegram backend uses pinned `openai-codex==0.144.4`, ephemeral in-memory threads, a named Bubblewrap workspace permission profile, and a sanitized launcher. Telegram photos remain validated in-memory Codex SDK image inputs, and voice/audio attachments remain validated transcription inputs. Any explicit Telegram document is atomically persisted byte-for-byte under `telegram_uploads/` in the selected project and submitted to Codex by its untrusted relative workspace path. Upload admission is turn-reserved, capped at 20,000,000 bytes by default, and rejects traversal, symlinked inboxes, protected `.env` variants, recognizable credential/private-key files, and private-key content.
- Codex final responses now distinguish photo-style `telegram_image` markers from document-only `telegram_file` markers. Both marker types are removed from visible text and share a four-attachment turn cap. Requested documents preserve original filenames and bytes, are limited to 50,000,000 bytes by default, remain confined to regular files beneath the selected project, and reject protected environment or credential/private-key material.
- Repository-scoped publication is configured for `/workspace/mental-models`, `git@github.com:julianlizarraga15/mental-models.git`, and `main`. Its dedicated deploy key and verified `known_hosts` live outside the workspace and are mounted read-only at `/git-publish-secrets`; Codex and project commands cannot read them.
- The Git-publish MCP adapter is keyless. Codex auto-approves only invocation of `git-publish.publish` through the per-tool `approval_mode="approve"` setting, avoiding a duplicate generic MCP write-tool elicitation. The bot-side gateway remains the sole publication approval boundary and requires a newly delivered owner-only **Publish once** decision for the displayed repository, branch, and exact clean commit.
- The gateway rechecks the worktree after approval, creates a bounded bundle in a credential-free networkless Bubblewrap, imports it into a fresh bare repository, and performs an exact non-force GitHub SSH push with hooks, inherited Git configuration, credential helpers, interactive authentication, and password authentication disabled.
- Approval cards are sent directly through the bot to the owner chat. Delivery, resolution, expiry, and MCP completion are logged without message contents or credentials.
- API-Football analytics and official team-logo downloads are available through the separate keyless MCP/fixed-gateway boundary with a shared protected 100-attempt UTC daily counter. Project shell networking remains disabled.
- The optional Docker-authorized deployer remains disabled behind the `manual-deployer` profile; deployment is host-managed.

## Publication incident and resolution

- Initial Telegram retries reported rejection without showing an approval card. Two distinct causes were found: the card used the incoming message's reply helper, and Codex's generic MCP write-tool review declined the MCP invocation before the gateway ran.
- Commit `64d78ac` changed publication cards to direct bot delivery and added delivery/outcome logging. Commit `327dc33` required a fresh tool call for each publish/retry request and added MCP completion logging.
- Codex runtime logs then proved the remaining failure: `ResolveElicitation ... decision: Decline` occurred before the gateway callback. Commit `07e3353` added the narrow `mcp_servers.git-publish.tools.publish.approval_mode="approve"` override documented by the Codex configuration reference.
- A post-deployment standalone Codex probe invoked `git-publish.publish` and reached the live gateway, which returned the expected `Git publication is available only during an active owner turn.` response. That proves Codex no longer blocks the tool and the MCP-to-gateway path works.
- End-to-end acceptance is still pending: commit `ea523361df1f31c583b8f7bafb3fcd9a65e2b40c` has not yet been confirmed pushed to GitHub through a real Telegram **Publish once** approval after `07e3353`.

## Last stopping point

- The earlier `curl` fix was published as `1107c09` and deployed, but its live retry failed. The replacement fixed team-logo gateway, active-project binding, MCP tool, tests, documentation, and ADR 0017 are now implemented locally. The focused suite passes with 122 tests and three host-boundary skips; the complete suite passes with 222 tests and five host-boundary skips. A disposable image build succeeded, and its real fixed-host probe downloaded team 435 as a 90,381-byte valid PNG. Publication, redeployment, and a real Telegram tool turn are pending.
- Outbound safe workspace document delivery, tests, configuration, help text, README/architecture/security/runbook guidance, and ADR 0015 are implemented locally. The focused Codex/Telegram suite passes with 100 tests; the complete suite passes with 215 tests and four host-boundary skips. Publication and deployment have not been requested.
- Arbitrary inbound Codex document support, focused tests, configuration, help text, operational guidance, README/architecture/security documentation, and ADR 0014 are implemented and deployed on `main`. The focused Telegram/Codex suite passes with 90 tests; the complete suite passes with 205 tests and four host-boundary skips.
- Incoming Codex image support, tests, documentation, and ADR 0013 were committed as `3307321`, published directly to `main`, and deployed. The full local suite passes: 201 tests passed and four host-boundary tests skipped.
- The earlier Codex audio implementation, tests, configuration example, and documentation are committed and published directly to `main`.
- The live `personal-agent-bot-1` runs image `sha256:e4e8994b6e64fbb35c3e26cd21ba0a7839128c5d40cf28cc06cd5e536e966ba0`; it is healthy with zero restarts, uses the existing Codex authentication volume, and has `/git-publish-secrets` and `/openai-transcription-secrets` mounted read-only. A briefly created directory-named Compose project was removed before the correct `personal-agent` service was recreated.
- The previously validated combined base, Git-publication example, and transcription example Compose configuration remains unchanged by the image-input work.

## Next steps

1. Run focused and complete tests, publish and deploy the fixed team-logo gateway, then retry all eight crests from Telegram and verify the saved PNGs plus regenerated charts.
2. Verify captioned and captionless SVG/PDF documents end to end, confirm placement in the selected project's `telegram_uploads/`, and confirm a later text turn can refer to the persisted file.
3. Verify one captionless and one captioned Telegram photo end to end.
4. Verify one captionless and one captioned Telegram voice note end to end, then confirm transcription activity in the OpenAI dashboard. Missing transcription configuration remains healthy degraded mode.
5. From Telegram, request publication of the existing `mental-models` commit, confirm the fixed remote/branch/commit card, approve once, and verify GitHub `main` reaches `ea523361df1f31c583b8f7bafb3fcd9a65e2b40c`.
6. Record the end-to-end results here. If any fail, use content-free bot logs and the Codex runtime log rather than inferring the failure layer from model prose.

## Assumptions and risks

- The per-tool Codex approval override is intentionally limited to `git-publish.publish`; broadening it to other MCP write tools would weaken independent review.
- The Telegram gateway approval is mandatory, memory-only, single-use, destination/commit-bound, and expires after five minutes.
- The bot process holds narrow publication authority. Restrict and revoke the repository deploy key if the host or bot process is compromised.
- The optional transcription call is separately billed and cannot be interrupted once its synchronous worker-thread request has started; session invalidation prevents the result from being submitted to a replacement Codex thread.
- Incoming image bytes are sent to the Codex service as model input and remain available only in the ephemeral Codex thread until `/new`, `/project`, `/stop`, process exit, or bot replacement discards it. The deployed implementation has not yet been exercised with a real Telegram image against the live signed-in Codex service.
- Incoming documents intentionally change the selected project's working tree and persist across session resets and bot replacement. They are untrusted, are not automatically ignored/extracted/deleted, and can enter a later commit unless reviewed. Arbitrary format support does not permit credentials or remove the 20,000,000-byte default limit.
- Do not commit `.env`, private keys, tokens, `auth.json`, local Compose overrides, generated caches, or workspace project repositories.
