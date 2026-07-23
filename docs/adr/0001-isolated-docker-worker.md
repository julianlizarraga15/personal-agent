# ADR 0001: Run coding tasks in isolated Docker workers

- Status: Accepted
- Date: 2026-07-23

## Context

The coding agent may inspect files, execute tests, and change Git history. Running those operations directly on the bot host would increase the impact of a faulty task, repository build step, or compromised dependency.

## Decision

Each task runs in a short-lived Docker worker with a temporary repository checkout. The worker performs the agent run, project tests, commit, and push, then the checkout is discarded.

## Consequences

This limits filesystem persistence and makes task boundaries clear, but it does not make arbitrary code safe: the worker still needs a carefully-controlled runtime and Docker itself remains a privileged boundary. The bot deployment must therefore be on a trusted host, and stronger isolation may be required for hostile repositories.
