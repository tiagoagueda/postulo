"""What a release needs, in one file the workflow and a person can both run.

    python scripts/release_tools.py check v0.2.0             # tag, versions and changelog agree
    python scripts/release_tools.py notes 0.2.0 -o notes.md  # that version's changelog section
    python scripts/release_tools.py publish v0.2.0 dist/*    # a Forgejo release with those files

Standard library only, so it runs on the runner's Python without installing anything.
``publish`` talks to the Forgejo API with a token from ``FORGEJO_TOKEN``; the server and
repository come from ``FORGEJO_URL`` and ``FORGEJO_REPOSITORY`` (``owner/name``), which
Forgejo Actions set as ``GITHUB_SERVER_URL`` and ``GITHUB_REPOSITORY``.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ReleaseError(Exception):
    pass


# ------------------------------------------------------------------- versions


def version_of_tag(tag: str) -> str:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.]+)?", tag):
        raise ReleaseError(f"{tag!r} is not a release tag; expected vX.Y.Z.")
    return tag[1:]


def pyproject_version(root: Path = ROOT) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ReleaseError("pyproject.toml has no version.")
    return match.group(1)


def package_version(root: Path = ROOT) -> str:
    text = (root / "src" / "postulo" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ReleaseError("src/postulo/__init__.py has no __version__.")
    return match.group(1)


def changelog_section(version: str, root: Path = ROOT) -> str:
    """The body of ``## [version] — date`` in CHANGELOG.md, without the heading."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(
        rf"^## \[{re.escape(version)}\](?:\s*[—-]\s*\d{{4}}-\d{{2}}-\d{{2}})?\s*$", re.MULTILINE
    )
    match = heading.search(text)
    if not match:
        raise ReleaseError(
            f"CHANGELOG.md has no '## [{version}]' section. Write it before tagging."
        )
    rest = text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = rest[: following.start()] if following else rest
    body = body.strip()
    if not body:
        raise ReleaseError(f"The CHANGELOG.md section for {version} is empty.")
    return body + "\n"


def check(tag: str, root: Path = ROOT) -> str:
    """Everything that must agree before a release is made. Returns the version."""
    version = version_of_tag(tag)
    declared = pyproject_version(root)
    if declared != version:
        raise ReleaseError(f"Tag {tag} but pyproject.toml says {declared}.")
    packaged = package_version(root)
    if packaged != version:
        raise ReleaseError(f"Tag {tag} but src/postulo/__init__.py says {packaged}.")
    changelog_section(version, root)
    return version


# ------------------------------------------------------------------- publishing


def _api(
    method: str,
    url: str,
    token: str,
    body: bytes | None = None,
    content_type: str = "application/json",
):
    # The operator's own Forgejo, over https; never a file: or custom scheme.
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310
    request.add_header("Authorization", f"token {token}")
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - the operator's own server
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise ReleaseError(f"{method} {url} -> {error.code}: {detail}") from error


def publish(
    tag: str, assets: list[Path], *, server: str, repository: str, token: str, notes: str
) -> dict:
    """Create the release for ``tag`` (or find it) and attach ``assets``."""
    version = version_of_tag(tag)
    base = f"{server.rstrip('/')}/api/v1/repos/{repository}"
    existing = None
    try:
        existing = _api("GET", f"{base}/releases/tags/{urllib.parse.quote(tag)}", token)
    except ReleaseError as error:
        if "404" not in str(error):
            raise
    if existing is None:
        payload = json.dumps(
            {
                "tag_name": tag,
                "name": f"Postulo {version}",
                "body": notes,
                "draft": False,
                "prerelease": "-" in version or "+" in version,
            }
        ).encode("utf-8")
        existing = _api("POST", f"{base}/releases", token, payload)
    release_id = existing["id"]
    already = {asset["name"] for asset in existing.get("assets") or []}

    for path in assets:
        if path.name in already:
            continue
        boundary = uuid.uuid4().hex
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="attachment"; '
                f'filename="{path.name}"\r\nContent-Type: {kind}\r\n\r\n'
            ).encode()
            + path.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        _api(
            "POST",
            f"{base}/releases/{release_id}/assets?name={urllib.parse.quote(path.name)}",
            token,
            body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
    return existing


# ------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    checker = commands.add_parser(
        "check", help="Make sure the tag, the versions and the changelog agree."
    )
    checker.add_argument("tag")

    noter = commands.add_parser("notes", help="Print that version's changelog section.")
    noter.add_argument("version")
    noter.add_argument("-o", "--output", type=Path)

    publisher = commands.add_parser("publish", help="Create the Forgejo release and attach files.")
    publisher.add_argument("tag")
    publisher.add_argument("assets", nargs="*", type=Path)
    publisher.add_argument("--notes", type=Path, help="A file holding the release notes.")

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            version = check(args.tag)
            print(f"{args.tag}: pyproject.toml, __version__ and CHANGELOG.md all say {version}.")
        elif args.command == "notes":
            body = changelog_section(args.version)
            if args.output:
                args.output.write_text(body, encoding="utf-8")
            else:
                sys.stdout.write(body)
        elif args.command == "publish":
            server = os.environ.get("FORGEJO_URL") or os.environ.get("GITHUB_SERVER_URL", "")
            repository = os.environ.get("FORGEJO_REPOSITORY") or os.environ.get(
                "GITHUB_REPOSITORY", ""
            )
            token = os.environ.get("FORGEJO_TOKEN", "")
            if not (server and repository and token):
                raise ReleaseError("Set FORGEJO_URL, FORGEJO_REPOSITORY and FORGEJO_TOKEN.")
            notes = (
                args.notes.read_text(encoding="utf-8")
                if args.notes
                else changelog_section(version_of_tag(args.tag))
            )
            release = publish(
                args.tag,
                args.assets,
                server=server,
                repository=repository,
                token=token,
                notes=notes,
            )
            print(f"Release {release.get('name')}: {release.get('html_url')}")
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
