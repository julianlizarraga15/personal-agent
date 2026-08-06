# Repository Worker

Minimal Dockerized Python worker controlled either from the command line or through Telegram. It clones a repository into a temporary workspace, asks Codex CLI to implement a task, runs available project tests, and pushes the result directly to `main` by default. This workflow does not create pull requests.

The repository operating contract is documented in [`AGENTS.md`](AGENTS.md). See [`SECURITY.md`](SECURITY.md) before deploying the Telegram bot, [`RUNBOOK.md`](RUNBOOK.md) for operations, and [`docs/adr/`](docs/adr/) for the architectural decisions behind the isolation and repository boundaries. Open [`codex-architecture.html`](codex-architecture.html) for a simplified visual map of the current Codex runtime and security boundary; [`architecture-map.html`](architecture-map.html) preserves the more detailed legacy architecture snapshot.

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

The default `AGENT_BACKEND=codex` path uses the official Codex Python SDK and a signed-in ChatGPT subscription. Text turns do not need an OpenAI API key. Optional audio transcription uses a separate OpenAI Platform API key mounted only into the bot; that direct API usage is billed separately from ChatGPT. The bot has a writable `workspace/` mount, but no Docker socket or deployment queue. Optional Git publication keeps one repository-scoped deploy key behind a bot-side gateway; the Codex command sandbox never receives either credential.

Create `.env`, build the image, and perform the one-time device login. The `codex-state` volume preserves authentication and Codex configuration across container replacement.

```bash
cp .env.example .env
# edit the Telegram token and numeric owner ID; optionally add API_FOOTBALL_KEY
docker compose build bot
docker compose run --rm codex-login
docker compose up -d bot
```

Open the printed verification URL, enter its code, and wait for the command to report success. Never copy `auth.json` into an image or repository.

The bot accepts only `TELEGRAM_ALLOWED_USER_ID`. Ordinary text starts an ephemeral Codex thread in the workspace root. `/project <directory>` validates a directory beneath that root and starts a fresh thread there; `/new` starts fresh at the root; `/stop` interrupts the active turn and discards the session; `/help` lists the same pass-1 surface. One turn may run per user. Restarting the bot forgets every thread but preserves login state. The image includes `uv` for Python project setup.

Threads use a named Codex permissions profile derived from `:workspace`. Codex may edit and run commands in the selected workspace automatically, while project-command networking remains disabled. In pinned Codex 0.144.4, this permission-profile mode does not expose a safe destination-scoped approval path: enabling networking without the managed proxy is broad, and proxy domain rules are hard policy rather than interactive approval. Shell downloads therefore remain unavailable. Public files, optional Git publication, and API-Football team-logo retrieval use separate bot-side gateways described below.

When the optional `API_FOOTBALL_KEY` from the API-Sports dashboard is set in the private host `.env`, Codex can query approved read-only football analytics through the API-Football MCP `get` tool. The same MCP exposes `download_team_logo` for official crests: it accepts only a numeric team ID, fixes the unauthenticated origin and path to `https://media.api-sports.io/football/teams/<id>.png`, follows no redirects, requires a bounded PNG, and atomically saves `assets/team-crests/<id>.png` beneath the project bound to the active owner turn. Its keyless stdio adapter talks only to `/run/api-football.sock`; the in-process bot gateway separately fixes analytics to `https://v3.football.api-sports.io`, verifies TLS, injects `x-apisports-key`, ignores proxy environment variables, and never gives the key to Codex. Project shell networking remains disabled. The packaged `api-football endpoints`, `api-football --help`, and `api-football get ...` commands are for trusted container diagnostics, not sandbox tasks. Odds, bookmakers, predictions, arbitrary URLs/paths, non-GET analytics requests, and unknown endpoints are rejected. A protected atomic counter in `trace-state` allows at most 100 attempted analytics or logo calls per UTC day; invalid local requests do not count, and there is no response cache. Without a configured key the bot remains healthy; analytics report that API-Football is not configured while the unauthenticated fixed logo operation remains available during an active turn.

For other owner-requested public files, Codex uses the required `public-download.download_file` MCP tool with a credential-free HTTPS URL and exact project-relative destination. The keyless adapter talks only to `/run/public-download.sock`; the bot sends the URL, destination, and 50 MB default ceiling (`PUBLIC_DOWNLOAD_MAX_BYTES`) directly to the owner chat and waits up to five minutes for **Download once**. Rejection or expiry performs no DNS lookup, network request, or write. After approval, the gateway resolves the hostname once, rejects any private/local/non-global answer, pins the connection to the validated address while retaining TLS hostname verification, follows at most five same-host redirects, and rejects cross-host redirects. Declared and actual sizes are bounded, and bytes are atomically written through non-symlink directory descriptors. Protected metadata and credential-like destinations are rejected. No headers, cookies, credentials, execution, or automatic extraction are supported; downloaded files remain untrusted project input.

