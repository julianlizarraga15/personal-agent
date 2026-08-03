# ADR 0016: Enable approved public file downloads

- Status: Accepted
- Date: 2026-08-03

## Context

The exact-host network approval introduced by ADR 0009 works only when Codex emits a command approval containing a concrete HTTPS destination. The bot image did not include `curl` or `wget`, so an owner-requested download fell back to an opaque Python HTTPS client. Its DNS request was blocked inside Bubblewrap, and generic network escalation correctly failed closed without producing an owner approval card. API-Football could return team crest URLs but its fixed analytics gateway could not retrieve assets from the separate media host.

The activity message also changed to `Completed.` whenever Codex returned a normal final response, even when that response explicitly reported unfinished work.

## Decision

Install `curl` in the bot image and verify it during the image build. In the Codex capability instruction, require owner-requested public file downloads to use `curl` with a literal HTTPS URL so the sandbox can identify the exact hostname and route the existing current-turn approval to Telegram. Keep Python network clients, generic escalation, non-HTTPS destinations, IP literals, and private/local hosts blocked.

Rename the normal final activity state from `Completed.` to `Response sent.`. This status confirms message delivery without claiming that the user's requested task succeeded; the substantive final response remains authoritative.

## Consequences

Public asset downloads can use the existing owner-reviewed, exact-host boundary without adding a new credential or broadly enabling network access. Redirects to another hostname may require a separate approval and remain subject to sandbox policy. End-to-end acceptance requires rebuilding the bot and approving a real literal-URL `curl` request from Telegram.
