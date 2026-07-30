"""Launch the bundled Codex app-server without inheriting bot-only secrets."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from codex_cli_bin import bundled_codex_path, bundled_path_dir


SAFE_ENV_KEYS = {
    "CODEX_HOME",
    "LANG",
    "LC_ALL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TZ",
}


def sanitized_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return the small environment the app-server and its commands may inherit."""

    source = os.environ if environ is None else environ
    clean = {key: source[key] for key in SAFE_ENV_KEYS if source.get(key)}
    clean["CODEX_HOME"] = source.get("CODEX_HOME", "/codex-home")
    clean["HOME"] = "/tmp/codex-runtime-home"
    path_dir = bundled_path_dir()
    path_parts = [str(path_dir)] if path_dir is not None else []
    path_parts.extend(["/usr/local/bin", "/usr/local/sbin", "/usr/bin", "/usr/sbin", "/bin", "/sbin"])
    clean["PATH"] = os.pathsep.join(path_parts)
    clean["TMPDIR"] = "/tmp"
    return clean


def main() -> None:
    """Replace this process with the pinned Codex runtime and a clean environment."""

    Path("/tmp/codex-runtime-home").mkdir(mode=0o700, parents=True, exist_ok=True)
    codex = bundled_codex_path()
    os.execve(str(codex), [str(codex), *sys.argv[1:]], sanitized_environment())


if __name__ == "__main__":
    main()
