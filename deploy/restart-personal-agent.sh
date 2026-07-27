#!/bin/sh
set -eu

if [ "$#" -ne 0 ]; then
    echo "this deployment helper accepts no arguments" >&2
    exit 2
fi

compose_file=${DEPLOY_COMPOSE_FILE:?DEPLOY_COMPOSE_FILE is required}
project_name=${DEPLOY_PROJECT_NAME:?DEPLOY_PROJECT_NAME is required}
bot_image=${BOT_IMAGE:?BOT_IMAGE is required}

case "$compose_file" in
    /workspace/personal-agent/docker-compose.yml) ;;
    *) echo "refusing an unexpected Compose file" >&2; exit 2 ;;
esac

case "$bot_image" in
    personal-agent-bot:*) ;;
    *) echo "refusing an unexpected bot image" >&2; exit 2 ;;
esac

exec python -m deployment
