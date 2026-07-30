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

The default `AGENT_BACKEND=codex` path uses the official Codex Python SDK and a signed-in ChatGPT subscription. It does not need an OpenAI API key. The bot has a writable `workspace/` mount, but no Docker socket, SSH key, or Git push credential.

Create `.env`, build the image, and perform the one-time device login. The `codex-state` volume preserves authentication and Codex configuration across container replacement.

```bash
cp .env.example .env
# edit the Telegram token and numeric owner ID
docker compose build bot
docker compose run --rm codex-login
docker compose up -d bot deployer
```

Open the printed verification URL, enter its code, and wait for the command to report success. Never copy `auth.json` into an image or repository.

The bot accepts only `TELEGRAM_ALLOWED_USER_ID`. Ordinary text starts an ephemeral Codex thread in the workspace root. `/project <directory>` validates a directory beneath that root and starts a fresh thread there; `/new` starts fresh at the root; `/stop` interrupts the active turn and discards the session; `/help` lists the same pass-1 surface. One turn may run per user. Restarting the bot forgets every thread but preserves login state.

Threads use Codex defaults with `Sandbox.workspace_write` and deny-all approvals. Codex may edit the selected workspace automatically. Requests for escalation, writes outside the sandbox, shell networking, Docker access, or credential-backed Git push fail without an approval prompt. Images and audio are rejected in pass 1; send text instead.

To select a project and work across continued turns:

```text
/project my-project
```

Then send ordinary text messages:

```text
Add input validation to the API
Now add tests for that validation
```

Agent replies use the existing safe Telegram Markdown renderer. A single debounced activity message reports coarse thinking, command, and file-change progress.

### Dormant Responses rollback

The previous Responses implementation remains in the source but is not registered in Codex mode. To roll back manually, set `AGENT_BACKEND=responses`, add `OPENAI_API_KEY` to the bot through a private local Compose override, rebuild, and restart the bot. That mode restores media, routing, approval, usage, trace, `/run`, and self-deployment commands; it also requires restoring any Docker/Git mounts needed for those legacy operations. Do not commit the override or credentials.

You can also send one Telegram photo or JPEG, PNG, WEBP, or non-animated GIF document, with an optional caption explaining what to inspect or change. Images are validated, limited to 10 MiB by default (`TELEGRAM_MAX_IMAGE_BYTES`), and sent to the model at high detail. The image is available only during that turn; resend it for follow-up visual questions. Albums, stickers, animation, video, PDFs, and image generation are not supported.

Voice notes, Telegram audio attachments, and FLAC, MP3/MPEG/MPGA, MP4/M4A, OGG, WAV, or WebM audio documents are also accepted. The bot validates the actual file signature, limits audio to 20 MB (`TELEGRAM_MAX_AUDIO_BYTES`) and a reported duration of 10 minutes (`TELEGRAM_MAX_AUDIO_SECONDS`) by default, and transcribes it with `OPENAI_TRANSCRIPTION_MODEL` (`gpt-4o-mini-transcribe` by default). Spoken words become the user message. When the audio has a caption, the caption is the instruction applied to the transcript. Audio bytes are held only for transcription; later conversation turns retain normal text context, not the file.

The bot calls the OpenAI Responses API directly and gives the model image understanding, hosted web search, and constrained local tools for listing, reading, editing, testing, and inspecting Git. File listings, reads, and command results are bounded to keep large tool output out of later prompts. The model uses web search for current or externally verifiable facts and can include source citations. File writes and Git commit/push actions pause for explicit approval; react 👍 or 👎 to the matching approval prompt, or use `/approve [id]` or `/reject [id]` (the ID may be omitted when there is one pending request). A self-deployment request gets one approval covering the requested edits, tests, commit, push, rebuild, and restart. `/new` starts a fresh conversation in the default workspace, `/usage` reports current-session, current UTC day, and all recorded direct-API usage, and `/stop` forgets the session. `/prompt` exports the exact active application prompts, dynamic project context, tool definitions, model settings, capability rules, and provider limits. `/traces` lists retained owner traces and `/trace [turn-id]` exports the latest or selected trace. Common questions such as “what’s your prompt?” deterministically use the same export path without asking a model. The original one-shot `/run` worker form remains supported. Conversation context is kept in memory and is lost when the bot restarts, while usage metadata and traces persist; only one task runs per user at a time. The computer boundary is the explicitly-mounted `workspace/` directory.

