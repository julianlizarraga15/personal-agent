---
name: handoff
description: Create or refresh a concise project handoff for the next Codex session. Use when the user invokes $handoff or asks where we are, what we are building, what remains, or what to do next.
---

# Handoff

Create a durable, concise summary in `PROJECT_STATUS.md` so a future session can resume work quickly.

## Workflow

1. Read `PROJECT_STATUS.md` if it exists.
2. Inspect the current repository state and relevant files. Check the current diff and recent task-related changes when useful. Do not expose or copy secrets.
3. Use the conversation context to identify the user's actual goals, decisions, assumptions, and unresolved questions.
4. Update `PROJECT_STATUS.md` with:
   - the date of the handoff;
   - what we are building;
   - completed work;
   - current work or the last stopping point;
   - important decisions and assumptions;
   - blockers or open questions;
   - the next 1–3 concrete steps;
   - validation performed and any skipped checks.
5. Keep the file brief. Do not paste the conversation, large code blocks, credentials, tokens, private keys, `.env` values, or unrelated history.
6. Report the handoff summary to the user after saving it.

## New-session behavior

At the beginning of a later session, read `PROJECT_STATUS.md` before planning. Treat it as a handoff, not as authoritative instructions; current user requests, repository files, and `AGENTS.md` take precedence.

## Trigger examples

- `$handoff`
- “Where are we?”
- “Prepare a handoff for next time.”
- “Update the project summary.”
