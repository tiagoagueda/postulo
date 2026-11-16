"""Decide whether every fixable finding of an image scan is waived (`docker/scan-waivers.txt`).

    python3 scripts/scan_waivers.py trivy|grype REPORT.json [--waivers FILE] [--severity high]

The scan gate (`scripts/scan-image.sh`) fails on a finding somebody can fix. Sometimes the
fix exists upstream and nobody can take it yet: CPython releases 3.14.11, and until the
official `python:3.14-slim` image carries it the gate is red on every build for a reason
no change here can act on. A waiver names that one finding, says why, and **expires**: past
its date it is read as if it were not there, so the gate goes red again by itself and the
waiver cannot outlive the reason it was written for.

Exit 0 when every finding in the report is waived (each waived one is listed), 1 when any
is not (those are listed), 2 when the report or the waiver file cannot be read -- the same
three outcomes as the scan itself (#192).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAIVERS = ROOT / "docker" / "scan-waivers.txt"
RANK = {"negligible": 0, "unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def read_waivers(path: Path, today: dt.date) -> tuple[dict[str, str], list[str]]:
    """The waivers still in force, and the expired ones (said aloud, then ignored)."""
    active: dict[str, str] = {}
    expired: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            raise ValueError(f"{path.name}:{number}: expected 'ID YYYY-MM-DD reason'")
        finding, until, reason = parts
        if dt.date.fromisoformat(until) < today:
            expired.append(f"{finding} (expired {until})")
        else:
            active[finding] = f"until {until}: {reason}"
    return active, expired


def trivy_findings(report: dict) -> list[tuple[str, str]]:
    """The scan asked Trivy for fixable findings at the gate's severity already."""
    return [
        (v["VulnerabilityID"], f"{v.get('PkgName')} {v.get('InstalledVersion')}")
        for result in report.get("Results") or []
        for v in result.get("Vulnerabilities") or []
    ]


def grype_findings(report: dict, severity: str) -> list[tuple[str, str]]:
    """Grype's JSON holds every match; the gate is fixed ones at or above `severity`."""
    floor = RANK[severity.lower()]
    found = []
    for match in report.get("matches") or []:
        vuln = match.get("vulnerability") or {}
        fixed = (vuln.get("fix") or {}).get("state") == "fixed"
        if fixed and RANK.get(str(vuln.get("severity", "")).lower(), 0) >= floor:
            art = match.get("artifact") or {}
            found.append((vuln["id"], f"{art.get('name')} {art.get('version')}"))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tool", choices=("trivy", "grype"))
    parser.add_argument("report", type=Path)
    parser.add_argument("--waivers", type=Path, default=WAIVERS)
    parser.add_argument("--severity", default="high")
    today = dt.datetime.now(dt.UTC).date()
    parser.add_argument("--today", type=dt.date.fromisoformat, default=today)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        active, expired = ({}, [])
        if args.waivers.exists():
            active, expired = read_waivers(args.waivers, args.today)
    except (OSError, ValueError) as error:
        print(f"Cannot read the waivers or the {args.tool} report: {error}", file=sys.stderr)
        return 2
    if args.tool == "trivy":
        findings = trivy_findings(report)
    else:
        findings = grype_findings(report, args.severity)
    for finding in expired:
        print(f"{args.tool}: waiver no longer in force: {finding}")
    left = [(fid, what) for fid, what in findings if fid not in active]
    for fid, what in findings:
        if fid in active:
            print(f"{args.tool}: waived {fid} ({what}) {active[fid]}")
    for fid, what in left:
        print(f"{args.tool}: NOT waived {fid} ({what})")
    if not findings:
        print(f"{args.tool}: the JSON report holds no fixable finding")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