Agent replies render ordinary Markdown formatting in Telegram, including headings, emphasis, links, lists, blockquotes, inline code, and fenced code blocks. Unsupported raw HTML is removed, and a response is retried as plain text if Telegram rejects its formatted form.

The conversational path uses a cost-aware router. GPT-5 nano answers high-confidence simple messages, GPT-5.6 Luna handles ordinary reasoning and web questions, Terra handles routine computer work, and Sol handles unusually difficult, high-stakes, ambiguous, or deployment work. The router also exposes only the web or computer capabilities required by the request. Set `OPENAI_ROUTER_ENABLED=0` to disable routing, or use the `OPENAI_*_MODEL`, reasoning-effort, output-token, compaction, and warning settings shown in `.env.example`.

Long conversations use Responses API compaction after the configured threshold; earlier replay items are discarded after the compacted state is returned. Automatic prompt-cache reads and writes are recorded but explicit cache writes are not enabled. `/usage` includes available transcription token counts and estimates standard OpenAI charges from a dated local pricing table. Raw usage metadata is appended to `USAGE_DB_PATH` (`/workspace/.personal-agent-state/usage.sqlite3` by default), which is outside Git on the persistent workspace mount. Session resets and bot replacement do not clear daily or lifetime totals. Transcription cost is shown as unknown until a verified rate is added, and legacy `/run` Codex CLI usage is not included, so the OpenAI billing dashboard remains authoritative.

Every conversational, routing, transcription, model, hosted-search observation, computer tool, approval, self-deployment, and legacy `/run` turn gets an ordered, versioned trace. The live Telegram activity message shows the route/model or current tool/deployment stage, completion state, and matching turn ID without flooding the chat. Model-bound file and command output stays bounded, while the trace stores the complete application-observed redacted result, timing, errors, usage, truncation metadata, reasoning summaries, and compaction events.

Traces are stored at `TRACE_DB_PATH` (`/trace-state/traces.sqlite3` by default) in the dedicated persistent `trace-state` Docker volume shared only by the bot and deployer. The database is created with owner-only permissions and expired after `TRACE_RETENTION_DAYS` (seven days by default) on startup and normal writes. Exports are redacted JSON compressed with gzip; files larger than `TRACE_EXPORT_PART_BYTES` are emitted as numbered byte-for-byte parts that can be concatenated before decompression. Only the configured Telegram owner may list or export them. Credential-shaped fields and token patterns are redacted recursively. Secret-bearing `.env` files cannot be read, written, or printed by computer tools; `.env.example` remains available as the public template. Raw image and audio bytes are never duplicated into a trace; their media type, size, and SHA-256 are recorded instead.

Compatible main models are asked for an automatic reasoning summary; a provider rejection is traced and retried once without the summary. The transparency boundary is everything the application can observe. Provider-hidden controls, unreturned raw chain-of-thought, hosted-search internals that are not returned by the API, discarded original media after its turn, and redacted credential values are necessarily absent. Transparency does not grant new filesystem, command, approval, or deployment authority.

The bot writes content-free operational logs to stdout. Follow them in real time with `docker compose logs -f bot`; each turn includes a correlation ID and records only stage metadata such as routing, model requests, token/cache/search usage, tool start/finish, approvals, self-deployment stages, failures, and elapsed time. Logs intentionally exclude message text, prompts, file contents, command output, and credentials; detailed owner data is available only through the protected trace database and authenticated Telegram exports. Set `LOG_LEVEL=DEBUG` for additional library diagnostics or leave the default at `INFO`.

To let the agent update this bot, keep a checkout at `workspace/personal-agent` on `main` and run the dedicated `deployer` service. The agent tests and publishes the exact commit, then queues it for the deployer, which survives bot replacement and can roll back failed startup. Set `HOST_WORKSPACE_DIR`, `GIT_SSH_KEY_PATH`, and `GIT_KNOWN_HOSTS_PATH` to absolute host paths. A clean commit can be redeployed after an interrupted attempt; a bot restart still loses in-memory conversations and approvals.

Use `/pending` for active approvals, queued builds, startup verification, and rollback state. Deployment state is persisted outside Git at `/workspace/.personal-agent-state`; the restarted bot reports completion only after the deployer verifies stability. Stop it with `docker compose down`. Mounting `/var/run/docker.sock` gives these services host-level Docker authority; keep the deployment on a trusted machine.

The reusable worker API is available as `worker.execute_workflow(task, repo, on_status=...)`; the module CLI remains available inside the worker image.