The bot image uses system Bubblewrap with a narrow seccomp profile rather than privileged mode, `SYS_ADMIN`, or an unconfined container. A clean launcher prevents Codex commands from inheriting Telegram, OpenAI, Git, or proxy credentials. The command sandbox cannot read `/codex-home`, `/trace-state`, `/git-publish-secrets`, `/openai-transcription-secrets`, or workspace `.env` files. The Compose bot still has no Docker socket.

### Repository-scoped Git publication

To let Codex push `mental-models`, clone it beneath the configured host workspace as `mental-models`, then create a dedicated Ed25519 deploy key in a private directory outside that workspace. Add only its public key to the GitHub repository under **Settings → Deploy keys** and enable write access. Put the private key at `deploy-key` and a verified GitHub SSH host-key file at `known_hosts` in the private directory; both should be owner-readable only.

Set these private host `.env` values:

```dotenv
GIT_PUBLISH_REPOSITORY=/workspace/mental-models
GIT_PUBLISH_REMOTE=git@github.com:julianlizarraga15/mental-models.git
GIT_PUBLISH_BRANCH=main
GIT_PUBLISH_SECRETS_DIR=/absolute/path/to/mental-models-publish-secrets
```

Copy `docker-compose.git-publish.example.yml` to the ignored local file `docker-compose.git-publish.yml`, then include it whenever managing the bot:

```bash
docker compose -f docker-compose.yml -f docker-compose.git-publish.yml build bot
docker compose -f docker-compose.yml -f docker-compose.git-publish.yml up -d bot
```

After `/project mental-models`, ask Codex to commit and publish. The gateway requires a clean worktree, displays the fixed remote, branch, and full commit ID in Telegram, and pushes only after **Publish once**. It never force-pushes. Missing key/configuration leaves the bot healthy and causes the tool to return a setup error.

Publication has one deliberate approval point. Codex is configured with `mcp_servers.git-publish.tools.publish.approval_mode="approve"` so its generic MCP write-tool reviewer does not reject the call before it reaches the application. This does not approve a Git push: the keyless MCP adapter can only ask the bot-side gateway, which sends the exact commit and destination directly to the owner chat and waits up to five minutes for **Publish once**. A request is rejected if that card cannot be delivered, expires, or is rejected. Operational logs record MCP completion plus approval delivery and outcome without logging message contents or credentials.

Project-controlled Git configuration and attributes are treated as untrusted. Repository inspection and bundle creation run inside a networkless, read-only Bubblewrap that masks the deploy key and other protected state. After approval, the gateway imports that bounded bundle into a fresh bare repository and gives only that clean repository access to the one deploy key for the exact non-force push.

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

You can send one Telegram photo in Codex mode. The optional caption tells Codex what to inspect; without one, the bot asks for a general description, visible text, errors, and actionable details. The bot reserves the user turn before downloading, verifies the declared and actual size, decodes the real format with decompression-bomb protection, and passes the validated JPEG, PNG, WEBP, or static GIF bytes directly to the SDK as an in-memory data URL. The default limit is 10 MiB (`TELEGRAM_MAX_IMAGE_BYTES`). Photo context belongs to the ephemeral Codex conversation and disappears on `/new`, `/project`, `/stop`, or bot restart.

Voice notes and Telegram audio attachments are accepted in Codex mode. The bot validates the actual signature, limits audio to 20 MB (`TELEGRAM_MAX_AUDIO_BYTES`) and a reported duration of 10 minutes (`TELEGRAM_MAX_AUDIO_SECONDS`), then transcribes it in memory with `OPENAI_TRANSCRIPTION_MODEL` (`gpt-4o-mini-transcribe` by default). A caption becomes the instruction over the transcript; without a caption, the transcript is submitted directly. The per-user turn guard covers download, transcription, and Codex execution, and `/stop`, `/new`, or `/project` prevents a late transcript from entering the replacement session. Raw audio and transcript content are not logged.

