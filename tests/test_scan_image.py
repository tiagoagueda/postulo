"""`scripts/scan-image.sh` tells a finding from its own failure (#192).

The first time the scan ran in CI, a plumbing fault -- reports written where the job could
not read them -- made the step red in exactly the way a finding does, and nothing could tell
the two apart. The script has three outcomes now, and this drives all of them.

The scanners are a fake `docker` that answers the way the real ones do: Trivy exits with
whatever `--exit-code` it was given when it finds something and 1 when it breaks, Grype
exits 1 for both and the difference is whether a report came out. So what is tested is the
script's *reading* of the scanners -- exit code, verdict file, what is said -- and not the
scanners.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "scan-image.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="no bash on this machine")

#: The scanners, as the script sees them: `docker run --rm -v … <image> <args…>`. `FAKE_SCAN`
#: says how the world is; the script inherits the environment, so the test sets it.
FAKE_DOCKER = r"""#!/usr/bin/env bash
set -u
world="${FAKE_SCAN:-clean}"
shift                                   # run
while [ $# -gt 0 ] && [ "${1#-}" != "$1" ]; do
    case "$1" in -v) shift 2 ;; *) shift ;; esac
done
tool="$1"; shift
args=" $* "

case "$tool" in
    *trivy*)
        found=0
        while [ $# -gt 0 ]; do
            [ "$1" = "--exit-code" ] && found="$2"
            shift
        done
        if [ "$world" = trivy-broken ]; then
            echo "FATAL: could not download the database" >&2
            exit 1
        fi
        case "$args" in
            *cyclonedx*)
                [ "$world" = sbom-empty ] || echo '{"bomFormat": "CycloneDX"}'
                ;;
            *"--format json"*)
                echo '{"Results": [{"Vulnerabilities": [{"VulnerabilityID": "CVE-2026-0001",
                      "Severity": "HIGH", "PkgName": "msgpack", "InstalledVersion": "1.1.2",
                      "FixedVersion": "1.2.1"}]}]}'
                ;;
            *--ignore-unfixed*)
                if [ "$world" = findings ] || [ "$world" = trivy-waived ]; then
                    echo "CVE-2026-0001  HIGH  msgpack 1.1.2 -> 1.2.1"
                    exit "$found"
                fi
                echo "(no fixable findings)"
                ;;
            *)
                echo "everything, fixable or not"
                ;;
        esac
        ;;
    *grype*)
        case "$world" in
            grype-broken)
                echo "error: failed to load vulnerability db" >&2
                exit 1
                ;;
            grype-findings|grype-waived)
                case "$args" in
                    *"-o json"*)
                        echo '{"matches": [
                          {"vulnerability": {"id": "GHSA-0001", "severity": "High",
                             "fix": {"state": "fixed"}},
                           "artifact": {"name": "libx", "version": "1.0"}},
                          {"vulnerability": {"id": "GHSA-0002", "severity": "Medium",
                             "fix": {"state": "fixed"}},
                           "artifact": {"name": "liby", "version": "2.0"}}]}'
                        ;;
                    *)
                        echo "NAME  INSTALLED  FIXED-IN  SEVERITY"
                        echo "libx  1.0        1.1       High"
                        exit 1
                        ;;
                esac
                ;;
            *) echo "No vulnerabilities found" ;;
        esac
        ;;
    *)
        echo "unexpected tool: $tool" >&2
        exit 99
        ;;
