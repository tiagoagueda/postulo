"""A European Digital Credential, kept as the file it is (#670).

A university, a training provider or a public body now awards a qualification as a
**European Digital Credential**: JSON-LD on the W3C Verifiable Credential model (European
Learning Model v3), usually sealed with a JAdES signature. It is the proof, so it is kept as a
certificate, a diploma or a diploma supplement is, and **byte for byte**: a seal covers exact
bytes, so nothing here re-encodes, pretty-prints or "tidies" it.

This module answers one question, *is this a credential?*, by what the file holds and not by
its name. It is not a verifier. **Postulo does not check the seal**, and says so wherever a
credential is shown: a grey label, never a green tick, never absent. A check, if one is ever
wanted, is a plugin of its own that replaces the label with its own result.

**Two shapes, as the Europass wallet and issuer write them.** A sealed file is a JWS in the
JSON general serialisation, ``{"payload": "<the credential as a string>", "signatures":
[...]}``, the payload unencoded (``b64: false``), so the credential is the payload parsed. An
unsealed file is the bare credential. Both are recognised; neither has its ``@context``
followed.

**Read as a stranger's file is read here** (`plugins/europass/reader.py`): a size cap before
parsing, a nesting cap after, no key assumed present and no value assumed to have its type,
nothing fetched and no schema resolved. The old European Learning Model v2 credential was
signed XML; the wallets have converted every one, so it is recognised only to be refused with
a sentence saying where the new one is.
"""

from __future__ import annotations

import json

#: The extensions a credential arrives with. Only a credential may carry them: an upload of
#: any other JSON is refused as not being one.
EXTENSIONS = ("json", "jsonld")

#: A credential carries its issuer's logo and its own picture inline, as base64, so it is
#: larger than its facts: 100 to 150 KB in the samples, 1.3 MB for a transcript of records.
MAX_BYTES = 5 * 1024 * 1024

#: A credential nests a dozen levels or so. Anything this deep is not one.
MAX_DEPTH = 40

#: What the ``type`` of a verifiable credential always holds.
VERIFIABLE = "VerifiableCredential"


def _depth(value) -> int:
    """How deeply the parsed document nests, measured without recursing into it."""
    worst = 0
    stack = [(value, 1)]
    while stack:
        node, level = stack.pop()
        worst = max(worst, level)
        if level > MAX_DEPTH:
            return level
        if isinstance(node, dict):
            stack.extend((child, level + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, level + 1) for child in node)
    return worst


def _parsed(text) -> dict | None:
    """A JSON object out of text, bounded, or nothing."""
    if not isinstance(text, (str, bytes)):
        return None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):  # ValueError: bad JSON, bad UTF-8, a huge number
        return None
    if not isinstance(document, dict) or _depth(document) > MAX_DEPTH:
        return None
    return document


def types_of(value) -> list[str]:
    """A ``type``, which JSON-LD writes as a string when there is one and a list otherwise."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def credential_in(data: bytes) -> dict | None:
    """The verifiable credential a file holds, sealed or not, or ``None`` where it holds none.

    Nothing is fetched and nothing is decoded but the JSON: an embedded picture stays the
    string it is.
    """
    if not data or len(data) > MAX_BYTES:
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    document = _parsed(text)
    if document is None:
        return None
    # The sealed shape: the credential is the payload, as a string, beside its signatures.
    if "signatures" in document and isinstance(document.get("payload"), str):
        document = _parsed(document["payload"])
        if document is None:
            return None
    return document if VERIFIABLE in types_of(document.get("type")) else None


def is_credential(data: bytes) -> bool:
    return credential_in(data) is not None


def is_old_credential(data: bytes) -> bool:
    """Whether this is a credential in the old XML of the European Learning Model v2.

    By the start of the file only, before anything parses it: it is recognised to be refused,
    and the sentence that refuses it is the whole of what Postulo does with it.
    """
    head = data[:4096].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if not head.startswith(b"<"):
        return False
    return b"europasscredential" in head or b"data.europa.eu/europass/model/credentials" in head