Any file explicitly sent as a Telegram document—including images, SVG, PDF, archives, source code, and arbitrary binary formats—is instead saved byte-for-byte at `telegram_uploads/<sanitized-original-name>` in the active project. The default document limit is 20,000,000 bytes (`TELEGRAM_MAX_DOCUMENT_BYTES`), matching the hosted [Telegram Bot API download ceiling](https://core.telegram.org/bots/api#getfile). MIME type is informational only; the bot does not execute or extract an upload. The caption becomes the instruction, or Codex is asked to identify and safely inspect a captionless file, and the exact relative workspace path is included in the turn. Uploads persist across later turns and session resets, replace an existing same-name file atomically, and intentionally change the selected project's working tree. They are not automatically ignored, deleted, or excluded from commits.

Document support does not authorize sending secrets. Traversal/control-character names, symlinked `telegram_uploads/` directories, protected `.env` variants, recognizable credential/private-key filenames, and private-key content are rejected; `.env.example` remains allowed. One document per message is supported. Albums and multi-file batching are not added.

Audio transcription is optional and uses OpenAI Platform billing, separate from the ChatGPT subscription used by Codex. Create a dedicated Platform key, store it in an owner-readable `api-key` file in a private directory outside the workspace, and set `OPENAI_TRANSCRIPTION_SECRETS_DIR` to that directory. Copy `docker-compose.transcription.example.yml` to the ignored local file `docker-compose.transcription.yml`, then include it alongside any Git publication override:

```bash
docker compose -f docker-compose.yml -f docker-compose.git-publish.yml -f docker-compose.transcription.yml build bot
docker compose -f docker-compose.yml -f docker-compose.git-publish.yml -f docker-compose.transcription.yml up -d bot
```

The directory is mounted read-only at `/openai-transcription-secrets`; the bot reads only `api-key`, and the sanitized Codex launcher neither inherits the key nor permits sandbox access to that mount. If the override or key is absent, the bot stays healthy and explains that transcription is not configured. At the planned model’s documented estimate of roughly $0.003 per audio minute, actual charges remain subject to the OpenAI pricing dashboard.

When you ask Codex to create and send a plot or other image as a photo, it can attach PNG, JPEG, WEBP, or static GIF files from the selected project. The bot resolves each requested path beneath the active project, rejects protected `.env` paths, verifies the actual image content, and limits each photo-style attachment to 10 MiB by default (`TELEGRAM_MAX_OUTPUT_IMAGE_BYTES`). PNG, JPEG, and WEBP are sent as photos with a document fallback; static GIFs are sent as documents.

When you ask for a file/document or say “send as files,” Codex can instead attach any safe regular project file as a Telegram document, preserving its original filename and bytes. This includes PNG/JPEG originals, PDFs, archives, source, and binary files; document delivery never attempts Telegram photo conversion. Each file is limited to 50,000,000 bytes by default (`TELEGRAM_MAX_OUTPUT_DOCUMENT_BYTES`), matching Telegram's hosted Bot API document-upload limit. Missing, unreadable, outside-project, protected `.env`, recognizable credential/private-key, and private-key-content files fail with content-free errors. Photo and document requests share one maximum of four outbound attachments per turn and are sent individually rather than automatically archived.

Project commands still cannot access the network directly. Public files must use the approval-gated `download_file` tool; team crests should use the stricter fixed API-Football `download_team_logo` operation.

### Dormant Responses rollback

The previous Responses implementation remains in the source but is not registered in Codex mode. To roll back manually, set `AGENT_BACKEND=responses`, add `OPENAI_API_KEY` to the bot through a private local Compose override, rebuild, and restart the bot. That mode restores its own media, routing, approval, usage, trace, and `/run` implementation; self-deployment is intentionally disconnected from Docker authority. Do not commit the override or credentials.

You can also send one Telegram photo or JPEG, PNG, WEBP, or non-animated GIF document, with an optional caption explaining what to inspect or change. Images are validated, limited to 10 MiB by default (`TELEGRAM_MAX_IMAGE_BYTES`), and sent to the model at high detail. The image is available only during that turn; resend it for follow-up visual questions. Albums, stickers, animation, video, PDFs, and image generation are not supported.

Voice notes, Telegram audio attachments, and FLAC, MP3/MPEG/MPGA, MP4/M4A, OGG, WAV, or WebM audio documents are also accepted. The bot validates the actual file signature, limits audio to 20 MB (`TELEGRAM_MAX_AUDIO_BYTES`) and a reported duration of 10 minutes (`TELEGRAM_MAX_AUDIO_SECONDS`) by default, and transcribes it with `OPENAI_TRANSCRIPTION_MODEL` (`gpt-4o-mini-transcribe` by default). Spoken words become the user message. When the audio has a caption, the caption is the instruction applied to the transcript. Audio bytes are held only for transcription; later conversation turns retain normal text context, not the file.

The bot calls the OpenAI Responses API directly and gives the model image understanding, hosted web search, and constrained local tools for listing, reading, editing, testing, and inspecting Git. File listings, reads, and command results are bounded to keep large tool output out of later prompts. The model uses web search for current or externally verifiable facts and can include source citations. File writes and Git commit/push actions pause for explicit approval; react 👍 or 👎 to the matching approval prompt, or use `/approve [id]` or `/reject [id]` (the ID may be omitted when there is one pending request). A self-deployment request gets one approval covering the requested edits, tests, commit, push, rebuild, and restart. `/new` starts a fresh conversation in the default workspace, `/usage` reports current-session, current UTC day, and all recorded direct-API usage, and `/stop` forgets the session. `/prompt` exports the exact active application prompts, dynamic project context, tool definitions, model settings, capability rules, and provider limits. `/traces` lists retained owner traces and `/trace [turn-id]` exports the latest or selected trace. Common questions such as “what’s your prompt?” deterministically use the same export path without asking a model. The original one-shot `/run` worker form remains supported. Conversation context is kept in memory and is lost when the bot restarts, while usage metadata and traces persist; only one task runs per user at a time. The computer boundary is the explicitly-mounted `workspace/` directory.

Agent replies render ordinary Markdown formatting in Telegram, including headings, emphasis, links, lists, blockquotes, inline code, and fenced code blocks. Unsupported raw HTML is removed. Responses longer than Telegram's 4,096-character message limit are delivered as ordered chunks at readable boundaries, and each chunk is retried as plain text if Telegram rejects its formatted form.

The conversational path uses a cost-aware router. GPT-5 nano answers high-confidence simple messages, GPT-5.6 Luna handles ordinary reasoning and web questions, Terra handles routine computer work, and Sol handles unusually difficult, high-stakes, ambiguous, or deployment work. The router also exposes only the web or computer capabilities required by the request. Set `OPENAI_ROUTER_ENABLED=0` to disable routing, or use the `OPENAI_*_MODEL`, reasoning-effort, output-token, compaction, and warning settings shown in `.env.example`.

Long conversations use Responses API compaction after the configured threshold; earlier replay items are discarded after the compacted state is returned. Automatic prompt-cache reads and writes are recorded but explicit cache writes are not enabled. `/usage` includes available transcription token counts and estimates standard OpenAI charges from a dated local pricing table. Raw usage metadata is appended to `USAGE_DB_PATH` (`/workspace/.personal-agent-state/usage.sqlite3` by default), which is outside Git on the persistent workspace mount. Session resets and bot replacement do not clear daily or lifetime totals. Transcription cost is shown as unknown until a verified rate is added, and legacy `/run` Codex CLI usage is not included, so the OpenAI billing dashboard remains authoritative.

Every conversational, routing, transcription, model, hosted-search observation, computer tool, approval, self-deployment, and legacy `/run` turn gets an ordered, versioned trace. The live Telegram activity message shows the route/model or current tool/deployment stage, completion state, and matching turn ID without flooding the chat. Model-bound file and command output stays bounded, while the trace stores the complete application-observed redacted result, timing, errors, usage, truncation metadata, reasoning summaries, and compaction events.

Traces are stored at `TRACE_DB_PATH` (`/trace-state/traces.sqlite3` by default) in the dedicated persistent `trace-state` Docker volume shared only by the bot and deployer. The database is created with owner-only permissions and expired after `TRACE_RETENTION_DAYS` (seven days by default) on startup and normal writes. Exports are redacted JSON compressed with gzip; files larger than `TRACE_EXPORT_PART_BYTES` are emitted as numbered byte-for-byte parts that can be concatenated before decompression. Only the configured Telegram owner may list or export them. Credential-shaped fields and token patterns are redacted recursively. Secret-bearing `.env` files cannot be read, written, or printed by computer tools; `.env.example` remains available as the public template. Raw image and audio bytes are never duplicated into a trace; their media type, size, and SHA-256 are recorded instead.

Compatible main models are asked for an automatic reasoning summary; a provider rejection is traced and retried once without the summary. The transparency boundary is everything the application can observe. Provider-hidden controls, unreturned raw chain-of-thought, hosted-search internals that are not returned by the API, discarded original media after its turn, and redacted credential values are necessarily absent. Transparency does not grant new filesystem, command, approval, or deployment authority.

The bot writes content-free operational logs to stdout. Follow them in real time with `docker compose logs -f bot`; each turn includes a correlation ID and records only stage metadata such as routing, model requests, token/cache/search usage, tool start/finish, approvals, self-deployment stages, failures, and elapsed time. Logs intentionally exclude message text, prompts, file contents, command output, and credentials; detailed owner data is available only through the protected trace database and authenticated Telegram exports. Set `LOG_LEVEL=DEBUG` for additional library diagnostics or leave the default at `INFO`.

The optional deployer is reserved for host-managed recovery. It is disabled by default behind the `manual-deployer` profile, does not restart automatically, and consumes a private queue volume that the bot cannot access. It also requires the queued commit to equal the configured HTTPS remote branch before building.

Mounting `/var/run/docker.sock` gives the optional deployer host-level authority. Start it only with `docker compose --profile manual-deployer up -d deployer` when a trusted host workflow explicitly needs it, and stop it afterward. The default bot deployment does not start or depend on it.

The reusable worker API is available as `worker.execute_workflow(task, repo, on_status=...)`; the module CLI remains available inside the worker image.