esac
"""


@pytest.fixture
def scan(tmp_path):
    """Run the script against a world, returning (returncode, stdout, stderr, out_dir)."""
    fake = tmp_path / "fake-docker.sh"
    fake.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    out = tmp_path / "out"

    def run(world: str, waivers: str = "") -> tuple[int, str, str, Path]:
        waiver_file = tmp_path / "waivers.txt"
        waiver_file.write_text(waivers, encoding="utf-8")
        env = {
            **os.environ,
            "FAKE_SCAN": world,
            "SCAN_WAIVERS": waiver_file.as_posix(),
            "PYTHON": Path(sys.executable).as_posix(),
            # Word-split by the script into `sh <path>`, so the file's mode is irrelevant.
            # Not bash's own path: on Windows that is `C:\Program Files\...`, and the space
            # splits it into a command that does not exist -- which the script then
            # reported, correctly, as a scan that did not complete.
            "DOCKER": f"sh {fake.as_posix()}",
            "SCAN_OUTPUT_DIR": out.as_posix(),
        }
        done = subprocess.run(  # noqa: S603 - a fixed argument list, bash resolved above
            [BASH, SCRIPT.as_posix(), "postulo:test"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        return done.returncode, done.stdout, done.stderr, out

    return run


def verdict(out: Path) -> str:
    return (out / "verdict.txt").read_text(encoding="utf-8").strip()


def test_a_clean_image_is_exit_zero_with_every_report(scan):
    code, stdout, _, out = scan("clean")

    assert code == 0, stdout
    assert verdict(out) == "clean"
    assert "Nothing fixable" in stdout
    for report in ("trivy-full.txt", "sbom.cdx.json", "trivy-fixable.txt", "grype-fixable.txt"):
        assert (out / report).stat().st_size > 0, f"{report} is missing or empty"


def test_trivy_findings_are_exit_one(scan):
    code, stdout, _, out = scan("findings")

    assert code == 1
    assert verdict(out) == "findings"
    assert "Fixable findings" in stdout
    assert "CVE-2026-0001" in stdout, "the report is shown, not only the verdict"


def test_grype_findings_are_exit_one_even_though_grype_says_one_for_everything(scan):
    code, _, _, out = scan("grype-findings")

    assert code == 1
    assert verdict(out) == "findings"


@pytest.mark.parametrize(
    ("world", "names"),
    [
        ("trivy-broken", "trivy"),
        ("grype-broken", "grype"),
        ("sbom-empty", "bill of materials"),
    ],
)
def test_a_scanner_that_did_not_run_is_exit_two_and_not_a_finding(scan, world, names):
    """The point of #192: a plumbing failure must not impersonate a finding."""
    code, stdout, stderr, out = scan(world)

    assert code == 2, stdout + stderr
    assert verdict(out).startswith("incomplete:")
    assert names in verdict(out)
    assert "did not complete" in stderr
    assert "Fixable findings" not in stdout
    assert "Nothing fixable" not in stdout, "an incomplete scan says nothing about the image"


def test_a_run_that_dies_leaves_no_stale_verdict(scan, tmp_path):
    """Yesterday's `clean` must not survive a run that did not get as far as a verdict."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "verdict.txt").write_text("clean\n", encoding="utf-8")

    _, _, _, out = scan("trivy-broken")

    assert verdict(out) != "clean"
    assert verdict(out).startswith("incomplete:")


def test_the_reports_never_travel_through_a_bind_mount():
    """The root cause: `-v "$OUT:/out"` is resolved by the daemon against the host, so in CI
    the scanners wrote where the job could not read. Every report is a redirect now."""
    # The comments explain the old mistake by quoting it; only the code is held to this.
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    code = [line for line in lines if not line.lstrip().startswith("#")]
    for line in code:
        assert '"$OUT:' not in line and "$OUT:/" not in line, line
    for report in ("trivy-full.txt", "sbom.cdx.json", "trivy-fixable.txt", "grype-fixable.txt"):
        redirect = f'> "$OUT/{report}"'
        assert any(redirect in line for line in code), f"{report} is not written by a redirect"


#: A waiver in force for each scanner's fake finding, and one long expired.
IN_FORCE = (
    "CVE-2026-0001 2999-01-01 fixed upstream, not in the base image\nGHSA-0001 2999-01-01 same\n"
)
EXPIRED = "CVE-2026-0001 2000-01-01 long gone\nGHSA-0001 2000-01-01 long gone\n"


@pytest.mark.parametrize("world", ["trivy-waived", "grype-waived"])
def test_a_finding_waived_until_a_later_date_passes(scan, world):
    code, stdout, stderr, out = scan(world, IN_FORCE)

    assert code == 0, stdout + stderr
    assert verdict(out) == "clean"
    assert "waived" in stdout


@pytest.mark.parametrize("world", ["trivy-waived", "grype-waived"])
def test_an_expired_waiver_fails_the_gate_again(scan, world):
    """A waiver cannot outlive its reason: past its date the finding is red by itself."""
    code, stdout, _, out = scan(world, EXPIRED)

    assert code == 1
    assert verdict(out) == "findings"
    assert "no longer in force" in stdout


def test_a_waiver_for_something_else_waives_nothing(scan):
    code, stdout, _, _ = scan("trivy-waived", "CVE-1999-9999 2999-01-01 another thing\n")

    assert code == 1
    assert "NOT waived CVE-2026-0001" in stdout


def test_an_unreadable_waiver_file_is_an_incomplete_scan(scan):
    code, _, stderr, out = scan("trivy-waived", "CVE-2026-0001 someday\n")

    assert code == 2, stderr
    assert verdict(out).startswith("incomplete:")


def test_the_committed_waivers_all_expire():
    """Every line of the real file names a date, so none is forever."""
    import datetime as dt

    waivers = REPO / "docker" / "scan-waivers.txt"
    for line in waivers.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            finding, until, reason = line.split(None, 2)
            assert dt.date.fromisoformat(until), finding
            assert reason
