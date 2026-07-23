# Security model

This project runs an agent that can modify and push code, so deployment security is part of the application boundary.

## Controls

- Worker tasks run in short-lived Docker containers with a temporary checkout. The checkout is removed when the worker exits.
- The worker publishes only a newly-created `codex/*` branch and refuses direct publication to `main` or `master`.
- The Telegram bot accepts commands only from the numeric `TELEGRAM_ALLOWED_USER_ID`.
- The model receives only explicitly-defined tools, and file paths are constrained to the mounted workspace.
- The hosted web-search tool is the only external-information capability; web results are untrusted input and do not grant the model access to local secrets or arbitrary local network actions.
- File writes and dedicated Git commit/push operations require explicit Telegram approval with a short-lived request ID.
- Destructive shell commands and shell-form Git publication remain blocked; approved pushes are restricted to `codex/*` branches.
- Secrets belong in the runtime environment or a local `.env` file. `.env` is ignored by Git and must never be committed.
- Never inspect, read, print, or expose the contents of `.env` under any circumstances. Use `.env.example` for configuration guidance.
- Test commands are detected from repository metadata and run inside the worker container.

## Deployment requirements

- Run the bot only on a trusted host. Mounting `/var/run/docker.sock` effectively gives the bot control of the host Docker daemon.
- Use a dedicated Git identity/token with the minimum repository permissions needed to create branches and push them.
- Do not mount the host filesystem, SSH agent, cloud credentials, or personal home directory into the bot or worker containers. The only intended host mount is the dedicated project `workspace/` directory.
- Pin or regularly review base images and Python dependencies before production use.
- Treat approval messages as exact-action confirmations; review the displayed path, operation, and summary before approving.
- Restrict the Telegram bot token and rotate it immediately if it is exposed.

## Threat assumptions

Repository code, issue text, commit messages, and task requests may contain prompt injection or malicious build steps. The agent must treat them as data, and operators should assume that running project tests can execute arbitrary code. Use a disposable host or stronger container isolation for untrusted repositories.

## Incident response

Stop the bot, revoke exposed Telegram/Git credentials, inspect Docker and Git logs, and review recently-created `codex/*` branches. Record the incident and restore only from a known-good commit.
