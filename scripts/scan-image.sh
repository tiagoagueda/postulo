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
# **Three outcomes, told apart by exit code and by `$OUT/verdict.txt`.**
#
#   0  nothing fixable -- the image may be published
#   1  fixable findings at $SEVERITY or above -- the gate
#   2  the scan did not complete -- a scanner that failed to run, a report that came back
#      empty. Not a finding, and it must not look like one: the first time this ran in CI
#      a plumbing fault made the step red in exactly the way a finding does, and a gate that
#      cannot be told from its own failure is not reporting anything (#192).
#
# The verdict file is removed before anything runs, so a run that dies leaves no verdict
# rather than yesterday's.
#
# Both tools download a vulnerability database, so this needs the network. That is fine for
# CI and worth knowing before anything depends on it at a release with no connection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMAGE="${1:-}"
OUT="${SCAN_OUTPUT_DIR:-$ROOT/.scan}"

# Pinned, because a scanner that changes under you changes what the gate means.
#
# `aquasec/trivy:0.68.0` was pinned here and **does not exist** -- there is no such tag on
# Docker Hub and there never was. Nothing noticed until the dev channel ran this from a
# clean host, because the findings behind #155 and #157 were produced on a machine that
# already had some Trivy pulled, and the pin was never the thing that fetched it (#190).
#
# Check a tag exists before pinning it: a 200 from the registry's tag endpoint.
#   curl -sI https://hub.docker.com/v2/repositories/aquasec/trivy/tags/0.74.0
TRIVY="${TRIVY_IMAGE:-aquasec/trivy:0.74.0}"
# This one is real, and eighteen minor versions behind v0.118.0. Left alone deliberately:
# a newer scanner finds more, which is good and is also a gate that starts failing for
# reasons unrelated to whatever change is being made. Worth bumping on its own.
GRYPE="${GRYPE_IMAGE:-anchore/grype:v0.100.0}"

#: What a fixable finding at or above this level does: stop.
SEVERITY="${SCAN_SEVERITY:-HIGH,CRITICAL}"
GRYPE_SEVERITY="${GRYPE_SEVERITY:-high}"

#: What Trivy exits with when it finds something. Not 1: Trivy exits 1 for its own
#: failures too, and the whole point is telling the two apart.
FOUND=3

mkdir -p "$OUT"
rm -f "$OUT/verdict.txt"

verdict() {
    printf '%s\n' "$1" > "$OUT/verdict.txt"
}

# The scan could not be completed. Says so on stderr, in the verdict, and in the exit code,
# and says nothing about the image -- because nothing is known about it.
incomplete() {
    echo >&2
    echo "The scan did not complete: $1." >&2
    echo "That is not a finding. Nothing here says the image is clean; nothing here says it is not." >&2
    verdict "incomplete: $1"
    exit 2
}

if [ -z "$IMAGE" ]; then
    IMAGE="postulo:scan"
    echo "Building $IMAGE"
    $DOCKER build -f "$ROOT/docker/Dockerfile" -t "$IMAGE" "$ROOT"
fi

# The scanners run as containers and read the image out of the daemon, so nothing has to be
# installed on the machine doing the scanning. The cache volumes are what keep a second run
# from downloading both databases again.
#
# **Reports come back on stdout, never through a bind mount.** A `-v "$OUT:/out"` here is
# resolved by the *daemon*, against the host filesystem -- so where this script itself runs
# inside a container with the socket mounted in, which is how CI runs it, the scanner wrote
# into a directory on the host the caller could not see, and every `cat` after it failed.
# Redirecting stdout writes the file wherever this shell is: the same place on a laptop,
# the right place in CI. Grype was already written this way (#190, #192).
trivy() {
    $DOCKER run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v postulo-trivy-cache:/root/.cache \
        "$TRIVY" "$@"
}

grype() {
    $DOCKER run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v postulo-grype-cache:/root/.cache/grype \
        "$GRYPE" "$@"
}

echo
echo "== Everything either tool can see, fixable or not =="
# Informational: the gate below is what decides, so a hiccup here is reported, not fatal.
trivy image --scanners vuln,secret,misconfig --format table "$IMAGE" \
    > "$OUT/trivy-full.txt" || echo "(the full report did not complete; the gate below still decides)"
cat "$OUT/trivy-full.txt" || true

echo
echo "== A bill of materials, so somebody can scan this again next year =="
# Against a database that does not exist yet, which is more use to somebody self-hosting
# Postulo than today's verdict on today's image. The release keeps it as an artifact, so a
# release without one is a scan that did not complete.
trivy image --format cyclonedx "$IMAGE" > "$OUT/sbom.cdx.json" \
    || incomplete "trivy could not write the bill of materials"
[ -s "$OUT/sbom.cdx.json" ] || incomplete "the bill of materials came back empty"
echo "wrote $OUT/sbom.cdx.json"

echo
echo "== The gate: findings with a fix available =="
failed=0

# One run: the report and the verdict from the same scan, told apart by the exit code.
status=0
trivy image --scanners vuln --ignore-unfixed --severity "$SEVERITY" --exit-code "$FOUND" \
    --format table "$IMAGE" > "$OUT/trivy-fixable.txt" || status=$?
cat "$OUT/trivy-fixable.txt"
case "$status" in
    0) ;;
    "$FOUND") failed=1 ;;
    *) incomplete "trivy exited $status" ;;
esac

# Grype exits 1 for findings and 1 for its own failures, and has no flag to change that.
# The difference is whether it produced a report: a table on stdout is findings; nothing on
# stdout with a non-zero exit is a scanner that did not run.
status=0
grype "$IMAGE" --only-fixed --fail-on "$GRYPE_SEVERITY" -o table > "$OUT/grype-fixable.txt" \
    || status=$?
cat "$OUT/grype-fixable.txt"
if [ "$status" -ne 0 ]; then
    if [ -s "$OUT/grype-fixable.txt" ]; then
        failed=1
    else
        incomplete "grype exited $status with no report"
    fi
fi

echo
if [ "$failed" -ne 0 ]; then
    verdict "findings"
    echo "Fixable findings at $SEVERITY or above. Reports in $OUT."
    exit 1
fi
verdict "clean"
echo "Nothing fixable at $SEVERITY or above. Reports in $OUT."
