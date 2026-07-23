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

if docker image inspect "$bot_image" >/dev/null 2>&1; then
    base_image=${bot_image%:*}
    rollback_image="${base_image}:rollback-$(date -u +%Y%m%d%H%M%S)"
    docker tag "$bot_image" "$rollback_image"
    echo "previous image saved as $rollback_image"
fi

docker compose -f "$compose_file" -p "$project_name" build bot
docker compose -f "$compose_file" -p "$project_name" up -d --no-build --force-recreate bot
echo "bot service rebuilt and recreated"
