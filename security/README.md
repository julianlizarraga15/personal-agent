# Codex container seccomp profile

`codex-bwrap-seccomp.json` is an x86-64 profile derived from Moby's
`seccomp/v0.2.1` default profile. It retains an allowlist default and adds only
the namespace and mount operations required for Codex's inner Bubblewrap
sandbox. The bot does not receive `SYS_ADMIN`, privileged mode, unconfined
seccomp, or unconfined AppArmor.

Review the profile against the active Moby default before changing Docker
Engine versions. Validate it with `codex sandbox pwd` inside the built bot
image and with the repository's negative security tests.
