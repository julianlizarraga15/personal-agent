# ADR 0018: Add an approval-gated public download gateway

- Status: Accepted
- Date: 2026-08-03

## Context

ADR 0017 safely solved official team crests but deliberately left unrelated public assets unavailable. The owner needs a reusable way to retrieve public PDFs, CSVs, images, archives, and other bounded files without adding a new fixed endpoint for every source. Pinned Codex 0.144.4 still cannot combine the named workspace permission profile with a reliable interactive exact-domain command-network grant. Enabling generic profile networking would materially widen the command sandbox.

## Decision

Add a required keyless `public-download` MCP adapter with one `download_file(url, destination)` tool. Give only that tool a Codex `approval_mode="approve"` override so it can reach the real bot-side approval boundary. Bind the gateway to the current project and owner chat for one active turn. Before approval, validate only the request shape and display the exact initial URL, relative destination, and configured maximum; perform no DNS, upstream, or filesystem action. Every request requires a fresh, memory-only **Download once** decision that expires after five minutes.

Accept only credential-free HTTPS URLs on port 443 with public DNS hostnames. Reject user info, credential-like query names, fragments, IP literals, and local/private naming. Resolve after approval, require every answer to be globally routable, pin the TCP connection to a validated answer while retaining TLS hostname verification, and repeat validation after same-host redirects. Reject host-changing redirects so their final URL requires separate approval. Send no credentials and ignore proxy environment variables. Cap declared and actual bodies at 50,000,000 bytes or a lower configured value.

Accept only a safe relative destination under the active project. Reject traversal, protected repository/config roots, environment and credential-like names, symlinked directories, and every existing destination. Write to a new temporary file through directory descriptors, flush and fsync it, then atomically link it into place without overwrite. Treat all downloaded bytes and content-type metadata as untrusted. Do not open, execute, or extract them automatically.

Keep generic shell networking disabled. Keep the stricter API-Football team-logo operation for crests because its fixed host, path, PNG validation, and destination provide a smaller boundary when applicable.

## Consequences

Future public assets no longer require endpoint-specific code, and each external source plus local destination remains visible to the owner before any effect. The gateway cannot download authenticated or signed-query resources, use non-HTTPS endpoints, follow cross-host CDN redirects, or install dependencies through ordinary package managers. The owner must approve each file separately, and must request a redirect's final URL when hosts change.
