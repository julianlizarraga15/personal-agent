# ADR 0010: Keep API-Football credentials behind a local gateway

- Status: Accepted
- Date: 2026-07-31

## Context

Codex needs current football analytics, but putting an API-Sports key in its environment, workspace, command arguments, or network approval flow would make the credential readable by model-directed code. Direct public access would also let that code redirect requests or exhaust endpoints outside the intended analytics scope. API-Football accounts have upstream request limits, so local use needs an independent hard ceiling.

## Decision

Run an asynchronous gateway inside the existing bot process and expose only `/run/api-football.sock` through the Codex permission profile. Keep public domains unallowlisted. The gateway owns `API_FOOTBALL_KEY`, accepts only bounded GET requests for enumerated analytics endpoints and parameters, fixes the TLS origin to `v3.football.api-sports.io`, injects `x-apisports-key`, and ignores proxy environment variables. It rejects full URLs, traversal, unknown endpoints, odds, bookmakers, and predictions.

Increment an atomic persistent counter immediately before every upstream attempt and allow no more than 100 attempts per UTC day. Invalid local requests do not count. Do not cache responses in the first version. Recursively remove the credential from upstream JSON and return stable errors without exception details. Missing configuration is a healthy degraded mode; failure to create an owner-only socket when configured fails bot startup.

## Consequences

Codex can consume approved football data and the shared daily allowance but cannot retrieve the credential or choose another upstream host. The local socket is still a privileged data capability: malicious workspace code can spend the entire daily allowance and read returned analytics. The hardcoded endpoint and parameter sets require maintenance when API-Football changes. The quota file and gateway socket depend on the protected bot runtime and `trace-state` volume remaining writable.
