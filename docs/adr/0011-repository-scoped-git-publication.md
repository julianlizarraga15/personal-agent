# ADR 0011: Publish one repository through an approval-gated credential boundary

- Status: Accepted
- Date: 2026-08-01

## Context

The Codex bot can edit and commit projects in its writable workspace, but intentionally has no Git credential. Giving the command sandbox an SSH key or credential helper would let model-directed repository code read or misuse that credential. The owner wants Codex to publish `mental-models` while retaining the existing isolation boundary.

## Decision

Add a required keyless `git-publish` stdio MCP tool backed by an owner-only Unix-socket gateway in the Telegram process. The MCP tool accepts only an exact 40-character local commit ID. The gateway fixes one configured workspace checkout, one GitHub SSH remote, and one branch; it rejects external `.git` directories, URL/include rewrites, object alternates, dirty worktrees, secrets inside the workspace, non-private deploy-key permissions, and a local `HEAD` different from the requested commit.

Before pushing, the gateway presents the repository, branch, and exact commit to the configured Telegram owner. Approval is memory-only, single-use, and expires after five minutes. Project-controlled Git configuration cannot share a process authority boundary with the credential: source inspection and bounded bundle creation run in a networkless Bubblewrap with the project read-only and the secrets, authentication, trace, and gateway-socket paths masked. After approval and a second clean-tree/`HEAD` check, the gateway clones the bundle into a fresh bare repository. Only that new repository receives the strict known-host/deploy-key environment for a non-force push of the verified exact commit, with hooks and inherited system/global configuration disabled.

Mount a directory containing only the repository-scoped write-enabled deploy key and verified GitHub `known_hosts` file into `/git-publish-secrets` through a local Compose override. Do not mount the key into the workspace or inherit its path through the sanitized Codex launcher. Missing configuration is a healthy degraded mode.

## Consequences

Codex can publish only the configured repository and branch, and every attempted publication requires the owner to approve the displayed commit. Project commands and project-controlled Git filters cannot read the deploy key, choose another remote, force-push, or reuse approval. Bundle size and Git output are bounded. The bot still holds narrowly scoped Git publication authority, so the deploy key must be restricted to this repository, kept outside the workspace with owner-only key permissions, and revoked if the host or bot process is compromised.
