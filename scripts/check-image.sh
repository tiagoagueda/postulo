#!/usr/bin/env bash
# Build the container image and confirm it starts and answers.
#
# This lives here rather than in continuous integration because building an image needs
# the runner to have a Docker daemon it is allowed to talk to, which forgejo-runner does
# not provide by default. Run it wherever you do have Docker — it is exactly what a CI
# job would do.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:-postulo:check}"
NAME="postulo-image-check"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "Building $TAG"
docker build -f "$ROOT/docker/Dockerfile" -t "$TAG" "$ROOT"

echo "Starting it"
docker run -d --name "$NAME" -p 8000:8000 \
    -e POSTULO_SECRET_KEY="check-only-$(head -c 32 /dev/urandom | base64 | tr -d '=+/')" \
    -e POSTULO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    -e POSTULO_SSL_REDIRECT=false \
    "$TAG" >/dev/null

echo -n "Waiting for it to answer"
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/healthz 2>/dev/null | grep -q '"status": "ok"'; then
        echo
        echo "The image builds, starts, migrates and answers its health check."
        exit 0
    fi
    echo -n "."
    sleep 2
done

echo
echo "It did not become healthy. Its log:" >&2
docker logs "$NAME" >&2
exit 1
