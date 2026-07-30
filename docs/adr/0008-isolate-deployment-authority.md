# ADR 0008: Isolate deployment authority from the agent workspace

- Status: Accepted
- Date: 2026-07-30

## Context

Removing the Docker socket from the Codex bot did not fully remove deployment authority. The bot and deployer still shared `/workspace/.personal-agent-state`, so workspace-write access could forge a deployment request. Because the bot could also create a clean local commit, checking only local `HEAD` and worktree cleanliness allowed an unpublished commit to reach the Docker-privileged builder.

## Decision

Move deployment requests and status into a named volume mounted only into the deployer. Do not mount or expose that queue to the bot. Put the deployer behind an explicit `manual-deployer` Compose profile, disable automatic restart, and keep it stopped during normal Codex operation.

Before building, require the requested commit to equal the configured HTTPS remote branch as returned by `git ls-remote`, then also require the local checkout to be clean at that exact commit. Reject local paths, SSH remotes, malformed refs, unavailable remotes, and mismatches.

## Consequences

Codex workspace-write access cannot enqueue a privileged action. A forged local commit cannot pass the remote publication check. The old Responses self-deployment producer remains dormant code but is intentionally disconnected from the deployer. Host operators must deploy manually or explicitly start a trusted recovery workflow, and private repositories need an HTTPS authentication mechanism configured only for the deployer.
