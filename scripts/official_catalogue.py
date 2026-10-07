"""Build, sign and check the official plugin catalogue: ``catalogue/official/index.json``.

The catalogue is the list of plugins the Postulo project publishes (the *Official* tier of
Server settings → Plugins). It is one JSON file and a detached Ed25519 signature over
exactly its bytes, which is what ``postulo.plugins.catalogue`` reads and checks.

    python scripts/official_catalogue.py build  --wheels DIR   # plugins.toml + wheels → index.json
    python scripts/official_catalogue.py sign                  # index.json → index.json.sig
    python scripts/official_catalogue.py verify [--wheels DIR] # what an instance would check

``sign`` reads the private key (base64, the 32 raw bytes) from ``POSTULO_CATALOGUE_KEY``
or, with ``--key-file``, from a file; never from an argument, where it would be in the
shell's history. The key is kept in the project's password manager and nowhere in this
repository. ``verify`` needs no secret: it checks the signature against the public key an
instance is built trusting and, given the wheels, that every checksum is the one of the
file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DIRECTORY = ROOT / "catalogue" / "official"
SOURCE = DIRECTORY / "plugins.toml"
INDEX = DIRECTORY / "index.json"
SIGNATURE = DIRECTORY / "index.json.sig"


def wheel_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(document: dict) -> bytes:
    """The index's bytes, the same every time for the same content: sorted, LF, one newline."""
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def build(wheels: Path) -> dict:
    source = tomllib.loads(SOURCE.read_text(encoding="utf-8"))
    plugins = []
    for entry in source.get("plugin", []):
        releases = []
        for release in entry.get("release", []):
            wheel = wheels / wheel_name(release["url"])
            if not wheel.is_file():
                raise SystemExit(f"{wheel} is not there: build the wheel and point --wheels at it.")
            releases.append(
                {
                    "version": release["version"],
                    "url": release["url"],
                    "sha256": sha256_of(wheel),
                    "requires_postulo": release.get("requires_postulo", ""),
                    "provides": list(release.get("provides", [])),
                }
            )
        plugins.append(
            {
                **{key: value for key, value in entry.items() if key != "release"},
                "releases": releases,
            }
        )
    return {"plugins": plugins}


def private_key(key_file: str | None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if key_file:
        raw = Path(key_file).read_text(encoding="ascii")
    else:
        raw = os.environ.get("POSTULO_CATALOGUE_KEY", "")
    if not raw.strip():
        raise SystemExit("No key: set POSTULO_CATALOGUE_KEY or pass --key-file.")
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw.strip(), validate=True))


def check(wheels: Path | None) -> list[str]:
    """Every way the published pair would be refused, as sentences. Empty means it is good."""
    from postulo.plugins import catalogue, provenance

    if not INDEX.is_file() or not SIGNATURE.is_file():
        return ["index.json or index.json.sig is missing."]
    problems = []
    payload = INDEX.read_bytes()
    signature = SIGNATURE.read_text(encoding="ascii").strip()
    try:
        catalogue.verify(payload, signature, provenance.OFFICIAL_KEY)
    except catalogue.CatalogueError as error:
        problems.append(str(error))
    try:
        listings = catalogue.parse(payload, catalogue="official")
    except catalogue.CatalogueError as error:
        return [*problems, str(error)]
    if payload != render(json.loads(payload)):
        problems.append("index.json is not in the form `build` writes, so it was edited by hand.")
    for listing in listings:
        if not listing.releases:
            problems.append(f"{listing.name} lists no release.")
        for release in listing.releases:
            if wheels is None:
                continue
            wheel = wheels / wheel_name(release.url)
            if not wheel.is_file():
                problems.append(f"{wheel.name} is not in {wheels}.")
            elif sha256_of(wheel) != release.sha256:
                problems.append(f"{wheel.name} is not the file {listing.name} signed for.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    built = sub.add_parser("build", help="write index.json from plugins.toml and the wheels")
    built.add_argument("--wheels", type=Path, required=True)
    signed = sub.add_parser("sign", help="write index.json.sig")
    signed.add_argument("--key-file")
    verified = sub.add_parser("verify", help="check the pair as an instance would")
    verified.add_argument("--wheels", type=Path)
    args = parser.parse_args(argv)

    if args.command == "build":
        INDEX.write_bytes(render(build(args.wheels)))
        SIGNATURE.unlink(missing_ok=True)
        print(f"Wrote {INDEX.relative_to(ROOT)}; it is unsigned until `sign` runs.")
        return 0
    if args.command == "sign":
        from postulo.plugins import provenance

        key = private_key(args.key_file)
        public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
        if public != provenance.OFFICIAL_KEY:
            raise SystemExit("That is not the key instances trust (provenance.OFFICIAL_KEY).")
        SIGNATURE.write_bytes(base64.b64encode(key.sign(INDEX.read_bytes())) + b"\n")
        print(f"Wrote {SIGNATURE.relative_to(ROOT)}.")
        return 0
    problems = check(args.wheels)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print("The index and its signature check out.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
