# Personal Agent

## Goal

An autonomous coding agent controlled through Telegram.

## Worker responsibilities

- Receive a text task
- Clone or open a project repository
- Inspect existing code
- Run a coding agent
- Execute tests
- Commit and push changes
- Return a summary and generated files

## Current architecture

Telegram long polling → in-memory user session → OpenAI Responses API → computer tools

The session stores the optional legacy worker project, the conversational computer context, model input items, and at most one pending approval. Ordinary messages use constrained computer tools in the explicitly-mounted workspace by default. `/project` narrows that context to a directory below the workspace, `/new` starts over in the workspace root, and `/stop` forgets the session. The legacy worker remains available for the one-shot `/run` flow and future stronger isolation.

File writes and dedicated Git commit/push tools call back to Telegram for exact-action approval. The model tool loop waits on a thread-safe event while `/approve <id>` or `/reject <id>` resolves the request; unanswered requests expire after five minutes.

## Principles

- Project code lives in separate repositories
- Execution happens in containers
- The agent has broad permissions only inside an isolated Docker scope
- Every task should leave logs and Git history
