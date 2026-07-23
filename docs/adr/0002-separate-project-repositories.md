# ADR 0002: Keep project repositories separate from the agent repository

- Status: Accepted
- Date: 2026-07-23

## Context

The agent's own orchestration code needs a stable home, while task targets may have unrelated histories, dependencies, and release processes. Mixing them would make permissions, cleanup, and publishing ambiguous.

## Decision

The agent repository stores orchestration code and configuration only. Each target project is cloned into its own temporary worker checkout and changes are pushed to a new `codex/*` branch in that project's remote repository.

## Consequences

Tasks remain isolated and project history is preserved in the project repository. The worker needs credentials with narrowly-scoped push access, and operators must track allowed targets separately through `projects.yaml` or an equivalent deployment policy.
