"""Persistent deployment controller that survives bot replacement."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import time

from deployment import DeploymentBusy, DeploymentManifest, DeploymentQueue, deployment_lock, run_deployment


LOGGER = logging.getLogger(__name__)


def run_once(queue: DeploymentQueue) -> bool:
    request = None
    release_request = True
    try:
        with deployment_lock(queue.state_dir):
            request = queue.claim()
            if request is None:
                return False
            LOGGER.info("deployment started deployment_id=%s commit=%s", request["deployment_id"], request["commit"])
            result = run_deployment(
                request,
                compose_file=os.environ["DEPLOY_COMPOSE_FILE"],
                project_name=os.environ["DEPLOY_PROJECT_NAME"],
                bot_image=os.environ["BOT_IMAGE"],
                repository=os.environ.get("SELF_REPOSITORY_PATH", "/workspace/personal-agent"),
                state_dir=str(queue.state_dir),
            )
            LOGGER.info("deployment finished deployment_id=%s status=%s", request["deployment_id"], result.get("status"))
    except DeploymentBusy:
        return False
    except Exception as exc:
        LOGGER.exception("deployment controller failed deployment_id=%s", request.get("deployment_id") if request else "unknown")
        try:
            DeploymentManifest(queue.state_dir).transition("failed", error=str(exc))
        except OSError:
            # Preserve the active request if durable state cannot be written.
            # Container restart can then resume it after storage recovers.
            release_request = False
            LOGGER.exception("deployment failure could not be persisted; retaining active request")
            raise
    finally:
        if request is not None and release_request:
            queue.finish()
    return request is not None


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in ("HOST_WORKSPACE_DIR", "GIT_SSH_KEY_PATH", "GIT_KNOWN_HOSTS_PATH"):
        if not Path(os.environ.get(name, "")).is_absolute():
            raise RuntimeError(f"{name} must be an absolute host path for self-deployment")
    queue = DeploymentQueue(os.environ.get("DEPLOYMENT_STATE_DIR", "/workspace/.personal-agent-state"))
    LOGGER.info("deployer starting")
    while True:
        queue.heartbeat()
        if not run_once(queue):
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
