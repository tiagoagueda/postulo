"""What a release needs, in one file the workflow and a person can both run.

    python scripts/release_tools.py check v0.2.0             # tag, versions and changelog agree
    python scripts/release_tools.py check v0.2.0 --ci        # ...and CI passed on that commit
    python scripts/release_tools.py notes 0.2.0 -o notes.md  # that version's changelog section
    python scripts/release_tools.py publish v0.2.0 dist/*    # a Forgejo release with those files
    python scripts/release_tools.py attach v0.2.0 sbom.json  # one more file on that release
    python scripts/release_tools.py verify-image v0.2.0 --registry host --image owner/name

Standard library only, so it runs on the runner's Python without installing anything -- and
the docker runner's Python is Ubuntu's 3.10, so nothing newer is used. ``publish`` and
``attach`` talk to the Forgejo API with a token from ``FORGEJO_TOKEN``; the server and
repository come from ``FORGEJO_URL`` and ``FORGEJO_REPOSITORY`` (``owner/name``), which
Forgejo Actions set as ``GITHUB_SERVER_URL`` and ``GITHUB_REPOSITORY``. ``verify-image`` asks
the registry itself, the way ``docker pull`` would: with ``FORGEJO_USER`` and
``FORGEJO_TOKEN`` where the image is private, and anonymously otherwise.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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

#: Every architecture a release image is built for, and so every one each tag has to carry.
PLATFORMS = ("linux/amd64", "linux/arm64")

#: Whatever the registry stored the manifest as; the reply says which it was.
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class ReleaseError(Exception):
    pass


# ------------------------------------------------------------------- versions


def version_of_tag(tag: str) -> str:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.]+)?", tag):
        raise ReleaseError(f"{tag!r} is not a release tag; expected vX.Y.Z.")
    return tag[1:]


def image_tags(version: str) -> tuple[str, str, str]:
    """The three tags a release pushes: the version, its minor, and ``latest``.

    The minor is the version with its last dotted part removed, exactly as the workflow
    computes it with ``${version%.*}``, so the two cannot disagree about a pre-release.
    """
    return (version, version.rsplit(".", 1)[0], "latest")


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


#: The CI jobs a release has to have passed, by the names Forgejo Actions records a commit
#: status under: "<workflow> / <job> (<event>)". Every test leg, whichever Pythons the matrix
#: holds this year, and the browser. The combined state is deliberately not used: the dev
#: image job fails on a registry timeout often enough that "failure" there says nothing
#: about the code (#233).
REQUIRED_JOBS = (
    ("a test leg", re.compile(r"^CI / test \(")),
    ("the browser", re.compile(r"^CI / browser ")),
)


def ci_problems(ref: str, *, server: str, repository: str, token: str) -> list[str]:
    """What stands between ``ref`` and a release: every required job not recorded as a success.

    Empty means go. The combined status is asked for by the tag itself, so the answer is
    about the commit the tag points at, whichever branch it was pushed from.
    """
    base = _repository_api(server, repository)
    combined = _api("GET", f"{base}/commits/{urllib.parse.quote(ref, safe='')}/status", token)
    recorded = {
        status.get("context", ""): status.get("status", "")
        for status in (combined or {}).get("statuses") or []
    }
    problems = []
    for what, pattern in REQUIRED_JOBS:
        matching = sorted(context for context in recorded if pattern.match(context))
        if not matching:
            problems.append(f"no CI status for {what} on {ref}; has CI run for this commit?")
        for context in matching:
            if recorded[context] != "success":
                problems.append(f"{context}: {recorded[context]}")
    return problems


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


def _repository_api(server: str, repository: str) -> str:
    return f"{server.rstrip('/')}/api/v1/repos/{repository}"


def find_release(tag: str, *, base: str, token: str) -> dict | None:
    """The release for ``tag``, or None when there is none yet."""
    try:
        return _api("GET", f"{base}/releases/tags/{urllib.parse.quote(tag)}", token)
    except ReleaseError as error:
        if "404" not in str(error):
            raise
        return None


def _attach_missing(release: dict, assets: list[Path], *, base: str, token: str) -> list[str]:
    """Upload each of ``assets`` the release does not already carry; the names uploaded."""
    already = {asset["name"] for asset in release.get("assets") or []}
    uploaded: list[str] = []
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
            f"{base}/releases/{release['id']}/assets?name={urllib.parse.quote(path.name)}",
            token,
            body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        uploaded.append(path.name)
    return uploaded


def publish(
    tag: str, assets: list[Path], *, server: str, repository: str, token: str, notes: str
) -> dict:
    """Create the release for ``tag`` (or find it) and attach ``assets``."""
    version = version_of_tag(tag)
    base = _repository_api(server, repository)
    existing = find_release(tag, base=base, token=token)
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
    _attach_missing(existing, assets, base=base, token=token)
    return existing


def attach(tag: str, assets: list[Path], *, server: str, repository: str, token: str) -> list[str]:
    """Attach files to a release that already exists, and refuse to invent one.

    The image workflow's half of a release: it runs after the release workflow has made the
    release, so a tag with no release is a tag that workflow never saw -- something to say,
    rather than paper over with a bare release nobody wrote notes for (#251). Returns the
    names it uploaded; a file the release already carries is left alone.
    """
    version_of_tag(tag)
    base = _repository_api(server, repository)
    release = find_release(tag, base=base, token=token)
    if release is None:
        raise ReleaseError(
            f"There is no release for {tag}; the release workflow makes one when the tag "
            "is pushed, and this attaches to it rather than inventing one."
        )
    return _attach_missing(release, assets, base=base, token=token)


# ------------------------------------------------------------------- the registry


def _registry_token(registry: str, user: str, token: str) -> str:
    """The bearer the registry's ``/v2/`` wants: anonymous for a public image, or exchanged
    for the user's token where it is not."""
    request = urllib.request.Request(f"https://{registry}/v2/token", method="GET")
    if user and token:
        pair = base64.b64encode(f"{user}:{token}".encode()).decode()
        request.add_header("Authorization", f"Basic {pair}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return str(json.loads(response.read())["token"])
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise ReleaseError(f"GET https://{registry}/v2/token -> {error.code}: {detail}") from error


def _registry_manifest(
    registry: str, image: str, reference: str, *, user: str = "", token: str = ""
) -> tuple[str, dict]:
    """A tag's manifest as the registry serves it, and its digest: the pull path itself."""
    bearer = _registry_token(registry, user, token)
    url = f"https://{registry}/v2/{image}/manifests/{urllib.parse.quote(reference, safe='')}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {bearer}")
    request.add_header("Accept", MANIFEST_TYPES)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = response.read()
            digest = response.headers.get("Docker-Content-Digest", "")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ReleaseError(
                f"{image}:{reference} is not in the registry at {registry}."
            ) from error
        detail = error.read().decode(errors="replace")[:300]
        raise ReleaseError(f"GET {url} -> {error.code}: {detail}") from error
    # The digest is what the registry says it is; failing that, what docker would compute.
    return digest or "sha256:" + hashlib.sha256(raw).hexdigest(), json.loads(raw)


def verify_image(
    version: str, *, registry: str, image: str, user: str = "", token: str = ""
) -> list[str]:
    """Make sure the registry serves every tag a release pushes, for every architecture.

    The release workflow and the image workflow are separate by design (#81), so a green
    release says nothing about the image, and v0.3.0 was tagged, published and pulled by
    nobody for a day before anyone found the image had never been pushed (#251). This asks
    the registry the way ``docker pull`` would: each of the three tags must be there, must
    carry both architectures, and must be one image -- a ``latest`` still pointing at last
    month's release is the quietest way to publish nothing. Returns the tags it saw, in
    order, and raises naming the first thing wrong.
    """
    digests: dict[str, str] = {}
    for tag in image_tags(version):
        digest, manifest = _registry_manifest(registry, image, tag, user=user, token=token)
        found = {
            f"{entry['platform'].get('os')}/{entry['platform'].get('architecture')}"
            for entry in manifest.get("manifests") or []
            if isinstance(entry, dict) and isinstance(entry.get("platform"), dict)
        }
        missing = [platform for platform in PLATFORMS if platform not in found]
        if missing:
            raise ReleaseError(
                f"{image}:{tag} is missing {', '.join(missing)}; it carries "
                f"{', '.join(sorted(found)) or 'no platform list at all'}."
            )
        digests[tag] = digest
    if len(set(digests.values())) != 1:
        raise ReleaseError(
            "The tags are not one image: "
            + ", ".join(f"{tag} is {digest[:19]}…" for tag, digest in digests.items())
        )
    return list(digests)


# ------------------------------------------------------------------- command line


def _forgejo() -> tuple[str, str, str]:
    """Server, repository and token for the API, from the environment or not at all."""
    server = os.environ.get("FORGEJO_URL") or os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("FORGEJO_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("FORGEJO_TOKEN", "")
    if not (server and repository and token):
        raise ReleaseError("Set FORGEJO_URL, FORGEJO_REPOSITORY and FORGEJO_TOKEN.")
    return server, repository, token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    checker = commands.add_parser(
        "check", help="Make sure the tag, the versions and the changelog agree."
    )
    checker.add_argument("tag")
    checker.add_argument(
        "--ci",
        action="store_true",
        help=(
            "Also ask Forgejo whether every test leg and the browser job passed on the tagged "
            "commit. Needs FORGEJO_URL, FORGEJO_REPOSITORY and FORGEJO_TOKEN."
        ),
    )

    noter = commands.add_parser("notes", help="Print that version's changelog section.")
    noter.add_argument("version")
    noter.add_argument("-o", "--output", type=Path)

    publisher = commands.add_parser("publish", help="Create the Forgejo release and attach files.")
    publisher.add_argument("tag")
    publisher.add_argument("assets", nargs="*", type=Path)
    publisher.add_argument("--notes", type=Path, help="A file holding the release notes.")

    attacher = commands.add_parser("attach", help="Attach files to a release that exists.")
    attacher.add_argument("tag")
    attacher.add_argument("assets", nargs="+", type=Path)

    verifier = commands.add_parser(
        "verify-image", help="Ask the registry whether the tag's image is really there."
    )
    verifier.add_argument("tag")
    verifier.add_argument("--registry", required=True, help="the host, e.g. source.example.com")
    verifier.add_argument(
        "--image", required=True, help="the image under it, lowercase: owner/name"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            version = check(args.tag)
            print(f"{args.tag}: pyproject.toml, __version__ and CHANGELOG.md all say {version}.")
            if args.ci:
                server, repository, token = _forgejo()
                problems = ci_problems(args.tag, server=server, repository=repository, token=token)
                if problems:
                    listed = "".join(f"\n  {problem}" for problem in problems)
                    raise ReleaseError(f"CI has not passed on {args.tag}:{listed}")
                print(f"{args.tag}: every test leg and the browser job passed.")
        elif args.command == "notes":
            body = changelog_section(args.version)
            if args.output:
                args.output.write_text(body, encoding="utf-8")
            else:
                sys.stdout.write(body)
        elif args.command == "publish":
            server, repository, token = _forgejo()
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
        elif args.command == "attach":
            server, repository, token = _forgejo()
            uploaded = attach(
                args.tag, args.assets, server=server, repository=repository, token=token
            )
            print(
                f"{args.tag}: attached {', '.join(uploaded)}."
                if uploaded
                else f"{args.tag}: every file was already there."
            )
        elif args.command == "verify-image":
            tags = verify_image(
                version_of_tag(args.tag),
                registry=args.registry,
                image=args.image,
                user=os.environ.get("FORGEJO_USER", ""),
                token=os.environ.get("FORGEJO_TOKEN", ""),
            )
            print(
                f"{args.registry}/{args.image}: {', '.join(tags)} are one image, "
                f"for {' and '.join(PLATFORMS)}."
            )
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
