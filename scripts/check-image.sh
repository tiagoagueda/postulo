#!/usr/bin/env bash
# Build the container image and confirm it starts and answers.
#
# This lives here rather than in continuous integration because building an image needs
# the runner to have a Docker daemon it is allowed to talk to, which forgejo-runner does
# not provide by default. Run it wherever you do have Docker — it is exactly what a CI
# job would do.
#
# Plenty of machines have Docker installed but do not put their user in the docker group,
# which is a defensible choice given that the group is effectively root. Set DOCKER to
# whatever reaches your daemon:
#
#   DOCKER="sudo docker" ./scripts/check-image.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:-postulo:check}"
NAME="postulo-image-check"
# The second container: run as a deployment runs it, and judged by Docker's own check.
DEPLOYED="postulo-image-check-deployed"
DOCKER="${DOCKER:-docker}"

cleanup() { $DOCKER rm -f "$NAME" "$DEPLOYED" >/dev/null 2>&1 || true; }
trap cleanup EXIT

a_secret_key() { echo "check-only-$(head -c 32 /dev/urandom | base64 | tr -d '=+/')"; }

# The PostgreSQL compose file, read the way an operator runs it: from the checkout, with
# only the .env at its root holding the password (#582). It used to stop with "required
# variable POSTGRES_PASSWORD is missing" because Compose looked in docker/.env.
echo "Reading the PostgreSQL compose file"
made_env=""
if [ ! -e "$ROOT/.env" ]; then
    echo "POSTGRES_PASSWORD=check/only?%41" > "$ROOT/.env"
    made_env="yes"
fi
(cd "$ROOT" && $DOCKER compose -f docker/compose.postgres.yml config --quiet)
[ -z "$made_env" ] || rm -f "$ROOT/.env"

echo "Building $TAG"
# With the moment, so the layer that takes Debian's updates runs and this checks the image
# a build would make now (#300).
$DOCKER build --pull --build-arg POSTULO_APT_REFRESH="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    -f "$ROOT/docker/Dockerfile" -t "$TAG" "$ROOT"

echo "Starting it"
$DOCKER run -d --name "$NAME" -p 8000:8000 \
    -e POSTULO_SECRET_KEY="$(a_secret_key)" \
    -e POSTULO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    -e POSTULO_SSL_REDIRECT=false \
    "$TAG" >/dev/null

echo -n "Waiting for it to answer"
answered=""
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/healthz 2>/dev/null | grep -q '"status": "ok"'; then
        answered="yes"
        break
    fi
    echo -n "."
    sleep 2
done
echo

if [ -z "$answered" ]; then
    echo "It did not become healthy. Its log:" >&2
    $DOCKER logs "$NAME" >&2
    exit 1
fi
echo "The image builds, starts, migrates and answers its health check."

# Then as an operator runs it: answering to its own name and nothing else, nothing
# published, the redirect left on, and judged by Docker's own health check rather than by
# a request from this side. That check asks 127.0.0.1 from inside the container, and it
# used to be refused with a 400 the moment POSTULO_ALLOWED_HOSTS stopped naming 127.0.0.1:
# a container serving pages was reported unhealthy, and the scheduler and worker, which
# wait for a healthy one, never started (#580). The run above cannot see that, because it
# allows the very host the check uses.
echo "Starting it as a deployment would, answering only to its own name"
$DOCKER run -d --name "$DEPLOYED" \
    -e POSTULO_SECRET_KEY="$(a_secret_key)" \
    -e POSTULO_ALLOWED_HOSTS=postulo.example.org \
    "$TAG" >/dev/null

echo -n "Waiting for Docker to call it healthy"
# The check waits twenty seconds and then asks every thirty, behind the migrations and
# whatever a first start downloads, so this is given three minutes.
state=""
for _ in $(seq 1 90); do
    state="$($DOCKER inspect --format '{{.State.Health.Status}}' "$DEPLOYED" 2>/dev/null || true)"
    case "$state" in
        healthy)
            echo
            echo "Docker's own health check passes with only the public host allowed."
            exit 0
            ;;
        unhealthy) break ;;
    esac
    echo -n "."
    sleep 2
done

echo
echo "Docker did not call it healthy (it said: ${state:-nothing}). Its log:" >&2
$DOCKER logs "$DEPLOYED" >&2
exit 1
