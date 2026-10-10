"""Operate the Weblate project translations are made in: translate.tiagoagueda.com (#349).

Translations are made in Weblate and nowhere else (#706). Weblate follows `main` (a Forgejo
webhook tells it of every push), drafts new strings by machine, and commits what speakers
save to a `weblate` branch it opens a pull request from. This script is for the moments
that loop needs a hand:

    uv run python scripts/weblate.py status            # every component: pending, locked, failing
    uv run python scripts/weblate.py lock|unlock [C…]  # stop or allow translating (all, or some)
    uv run python scripts/weblate.py commit|push       # commit and push Weblate's pending work now
    uv run python scripts/weblate.py pull              # make Weblate fetch and rebase onto main now
    uv run python scripts/weblate.py reset [C…]        # throw Weblate's checkout away for main's
    uv run python scripts/weblate.py components        # name a catalogue set with no component
    uv run python scripts/weblate.py components --create   # and create the missing ones
    uv run python scripts/weblate.py configure [C…]    # apply the settings below to components

**Renaming strings in bulk** (a sweep like #705) would drop every translation Weblate holds
for them that has not reached the repository yet, so it goes: `lock`, `commit`, `push`,
merge Weblate's pull request, `pull`, make the rename (carrying translations as drafts, see
docs/TRANSLATING.md), push, `unlock`.

The API key is read from the password manager each run (``bw``, unlocked by the session in
``~/.bw-session``) and never printed or written anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

API = "https://translate.tiagoagueda.com/api/"
PROJECT = "postulo"
#: The vault item holding the key, and the field it is in.
VAULT_ITEM = "Ouranos ❯ Services ❯ Weblate ❯ Tiago Agueda"
VAULT_FIELD = "API-KEY"

#: What every component is set to. The layout settings are what make a file Weblate saves
#: one `scripts/messages.py extract --check` accepts byte for byte
#: (`tests/test_po_roundtrip.py`); the rest is the round trip of #349.
LAYOUT = {
    "file_format_params": {"po_line_wrap": 65535, "po_set_x_generator": False},
    "language_code_style": "posix",
    "new_lang": "none",
}
#: Only for a component with a repository of its own; linked components follow it.
ROUND_TRIP = {
    "vcs": "gitea",
    "push_branch": "weblate",
    "merge_style": "rebase",
    "push_on_commit": True,
    "commit_pending_age": 1,
}

_KEY: str | None = None


def key() -> str:
    global _KEY
    if _KEY is None:
        bw = shutil.which("bw")
        session = Path.home() / ".bw-session"
        if not bw or not session.exists():
            raise SystemExit("Needs the Bitwarden CLI and a session in ~/.bw-session.")
        env = dict(os.environ, BW_SESSION=session.read_text(encoding="utf-8").strip())
        listed = subprocess.run(  # noqa: S603 - the Bitwarden CLI, with fixed arguments
            [bw, "list", "items", "--search", "Weblate"], capture_output=True, env=env, check=True
        ).stdout
        for item in json.loads(listed):
            if item.get("name") == VAULT_ITEM:
                for field in item.get("fields") or []:
                    if field.get("name") == VAULT_FIELD:
                        _KEY = field.get("value")
        if not _KEY:
            raise SystemExit(f"No {VAULT_FIELD} on {VAULT_ITEM!r}; `bw sync` and try again.")
    return _KEY


def call(path: str, method: str = "GET", data: dict | None = None):
    url = path if path.startswith("http") else API + path.lstrip("/")
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - https only
    request.add_header("Authorization", f"Token {key()}")
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            text = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {url}: {error.code} {error.read()[:400]!r}") from error
    return json.loads(text) if text else None


def every(path: str):
    url = path
    while url:
        page = call(url)
        yield from page["results"]
        url = page.get("next")


def components() -> list[dict]:
    return [c for c in every(f"projects/{PROJECT}/components/") if c["slug"] != "glossary"]


def chosen(names: list[str]) -> list[dict]:
    found = components()
    if not names:
        return found
    unknown = set(names) - {c["slug"] for c in found}
    if unknown:
        raise SystemExit(f"No such component: {', '.join(sorted(unknown))}")
    return [c for c in found if c["slug"] in names]


def linked(component: dict) -> bool:
    return str(component.get("repo", "")).startswith("weblate://") or bool(
        component.get("linked_component")
    )


# ------------------------------------------------------------------- commands


def cmd_status(_args) -> int:
    trouble = 0
    for component in components():
        slug = component["slug"]
        state = call(f"components/{PROJECT}/{slug}/repository/") or {}
        lock = call(f"components/{PROJECT}/{slug}/lock/") or {}
        notes = []
        if lock.get("locked"):
            notes.append("locked")
        for flag in ("needs_commit", "needs_push", "needs_merge"):
            if state.get(flag):
                notes.append(flag.replace("_", " "))
        if state.get("merge_failure"):
            notes.append("MERGE FAILURE")
            trouble += 1
        print(f"{slug:28} {component.get('vcs', ''):6} {', '.join(notes) or 'level with main'}")
    return 1 if trouble else 0


def cmd_lock(args) -> int:
    for component in chosen(args.components):
        state = call(f"components/{PROJECT}/{component['slug']}/lock/", "POST", {"lock": args.lock})
        print(component["slug"], "locked" if state.get("locked") else "open")
    return 0


def cmd_repository(args) -> int:
    """commit, push, pull, reset: on the project, or on the components named."""
    if not args.components:
        if args.operation == "reset":
            raise SystemExit("`reset` names its components: it throws work away.")
        call(f"projects/{PROJECT}/repository/", "POST", {"operation": args.operation})
        print(f"{args.operation}: asked of the whole project")
        return 0
    for component in chosen(args.components):
        slug = component["slug"]
        if args.operation == "reset":
            state = call(f"components/{PROJECT}/{slug}/repository/") or {}
            if state.get("needs_commit") or state.get("needs_push"):
                raise SystemExit(f"{slug} holds work main does not have; push it and merge first.")
        call(f"components/{PROJECT}/{slug}/repository/", "POST", {"operation": args.operation})
        print(f"{args.operation}: {slug}")
    return 0


def expected_components() -> dict[str, str]:
    """slug → file mask, for every catalogue set in this repository."""
    from postulo.core import messages_tool

    messages_tool.use(REPO)
    wanted = {}
    for subject in messages_tool.catalogue_sets():
        mask = subject.locale.relative_to(REPO).as_posix() + "/*/LC_MESSAGES/django.po"
        if subject.is_core:
            wanted["postulo"] = mask
        else:
            wanted["plugin-" + subject.name.removeprefix("plugins/").replace("_", "-")] = mask
    return wanted


def cmd_components(args) -> int:
    present = {c["slug"]: c for c in components()}
    missing = {slug: mask for slug, mask in expected_components().items() if slug not in present}
    for slug, mask in missing.items():
        print(f"missing: {slug} ({mask})")
        if args.create:
            call(
                f"projects/{PROJECT}/components/",
                "POST",
                {
                    "name": slug.replace("-", " ").title(),
                    "slug": slug,
                    "repo": f"weblate://{PROJECT}/postulo",
                    "filemask": mask,
                    "file_format": "po",
                    **LAYOUT,
                },
            )
            print(f"created: {slug}")
    if not missing:
        print("every catalogue set has its component")
    return 1 if missing and not args.create else 0


def cmd_configure(args) -> int:
    for component in chosen(args.components):
        settings = dict(LAYOUT)
        if args.round_trip and not linked(component):
            settings.update(ROUND_TRIP)
        call(f"components/{PROJECT}/{component['slug']}/", "PATCH", settings)
        print(f"configured: {component['slug']}{' (round trip)' if 'vcs' in settings else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(run=cmd_status)
    for name, lock in (("lock", True), ("unlock", False)):
        one = sub.add_parser(name)
        one.add_argument("components", nargs="*")
        one.set_defaults(run=cmd_lock, lock=lock)
    for operation in ("commit", "push", "pull", "reset"):
        one = sub.add_parser(operation)
        one.add_argument("components", nargs="*")
        one.set_defaults(run=cmd_repository, operation=operation)
    one = sub.add_parser("components")
    one.add_argument("--create", action="store_true")
    one.set_defaults(run=cmd_components)
    one = sub.add_parser("configure")
    one.add_argument("components", nargs="*")
    one.add_argument(
        "--round-trip", action="store_true", help="also the pull-request settings (#349)"
    )
    one.set_defaults(run=cmd_configure)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
