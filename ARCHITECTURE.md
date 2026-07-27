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

Telegram long polling → in-memory user session → conservative small-model router → small answer or OpenAI Responses API → web search and computer tools

The session stores the optional legacy worker project, the conversational computer context, model input items, and at most one pending approval. Ordinary messages use constrained computer tools in the explicitly-mounted workspace by default. `/project` narrows that context to a directory below the workspace, `/new` starts over in the workspace root, and `/stop` forgets the session. The legacy worker remains available for the one-shot `/run` flow and future stronger isolation.

The hosted web-search tool supplies current external information and source citations. File writes and dedicated Git commit/push tools call back to Telegram for exact-action approval. The model tool loop waits on a thread-safe event while `/approve <id>` or `/reject <id>` resolves the request; unanswered requests expire after five minutes.

The optional low-cost router receives the latest message and limited recent conversation metadata. It directly answers only clearly simple requests with high confidence. Coding, tool, web-search, contextual, ambiguous, or failed router requests use the configured large model. Routing is a latency and cost optimization, not a security boundary; all tool and approval controls remain in the main agent.

When the selected project is the configured self-repository, the agent also has a dedicated self-deployment tool. It runs tests, requests approval for the complete deployment, then invokes the fixed read-only Compose helper to rebuild and recreate the bot service. It sends a restart-in-progress notice after the push because recreating the bot terminates the process before it can send a final response. The helper preserves the prior bot image under a rollback tag.

## Principles

- Project code lives in separate repositories
- Execution happens in containers
- The agent has broad permissions only inside an isolated Docker scope
- Every task should leave logs and Git history
