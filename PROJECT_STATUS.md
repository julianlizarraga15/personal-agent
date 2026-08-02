# Project status

Last updated: 2026-08-02

## Current state

- The default Telegram backend uses pinned `openai-codex==0.144.4`, ephemeral in-memory threads, a named Bubblewrap workspace permission profile, and a sanitized launcher. The bot has no Docker socket or OpenAI API key.
- Repository-scoped publication is configured for `/workspace/mental-models`, `git@github.com:julianlizarraga15/mental-models.git`, and `main`. Its dedicated deploy key and verified `known_hosts` live outside the workspace and are mounted read-only at `/git-publish-secrets`; Codex and project commands cannot read them.
- The Git-publish MCP adapter is keyless. Codex auto-approves only invocation of `git-publish.publish` through the per-tool `approval_mode="approve"` setting, avoiding a duplicate generic MCP write-tool elicitation. The bot-side gateway remains the sole publication approval boundary and requires a newly delivered owner-only **Publish once** decision for the displayed repository, branch, and exact clean commit.
- The gateway rechecks the worktree after approval, creates a bounded bundle in a credential-free networkless Bubblewrap, imports it into a fresh bare repository, and performs an exact non-force GitHub SSH push with hooks, inherited Git configuration, credential helpers, interactive authentication, and password authentication disabled.
- Approval cards are sent directly through the bot to the owner chat. Delivery, resolution, expiry, and MCP completion are logged without message contents or credentials.
- API-Football remains available through its separate keyless MCP/fixed-gateway boundary with a protected 100-attempt UTC daily counter. Project shell networking remains disabled.
- The optional Docker-authorized deployer remains disabled behind the `manual-deployer` profile; deployment is host-managed.

## Publication incident and resolution

- Initial Telegram retries reported rejection without showing an approval card. Two distinct causes were found: the card used the incoming message's reply helper, and Codex's generic MCP write-tool review declined the MCP invocation before the gateway ran.
- Commit `64d78ac` changed publication cards to direct bot delivery and added delivery/outcome logging. Commit `327dc33` required a fresh tool call for each publish/retry request and added MCP completion logging.
- Codex runtime logs then proved the remaining failure: `ResolveElicitation ... decision: Decline` occurred before the gateway callback. Commit `07e3353` added the narrow `mcp_servers.git-publish.tools.publish.approval_mode="approve"` override documented by the Codex configuration reference.
- A post-deployment standalone Codex probe invoked `git-publish.publish` and reached the live gateway, which returned the expected `Git publication is available only during an active owner turn.` response. That proves Codex no longer blocks the tool and the MCP-to-gateway path works.
- End-to-end acceptance is still pending: commit `ea523361df1f31c583b8f7bafb3fcd9a65e2b40c` has not yet been confirmed pushed to GitHub through a real Telegram **Publish once** approval after `07e3353`.

## Last stopping point

- `main` and `origin/main` are at `07e3353` before this documentation update.
- Production runs image `sha256:6a11fe3aeaf94b7ebfbb7032cff676ca8e7e4212bb6c2e887585c45711975794`; the bot is healthy with zero restarts and the Git-publish secrets mount is read-only.
- The full suite passes: 190 tests passed and four host-boundary tests skipped. The post-deployment Codex MCP probe reached the live gateway safely without an active approval and therefore could not push.

## Next steps

1. From Telegram, request publication of the existing `mental-models` commit.
2. Confirm that a separate card displays the fixed GitHub remote, `main`, and commit `ea523361df1f31c583b8f7bafb3fcd9a65e2b40c`.
3. Press **Publish once** and verify the card changes to approved, the MCP call succeeds, and GitHub `main` reaches that exact commit.
4. Record the successful end-to-end result here. If it fails, use the content-free bot approval/MCP logs and the Codex runtime log rather than inferring the failure layer from the model's prose.

## Assumptions and risks

- The per-tool Codex approval override is intentionally limited to `git-publish.publish`; broadening it to other MCP write tools would weaken independent review.
- The Telegram gateway approval is mandatory, memory-only, single-use, destination/commit-bound, and expires after five minutes.
- The bot process holds narrow publication authority. Restrict and revoke the repository deploy key if the host or bot process is compromised.
- Do not commit `.env`, private keys, tokens, `auth.json`, local Compose overrides, generated caches, or workspace project repositories.
