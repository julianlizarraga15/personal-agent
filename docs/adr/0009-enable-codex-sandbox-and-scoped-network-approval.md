# ADR 0009: Enable the Codex sandbox and scoped network approval

- Status: Accepted; network-approval portion superseded by ADR 0017
- Date: 2026-07-30

## Context

Codex's Linux command sandbox uses Bubblewrap namespaces. Docker's default seccomp profile blocked their creation, leaving ordinary workspace inspection stuck with `No permissions to create a new namespace`. Disabling the sandbox or granting `SYS_ADMIN`, privileged mode, unconfined seccomp, or host credentials would solve the symptom by weakening the intended boundary. Fully blocking network also prevents routine dependency installation such as `uv sync`.

## Decision

Install system Bubblewrap and pinned `uv` in the bot image. Run the bot with an x86-64 seccomp allowlist derived from Moby's default and add only the namespace and mount operations Bubblewrap requires. Do not add Linux capabilities or disable seccomp/AppArmor.

Launch the pinned Codex runtime through an environment-sanitizing wrapper. Use a named permissions profile extending `:workspace`, deny sandboxed access to `/codex-home`, `/trace-state`, and workspace `.env` files, and disable network by default.

Enable only Codex sandbox approval prompts that carry a concrete network context. The application accepts only public HTTPS hostnames on port 443, binds the prompt to the configured Telegram owner, grants the destination for the current turn only, expires it after five minutes, and cancels it when the session changes. Reject file changes, generic shell escalation, `request_permissions`, local/private targets, and every unknown approval method.

## Consequences

Codex can inspect, edit, test, and scaffold projects—including `uv` projects—inside the mounted workspace without the namespace failure. Dependency downloads require an explicit exact-host button. Approval can expose workspace-readable information to that host, so the owner must review the destination. Commands still cannot read bot/auth/trace credentials, use Docker, publish with Git credentials, or write outside the workspace. The seccomp profile and the private SDK approval hook are pinned compatibility surfaces that must be revalidated before upgrading Docker, Bubblewrap, or `openai-codex`.

## Amendment

Live testing recorded in ADR 0017 found that pinned Codex 0.144.4 does not emit the expected exact-host approval while using the named permission profile: networking disabled fails before approval, networking enabled without the proxy is broad, and managed-proxy domain rules are hard policy. Generic command networking now remains disabled; the fixed API-Football gateway handles the narrower team-crest requirement.
