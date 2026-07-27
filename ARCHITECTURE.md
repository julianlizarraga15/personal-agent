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

The hosted web-search tool supplies current external information and source citations. File writes and dedicated Git commit/push tools call back to Telegram for exact-action approval. The delivered approval prompt's chat and message IDs are bound to the pending request, so a newly added 👍 or 👎 reaction on that exact message can resolve it; `/approve <id>` and `/reject <id>` remain available. The model tool loop waits on a thread-safe event, and unanswered requests expire after five minutes.

The optional low-cost router receives the latest message and limited recent conversation metadata. It directly answers only clearly simple requests with high confidence. Coding, tool, web-search, contextual, ambiguous, or failed router requests use the configured large model. Routing is a latency and cost optimization, not a security boundary; all tool and approval controls remain in the main agent.

When the selected project is the configured self-repository, the agent also has a dedicated self-deployment tool. It runs tests, publishes `main`, and atomically queues the published commit under the host-persistent state directory. A separate deployer service holds rollback authority and survives bot replacement; it builds only the bot image, resolves the Compose service container dynamically, waits through a startup stability window, and restores the prior image when verification fails. The replacement or rolled-back bot reports the durable outcome to the owner before marking it healthy. Deployment-controller upgrades are intentionally host-managed and are not part of normal bot self-deployment.

## Principles

- Project code lives in separate repositories
- Execution happens in containers
- The agent has broad permissions only inside an isolated Docker scope
- Every task should leave logs and Git history
