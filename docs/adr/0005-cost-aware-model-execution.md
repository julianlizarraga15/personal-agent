# ADR 0005: Bound conversational model cost

- Status: Accepted
- Date: 2026-07-27

## Context

The original router separated cheap simple answers from a medium or large model, but every non-simple turn still replayed an unbounded conversation and exposed every available tool. File and command results could make repeated tool-loop requests progressively more expensive, and the bot did not report token or estimated cost data.

## Decision

Use four cost roles: GPT-5 nano for routing and high-confidence simple answers, GPT-5.6 Luna for ordinary reasoning and web questions, Terra for routine computer work, and Sol for difficult, high-stakes, ambiguous, or deployment work. The router also selects the minimum web/computer capability set, while path and approval enforcement remains in application code.

Set explicit reasoning effort, verbosity, and output limits for main-model tiers. Enable Responses API server-side compaction at a configurable threshold and discard replay items superseded by the latest compaction item. Bound file listings, file ranges, and command output, and provide exact-fragment editing to avoid unnecessary full-file generation.

Record per-response token, cache, reasoning, and web-search usage. Expose session totals and a dated standard-price estimate through `/usage`; high-usage turns warn but continue. Observe automatic prompt caching without enabling explicit cache writes until measured traffic shows a net saving.

## Consequences

Routine Telegram use should consume fewer input, output, and tool-schema tokens. Routing mistakes can affect quality or omit an initially useful capability, so malformed and low-confidence decisions fall back to Sol with both capabilities. Compaction and bounded reads may require follow-up reads, and estimated cost can differ from the provider invoice or be unavailable for custom models.
