# Security model

This project runs an agent that can modify and push code, so deployment security is part of the application boundary.

## Controls

- The default Codex backend starts ephemeral threads with workspace-write sandboxing and deny-all approvals. It may edit only the selected mounted workspace; attempted escalation fails without prompting in Telegram.
- The bot has no Docker socket, SSH private key, Git push credential, or OpenAI API key. `CODEX_HOME` is a private Docker volume containing ChatGPT authentication and Codex configuration and must never be baked into an image or committed.
- Shell networking, Docker access, writes outside the selected workspace, and credential-backed Git publication are outside the pass-1 bot boundary. The separate deployer is the only service with Docker authority, and deployment is host-initiated.
- Codex-mode sessions and Telegram-to-thread mappings are memory-only. Restarting discards conversations while retaining authentication in `CODEX_HOME`.
- Images, audio, legacy approvals, usage/trace exports, `/run`, and self-deployment commands are not registered in Codex mode. The controls below describe dormant `AGENT_BACKEND=responses` rollback behavior where applicable.
- Worker tasks run in short-lived Docker containers with a temporary checkout. The checkout is removed when the worker exits.
- The worker publishes to the checked-out default branch (`main` or `master`) after the task and tests complete.
- The Telegram bot accepts commands only from the numeric `TELEGRAM_ALLOWED_USER_ID`.
- The model receives only explicitly-defined tools, and file paths are constrained to the mounted workspace.
- Telegram images are downloaded into memory rather than through token-bearing public URLs, capped by declared and actual size, and decoded with decompression-bomb protection. Only verified JPEG, PNG, WEBP, and single-frame GIF inputs reach the model; their Base64 data is removed from replay state after the turn.
- Telegram audio is downloaded into memory only after owner authorization and busy-session checks, capped by declared and actual size plus reported duration, and classified from file signatures rather than Telegram MIME metadata. Only supported formats reach the OpenAI transcription endpoint; raw bytes are discarded and never enter session history or logs.
- The small routing model receives no tools or file contents. It conservatively chooses a model tier and minimum capability set, and falls back to the large agent with available capabilities for ambiguity or routing failures; its decision never bypasses path constraints or approval checks.
- The hosted web-search tool is the only external-information capability; web results are untrusted input and do not grant the model access to local secrets or arbitrary local network actions.
- File writes and dedicated Git commit/push operations require explicit Telegram approval with a short-lived request ID. Reaction approvals are accepted only from the configured owner and only on the Telegram message bound to that exact pending request; command approval remains available. An explicit self-deployment request uses one broad approval for that complete operation, including edits, tests, commit, push, rebuild, and restart.
- Destructive shell commands and shell-form Git publication remain blocked; Git publication uses the dedicated approval flow.
- `.env` and secret-bearing `.env.*` paths cannot be read or modified through file tools, and shell commands mentioning them are blocked; the public `.env.example` template remains readable. Secrets remain runtime configuration, never trace content.
- Secrets belong in the runtime environment or a local `.env` file. `.env` is ignored by Git and must never be committed.
- Operational logs are written to container stdout and intentionally exclude prompts, message text, file contents, command output, and credentials. Token counts, cache activity, web-search counts, and estimated cost are safe operational metadata. The durable usage database contains only timestamps, numeric Telegram user IDs, model/phase names, and those counts. A separate trace database contains owner-visible prompts and execution data after recursive redaction. The trace database defaults to the dedicated persistent `trace-state` Docker volume, is mounted only into bot/deployer, and uses owner-only database permissions; this avoids permissive mode emulation on Windows-backed workspace mounts. Restrict access to Docker logs, the usage state directory, and Docker volumes because trace content can include private source code, conversations, paths, project names, and numeric Telegram user IDs.
- `/prompt`, `/trace`, and `/traces` use the same numeric owner authorization as all other bot input. A requested trace ID is additionally scoped to that owner. Visibility does not bypass project boundaries, approval, destructive-command, publication, or deployment controls.
- Trace redaction replaces secret-shaped fields, authorization headers, recognized API/Telegram token patterns, private keys, and `.env` content. Raw image/audio bytes are represented only by media type, byte length, and SHA-256. Redaction is defense in depth, not a reason to submit credentials in chat or source files.
- Self-deployment writes only deployment ID, commit, image references, timestamps, and machine-readable status to `/workspace/.personal-agent-state`; an automatically released file lock prevents concurrent deployments. A dedicated deployer retains Docker-socket authority across bot replacement.
- Never inspect, read, print, or expose the contents of `.env` under any circumstances. Use `.env.example` for configuration guidance.
- Test commands are detected from repository metadata and run inside the worker container.

## Deployment requirements

- Run the bot only on a trusted host. The bot does not mount `/var/run/docker.sock`; the deployer does and therefore has effective control of the host Docker daemon.
- The deployer allows only the configured Compose file and `personal-agent-bot:*` image family. It has Docker-socket authority and must be treated as a host-control boundary; update its separate image explicitly from the host.
- Use a dedicated Git identity/token with the minimum repository permissions needed to create branches and push them.
- Do not mount the host filesystem, SSH agent, cloud credentials, or personal home directory into the bot or worker containers. The only intended host mount is the dedicated project `workspace/` directory.
- Pin or regularly review base images and Python dependencies before production use.
- Treat approval messages as exact-action confirmations; review the displayed path, operation, and summary before approving.
- Restrict the Telegram bot token and rotate it immediately if it is exposed.

## Threat assumptions

Repository code, issue text, commit messages, task requests, text visible inside images, and transcribed speech may contain prompt injection or malicious build steps. The agent must treat them as data, and operators should assume that running project tests can execute arbitrary code. Use a disposable host or stronger container isolation for untrusted repositories.

The trace database intentionally increases the amount of sensitive, owner-readable data at rest. Provider-private instructions and controls, raw chain-of-thought not returned by the API, hosted-search internals not returned by the provider, deleted original media, and credential values removed by redaction are outside the transparency contract. Protect and expire the state directory; the default retention is seven days.

## Incident response

Stop the bot, revoke exposed Telegram/Git credentials, inspect Docker and Git logs, and review recently-created `codex/*` branches. Record the incident and restore only from a known-good commit.
