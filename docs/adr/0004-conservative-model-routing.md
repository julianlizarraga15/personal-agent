# ADR 0004: Route simple conversation to a small model

- Status: Accepted
- Date: 2026-07-23

## Context

Most Telegram turns do not need repository tools or the full reasoning budget of the main agent. Calling the large model for every message increases cost and can add latency.

## Decision

Use a configurable small model as a conservative router. It may answer only clearly simple requests with at least 0.9 confidence. Requests involving tools, coding, current information, ambiguity, active context, malformed routing output, or router failure fall back to the existing large-model agent.

The router receives the latest message and a short, truncated conversational context, but no files, secrets, tools, or approval capabilities. Routing can be disabled with `OPENAI_ROUTER_ENABLED=0`.

## Consequences

Simple messages become cheaper and may complete faster. Each routed message has an additional model call, so savings depend on the proportion of simple requests. Conservative fallback can send some borderline requests to the large model, preserving behavior and safety at the expense of some savings.
