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

You can also send one Telegram photo or JPEG, PNG, WEBP, or non-animated GIF document, with an optional caption explaining what to inspect or change. Images are validated, limited to 10 MiB by default (`TELEGRAM_MAX_IMAGE_BYTES`), and sent to the model at high detail. The image is available only during that turn; resend it for follow-up visual questions. Albums, stickers, animation, video, PDFs, and image generation are not supported.

Voice notes, Telegram audio attachments, and FLAC, MP3/MPEG/MPGA, MP4/M4A, OGG, WAV, or WebM audio documents are also accepted. The bot validates the actual file signature, limits audio to 20 MB (`TELEGRAM_MAX_AUDIO_BYTES`) and a reported duration of 10 minutes (`TELEGRAM_MAX_AUDIO_SECONDS`) by default, and transcribes it with `OPENAI_TRANSCRIPTION_MODEL` (`gpt-4o-mini-transcribe` by default). Spoken words become the user message. When the audio has a caption, the caption is the instruction applied to the transcript. Audio bytes are held only for transcription; later conversation turns retain normal text context, not the file.

The bot calls the OpenAI Responses API directly and gives the model image understanding, hosted web search, and constrained local tools for listing, reading, editing, testing, and inspecting Git. File listings, reads, and command results are bounded to keep large tool output out of later prompts. The model uses web search for current or externally verifiable facts and can include source citations. File writes and Git commit/push actions pause for explicit approval; react 👍 or 👎 to the matching approval prompt, or use `/approve [id]` or `/reject [id]` (the ID may be omitted when there is one pending request). A self-deployment request gets one approval covering the requested edits, tests, commit, push, rebuild, and restart. `/new` starts a fresh conversation in the default workspace, `/usage` reports tokens and estimated API cost for the current session, and `/stop` forgets the session. The original one-shot `/run` worker form remains supported. Sessions are kept in memory and are lost when the bot restarts; only one task runs per user at a time. The computer boundary is the explicitly-mounted `workspace/` directory.

Agent replies render ordinary Markdown formatting in Telegram, including headings, emphasis, links, lists, blockquotes, inline code, and fenced code blocks. Unsupported raw HTML is removed, and a response is retried as plain text if Telegram rejects its formatted form.

The conversational path uses a cost-aware router. GPT-5 nano answers high-confidence simple messages, GPT-5.6 Luna handles ordinary reasoning and web questions, Terra handles routine computer work, and Sol handles unusually difficult, high-stakes, ambiguous, or deployment work. The router also exposes only the web or computer capabilities required by the request. Set `OPENAI_ROUTER_ENABLED=0` to disable routing, or use the `OPENAI_*_MODEL`, reasoning-effort, output-token, compaction, and warning settings shown in `.env.example`.

Long conversations use Responses API compaction after the configured threshold; earlier replay items are discarded after the compacted state is returned. Automatic prompt-cache reads and writes are recorded but explicit cache writes are not enabled. `/usage` includes available transcription token counts and estimates standard OpenAI charges from a dated local pricing table. Transcription cost is shown as unknown until a verified rate is added, so the OpenAI billing dashboard remains authoritative.

The bot writes operational logs to stdout. Follow them in real time with `docker compose logs -f bot`; each turn includes a correlation ID and records routing, model requests, token/cache/search usage, high-usage warnings, tool start/finish, approval waits/resolutions/expiry, self-deployment stages, failures, and elapsed time. Logs intentionally exclude message text, prompts, file contents, command output, and credentials. Set `LOG_LEVEL=DEBUG` for additional library diagnostics or leave the default at `INFO`.

To let the agent update this bot, keep a checkout at `workspace/personal-agent` on `main` and run the dedicated `deployer` service. The agent tests and publishes the exact commit, then queues it for the deployer, which survives bot replacement and can roll back failed startup. Set `HOST_WORKSPACE_DIR`, `GIT_SSH_KEY_PATH`, and `GIT_KNOWN_HOSTS_PATH` to absolute host paths. A clean commit can be redeployed after an interrupted attempt; a bot restart still loses in-memory conversations and approvals.

Use `/pending` for active approvals, queued builds, startup verification, and rollback state. Deployment state is persisted outside Git at `/workspace/.personal-agent-state`; the restarted bot reports completion only after the deployer verifies stability. Stop it with `docker compose down`. Mounting `/var/run/docker.sock` gives these services host-level Docker authority; keep the deployment on a trusted machine.

The reusable worker API is available as `worker.execute_workflow(task, repo, on_status=...)`; the module CLI remains available inside the worker image.
