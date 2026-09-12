"""Remove old dev images from the registry, keeping the newest few (#190).

    python scripts/prune-dev-images.py --registry source.example.com --owner postulo \\
        --package postulo --keep 5 [--dry-run]

Every push to ``main`` publishes ``:dev`` and a pinnable ``:<version>-dev.<sha>``, and
nothing else ever takes one away. This keeps the newest ``--keep`` pinned tags -- enough to
roll an instance back -- and deletes the rest. Only versions spelled like a dev tag are
candidates: a release, ``latest`` and ``dev`` itself are never touched.

**A tag is not the whole image.** A two-architecture push stores the index under the tag
and each architecture's manifest as its own untagged ``sha256:`` version, and a manifest
nobody names still holds its layers. So after a tag goes, each manifest it referenced is
deleted too -- unless a tag that stays still references it, which is the common case for
the base layers and the reason this reads every remaining tag before removing anything.
Nothing else untagged is touched: a manifest this run did not orphan is not its business,
and a push in flight from another workflow has manifests in exactly that state.

Standard library only, and the runner's Python is Ubuntu's, so nothing newer than 3.10 is
used. Talks to the Forgejo API for the listing and the deletions and to the registry's own
``/v2/`` for the manifests, because the API does not hand out a manifest's content. The
credential comes from ``FORGEJO_USER`` and ``FORGEJO_TOKEN``, never the command line.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

#: What a dev tag looks like, and nothing else: the version from ``__init__.py`` -- with
#: whatever pre-release or build part it carries -- then ``-dev.`` and a short commit hash.
#: Deliberately strict, because everything that does not match is kept.
DEV_TAG = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]*?)?-dev\.[0-9a-f]{7,40}$")

#: An untagged version: the digest of a manifest an index refers to.
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Whatever the registry stored the manifest as; the reply says which it was.
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def created(version: dict[str, Any]) -> float:
    """When a version was created, from the API's timestamp; the epoch if it has none.

    Python 3.10's ``fromisoformat`` does not read a trailing ``Z``; Forgejo writes one.
    """
    stamp = str(version.get("created_at") or "")
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return 0.0


def select(versions: list[dict[str, Any]], keep: int) -> tuple[list[str], list[str]]:
    """Which dev tags to delete and which tags -- of any kind -- stay.

    The newest ``keep`` dev tags by creation time stay, along with every version that is
    not a dev tag and not a bare digest. Returns ``(doomed, remaining_tags)``.
    """
    if keep < 0:
        raise ValueError("keep must be zero or more")
    dev = sorted(
        (v for v in versions if DEV_TAG.match(str(v.get("version", "")))),
        key=created,
        reverse=True,
    )
    doomed = [str(v["version"]) for v in dev[keep:]]
    remaining = [
        str(v["version"])
        for v in versions
        if not DIGEST.match(str(v.get("version", ""))) and str(v["version"]) not in doomed
    ]
    return doomed, remaining


def children(manifest: dict[str, Any]) -> set[str]:
    """The manifests an index points at; empty for a single-architecture manifest."""
    return {
        str(entry["digest"])
        for entry in manifest.get("manifests") or []
        if isinstance(entry, dict) and DIGEST.match(str(entry.get("digest", "")))
    }


class Registry:
    """One Forgejo package registry, spoken to as one user.

    Small enough to be replaced whole by a fake in the tests; every method is one request.
    """

    def __init__(self, host: str, owner: str, package: str, user: str, token: str) -> None:
        self.host = host
        self.owner = owner
        self.package = package
        self._basic = base64.b64encode(f"{user}:{token}".encode()).decode()
        self._bearer: str | None = None

    # -- transport -------------------------------------------------------------------------

    def _request(
        self, method: str, url: str, headers: dict[str, str], *, ok: tuple[int, ...] = (200,)
    ) -> tuple[int, bytes]:
        # Always https to the host given on the command line; the scheme is not an input.
        request = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            if error.code in ok:
                return error.code, error.read()
            body = error.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{method} {url} -> {error.code}: {body}") from error

    def _api(self, method: str, path: str, *, ok: tuple[int, ...] = (200,)) -> tuple[int, bytes]:
        url = f"https://{self.host}/api/v1{path}"
        headers = {"Authorization": f"Basic {self._basic}", "Accept": "application/json"}
        return self._request(method, url, headers, ok=ok)

    def _registry_token(self) -> str:
        """The bearer token the registry wants, exchanged for the basic credential once."""
        if self._bearer is None:
            _, body = self._request(
                "GET", f"https://{self.host}/v2/token", {"Authorization": f"Basic {self._basic}"}
            )
            self._bearer = str(json.loads(body)["token"])
        return self._bearer

    # -- what the pruning needs --------------------------------------------------------------

    def versions(self) -> list[dict[str, Any]]:
        """Every version of the package, across however many pages the listing takes."""
        found: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urllib.parse.urlencode({"type": "container", "page": page, "limit": 50})
            _, body = self._api("GET", f"/packages/{self.owner}?{query}")
            batch = json.loads(body)
            found.extend(v for v in batch if v.get("name") == self.package)
            if len(batch) < 50:
                return found
            page += 1

    def manifest(self, reference: str) -> dict[str, Any]:
        """A tag's or digest's manifest, as the registry stored it."""
        url = (
            f"https://{self.host}/v2/{self.owner}/{self.package}/manifests/"
            f"{urllib.parse.quote(reference, safe='')}"
        )
        headers = {"Authorization": f"Bearer {self._registry_token()}", "Accept": MANIFEST_TYPES}
        _, body = self._request("GET", url, headers)
        return json.loads(body)

    def delete(self, version: str) -> bool:
        """Delete one version; False if it was already gone."""
        path = (
            f"/packages/{self.owner}/container/{self.package}/"
            f"{urllib.parse.quote(version, safe='')}"
        )
        status, _ = self._api("DELETE", path, ok=(204, 404))
        return status == 204


def prune(registry: Registry, keep: int, *, dry_run: bool = False) -> list[str]:
    """Delete the dev tags past the newest ``keep``, then the manifests only they held.

    Returns what was (or, dry, would have been) deleted, tags first, in order.
    """
    doomed, remaining = select(registry.versions(), keep)
    if not doomed:
        print(f"nothing to prune: {len(remaining)} tags, none past the newest {keep} dev tags")
        return []

    # Read before deleting: a doomed tag's manifest is gone with the tag.
    orphaned: set[str] = set()
    for tag in doomed:
        orphaned |= children(registry.manifest(tag))
    still_referenced: set[str] = set()
    for tag in remaining:
        still_referenced |= children(registry.manifest(tag))
    orphaned -= still_referenced

    deleted: list[str] = []
    for version in [*doomed, *sorted(orphaned)]:
        if dry_run:
            print(f"would delete {version}")
        elif registry.delete(version):
            print(f"deleted {version}")
        else:
            print(f"already gone: {version}")
        deleted.append(version)
    kept = len(remaining) - sum(1 for tag in remaining if not DEV_TAG.match(tag))
    print(f"{len(doomed)} dev tags and {len(orphaned)} manifests; {kept} dev tags kept")
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--registry", required=True, help="host name, e.g. source.example.com")
    parser.add_argument("--owner", required=True, help="the organisation or user, lowercase")
    parser.add_argument("--package", required=True, help="the image name under the owner")
    parser.add_argument("--keep", type=int, default=5, help="pinned dev tags to keep (5)")
    parser.add_argument("--dry-run", action="store_true", help="say what would go; delete nothing")
    args = parser.parse_args(argv)

    user = os.environ.get("FORGEJO_USER")
    token = os.environ.get("FORGEJO_TOKEN")
    if not user or not token:
        print("FORGEJO_USER and FORGEJO_TOKEN must be set", file=sys.stderr)
        return 2
    registry = Registry(args.registry, args.owner, args.package, user, token)
    try:
        prune(registry, args.keep, dry_run=args.dry_run)
    except (RuntimeError, urllib.error.URLError, KeyError, ValueError) as error:
        print(f"prune failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
