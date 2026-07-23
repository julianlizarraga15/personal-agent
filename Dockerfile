FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src ./src

ENTRYPOINT ["python", "-m", "src.worker"]
