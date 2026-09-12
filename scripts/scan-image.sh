#!/usr/bin/env bash
# Scan the container image, with both scanners, and fail only on what can be fixed.
#
# The findings that produced #155 and #157 were found by running these two by hand on a
# machine that happened to have them pulled. Neither was new; both had been in the image
# since it was built. This is that scan, written down, so the same thing is done the same
# way by a person and by `image.yml` (#156).
#
#   ./scripts/scan-image.sh                     # builds postulo:scan and scans it
#   ./scripts/scan-image.sh ghcr.io/x/y:1.2.3   # scans an image that already exists
#   DOCKER="sudo docker" ./scripts/scan-image.sh
#
# **Both tools, because they disagree usefully.** On the image that started this, Grype
# found the only actionable Debian update, which Trivy did not mark fixable; Trivy found
# the Python packages and the leftover uv cache, which Grype's deb-and-binary scan did not
# see at all. Running one and believing it would have missed half of it.
#
# **The gate is fixable findings, not severe ones.** Six CRITICALs with no fix available is
# the normal state of a Debian base image. A pipeline that fails on `--severity CRITICAL`
# fails every day for reasons nobody can act on, and is switched off within a fortnight. So
# `--ignore-unfixed` and `--only-fixed`: a failure here is always something somebody can do
# something about, which is what makes it worth stopping for.
#
# **The unfixable half is still reported**, along with the secret and misconfiguration scans
# that came back clean. A scan that records only what it failed on throws away the half that
# says the image is in the state you think it is.
#
# Both tools download a vulnerability database, so this needs the network. That is fine for
# CI and worth knowing before anything depends on it at a release with no connection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMAGE="${1:-}"
OUT="${SCAN_OUTPUT_DIR:-$ROOT/.scan}"

# Pinned, because a scanner that changes under you changes what the gate means.
TRIVY="${TRIVY_IMAGE:-aquasec/trivy:0.68.0}"
GRYPE="${GRYPE_IMAGE:-anchore/grype:v0.100.0}"

#: What a fixable finding at or above this level does: stop.
SEVERITY="${SCAN_SEVERITY:-HIGH,CRITICAL}"
GRYPE_SEVERITY="${GRYPE_SEVERITY:-high}"

mkdir -p "$OUT"

if [ -z "$IMAGE" ]; then
    IMAGE="postulo:scan"
    echo "Building $IMAGE"
    $DOCKER build -f "$ROOT/docker/Dockerfile" -t "$IMAGE" "$ROOT"
fi

# The scanners run as containers and read the image out of the daemon, so nothing has to be
# installed on the machine doing the scanning. The cache volumes are what keep a second run
# from downloading both databases again.
trivy() {
    $DOCKER run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v postulo-trivy-cache:/root/.cache \
        -v "$OUT:/out" \
        "$TRIVY" "$@"
}

grype() {
    $DOCKER run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v postulo-grype-cache:/root/.cache/grype \
        -v "$OUT:/out" \
        "$GRYPE" "$@"
}

echo
echo "== Everything either tool can see, fixable or not =="
trivy image --scanners vuln,secret,misconfig --format table \
    --output /out/trivy-full.txt "$IMAGE" || true
cat "$OUT/trivy-full.txt" || true

echo
echo "== A bill of materials, so somebody can scan this again next year =="
# Against a database that does not exist yet, which is more use to somebody self-hosting
# Postulo than today's verdict on today's image.
trivy image --format cyclonedx --output /out/sbom.cdx.json "$IMAGE"
echo "wrote $OUT/sbom.cdx.json"

echo
echo "== The gate: findings with a fix available =="
failed=0

trivy image --scanners vuln --ignore-unfixed --severity "$SEVERITY" \
    --format table --output /out/trivy-fixable.txt "$IMAGE"
cat "$OUT/trivy-fixable.txt"
trivy image --scanners vuln --ignore-unfixed --severity "$SEVERITY" \
    --exit-code 1 --quiet "$IMAGE" || failed=1

grype "$IMAGE" --only-fixed --fail-on "$GRYPE_SEVERITY" -o table > "$OUT/grype-fixable.txt" \
    || failed=1
cat "$OUT/grype-fixable.txt"

echo
if [ "$failed" -ne 0 ]; then
    echo "Fixable findings at $SEVERITY or above. Reports in $OUT."
    exit 1
fi
echo "Nothing fixable at $SEVERITY or above. Reports in $OUT."
