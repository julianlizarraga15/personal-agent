# Security model

This project runs an agent that can modify and push code, so deployment security is part of the application boundary.

## Controls

- Worker tasks run in short-lived Docker containers with a temporary checkout. The checkout is removed when the worker exits.
- The worker publishes to the checked-out default branch (`main` or `master`) after the task and tests complete.
- The Telegram bot accepts commands only from the numeric `TELEGRAM_ALLOWED_USER_ID`.
- The model receives only explicitly-defined tools, and file paths are constrained to the mounted workspace.
- Telegram images are downloaded into memory rather than through token-bearing public URLs, capped by declared and actual size, and decoded with decompression-bomb protection. Only verified JPEG, PNG, WEBP, and single-frame GIF inputs reach the model; their Base64 data is removed from replay state after the turn.
- The small routing model receives no tools or file contents. It conservatively chooses a model tier and minimum capability set, and falls back to the large agent with available capabilities for ambiguity or routing failures; its decision never bypasses path constraints or approval checks.
- The hosted web-search tool is the only external-information capability; web results are untrusted input and do not grant the model access to local secrets or arbitrary local network actions.
- File writes and dedicated Git commit/push operations require explicit Telegram approval with a short-lived request ID. Reaction approvals are accepted only from the configured owner and only on the Telegram message bound to that exact pending request; command approval remains available. An explicit self-deployment request uses one broad approval for that complete operation, including edits, tests, commit, push, rebuild, and restart.
- Destructive shell commands and shell-form Git publication remain blocked; Git publication uses the dedicated approval flow.
- Secrets belong in the runtime environment or a local `.env` file. `.env` is ignored by Git and must never be committed.
- Operational logs are written to container stdout and intentionally exclude prompts, message text, file contents, command output, and credentials. Token counts, cache activity, web-search counts, and estimated cost are safe operational metadata. Restrict access to Docker logs because paths, tool names, project names, and numeric Telegram user IDs may still be present.
- Self-deployment writes only deployment ID, commit, image references, timestamps, and machine-readable status to `/workspace/.personal-agent-state`; an automatically released file lock prevents concurrent deployments. A dedicated deployer retains Docker-socket authority across bot replacement.
- Never inspect, read, print, or expose the contents of `.env` under any circumstances. Use `.env.example` for configuration guidance.
- Test commands are detected from repository metadata and run inside the worker container.

## Deployment requirements

- Run the bot only on a trusted host. Mounting `/var/run/docker.sock` effectively gives the bot control of the host Docker daemon.
- The deployer allows only the configured Compose file and `personal-agent-bot:*` image family. It has Docker-socket authority and must be treated as a host-control boundary; update its separate image explicitly from the host.
- Use a dedicated Git identity/token with the minimum repository permissions needed to create branches and push them.
- Do not mount the host filesystem, SSH agent, cloud credentials, or personal home directory into the bot or worker containers. The only intended host mount is the dedicated project `workspace/` directory.
- Pin or regularly review base images and Python dependencies before production use.
- Treat approval messages as exact-action confirmations; review the displayed path, operation, and summary before approving.
- Restrict the Telegram bot token and rotate it immediately if it is exposed.

## Threat assumptions

Repository code, issue text, commit messages, task requests, and text visible inside images may contain prompt injection or malicious build steps. The agent must treat them as data, and operators should assume that running project tests can execute arbitrary code. Use a disposable host or stronger container isolation for untrusted repositories.

## Incident response

Stop the bot, revoke exposed Telegram/Git credentials, inspect Docker and Git logs, and review recently-created `codex/*` branches. Record the incident and restore only from a known-good commit.
