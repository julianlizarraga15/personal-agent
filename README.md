# Repository Worker

Minimal Dockerized Python worker controlled either from the command line or through Telegram. It clones a repository into a temporary workspace, asks Codex CLI to implement a task, runs available project tests, and pushes the result directly to `main` by default. This workflow does not create pull requests.

The repository operating contract is documented in [`AGENTS.md`](AGENTS.md). See [`SECURITY.md`](SECURITY.md) before deploying the Telegram bot, [`RUNBOOK.md`](RUNBOOK.md) for operations, and [`docs/adr/`](docs/adr/) for the architectural decisions behind the isolation and repository boundaries.

Unless noted otherwise, commands in this guide are intended to be run from the repository root.

## Local usage

Requires Python 3.11+, Git, and a non-interactive `codex` CLI available on `PATH`. Set `CODEX_BIN` when the executable has a different name.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m unittest discover -s tests
PYTHONPATH=src python -m worker \
  --task 'Add input validation' \
  --repo https://github.com/example/project.git
```

`--repo` may also be a local path to a Git repository. Codex is invoked as `codex exec --full-auto -- <task>`. Recognized `pytest`, npm, Go, Cargo, and Make test setups are run; projects without one are allowed to continue. A failed Codex run, test run, commit, or push stops the workflow and returns a non-zero exit code. The clone is removed automatically when the worker exits.

## Docker usage

Build and run the worker with:

```bash
docker build -t repository-worker .
docker run --rm repository-worker \
  --task 'Inspect the project structure' \
  --repo https://github.com/example/project.git
```

The image includes Git but expects the Codex CLI and its authentication to be supplied by the runtime image/environment. The Telegram bot runs separately and launches this image through Docker.

## Telegram usage with Docker Compose

Both the Telegram bot and coding worker run in containers. No local Python installation is required. The bot launches worker containers through the Docker socket, so run this on a machine with Docker Engine or Docker Desktop available.

Create a local `.env` file from the example and fill in your Telegram bot token, numeric Telegram user ID, and OpenAI API key:

```bash
cp .env.example .env
# edit .env
docker compose up --build
```

The bot uses long polling and accepts messages only from `TELEGRAM_ALLOWED_USER_ID`. The mounted `workspace/` directory is the default computer context, so ordinary messages can work there immediately. To narrow the context to one project directory, use:

```text
/project my-project
```

Then send ordinary text messages:

```text
Add input validation to the API
Now add tests for that validation
```

The bot calls the OpenAI Responses API directly and gives the model hosted web search plus constrained local tools for listing, reading, editing, testing, and inspecting Git. The model uses web search for current or externally verifiable facts and can include source citations. File writes and Git commit/push actions pause for explicit approval; approve or reject them with `/approve [id]` or `/reject [id]` (the ID may be omitted when there is one pending request). A self-deployment request gets one approval covering the requested edits, tests, commit, push, rebuild, and restart. `/new` starts a fresh conversation in the default workspace; `/stop` forgets the session. The original one-shot `/run` worker form remains supported. Sessions are kept in memory and are lost when the bot restarts; only one task runs per user at a time. The computer boundary is the explicitly-mounted `workspace/` directory.

The conversational path first uses a small conservative router for clearly simple messages. Coding, tool, web-search, contextual, or uncertain requests use the configured large model. Set `OPENAI_ROUTER_ENABLED=0` to disable routing, or configure `OPENAI_ROUTER_MODEL` and `OPENAI_MODEL` in the environment.

The bot writes operational logs to stdout. Follow them in real time with `docker compose logs -f bot`; each turn includes a correlation ID and records routing, model requests, tool start/finish, approval waits/resolutions/expiry, self-deployment stages, failures, and elapsed time. Logs intentionally exclude message text, prompts, file contents, command output, and credentials. Set `LOG_LEVEL=DEBUG` for additional library diagnostics or leave the default at `INFO`.

To let the agent update this bot, keep a checkout at `workspace/personal-agent` on `main` and run the dedicated `deployer` service. The agent tests and publishes the exact commit, then queues it for the deployer, which survives bot replacement and can roll back failed startup. Set `HOST_WORKSPACE_DIR`, `GIT_SSH_KEY_PATH`, and `GIT_KNOWN_HOSTS_PATH` to absolute host paths. A clean commit can be redeployed after an interrupted attempt; a bot restart still loses in-memory conversations and approvals.

Use `/pending` for active approvals, queued builds, startup verification, and rollback state. Deployment state is persisted outside Git at `/workspace/.personal-agent-state`; the restarted bot reports completion only after the deployer verifies stability. Stop it with `docker compose down`. Mounting `/var/run/docker.sock` gives these services host-level Docker authority; keep the deployment on a trusted machine.

The reusable worker API is available as `worker.execute_workflow(task, repo, on_status=...)`; the module CLI remains available inside the worker image.
