"""One-time ChatGPT device-code login for the persistent CODEX_HOME volume."""

from __future__ import annotations

from openai_codex import Codex


def main() -> int:
    with Codex() as codex:
        login = codex.login_chatgpt_device_code()
        print(f"Open {login.verification_url}")
        print(f"Enter code: {login.user_code}")
        result = login.wait()
        if not result.success:
            print("Codex login did not complete successfully.")
            return 1
    print("Codex login complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
