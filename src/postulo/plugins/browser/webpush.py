"""Web Push, written out: the message encryption and the key that signs every request.

Two RFCs and nothing else. RFC 8291 says how a message is encrypted so that the push service
carrying it cannot read it, and RFC 8292 (VAPID) says how this instance proves it is the one
a browser subscribed to. `pywebpush` does both, and brings `py-vapid`, `http-ece` and a second
HTTP client with it. Postulo already has the two things they wrap -- `cryptography` for the
curve, the key derivation and AES-GCM, and the guarded client for the request -- and a
hundred lines that match the RFC's own worked example are easier to audit than three
packages that are not.

**The signing key is derived, not stored.** It comes from the same material that encrypts a
connection's secrets (`POSTULO_FIELD_KEY`, else `SECRET_KEY`). A subscription is kept as one of
those secrets, so the day that material changes the subscriptions are unreadable anyway, and a
key that changes on the same day costs nothing extra. A stored key would be one more thing to
back up, and one that could outlive the subscriptions it signs for.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

#: The order of P-256, which a derived private key has to be below.
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

#: RFC 8188's record size. One record is all a push message ever is, and 4096 is what every
#: browser's push service accepts.
RECORD_SIZE = 4096

#: How long a push service keeps a message for a browser that is switched off. A reminder a
#: day late is still worth having; one a month late is noise.
TIME_TO_LIVE = 24 * 60 * 60

#: How long a VAPID token is good for. RFC 8292 caps it at a day; half that leaves room for a
#: clock that is a little wrong.
TOKEN_LIFETIME = 12 * 60 * 60


class PushFailed(Exception):
    """The push service did not take the message.

    ``gone`` is the case that needs a person: the browser has withdrawn the subscription, and
    only allowing notifications again in that browser brings it back.
    """

    def __init__(self, message: str, *, status: int = 0, gone: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.gone = gone


@dataclass(frozen=True)
class Subscription:
    """What a browser hands over when it agrees to receive pushes: where, and two keys."""

    endpoint: str
    p256dh: str
    auth: str


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


# --------------------------------------------------------------------- the instance's key


def application_key() -> ec.EllipticCurvePrivateKey:
    """This instance's VAPID key, the same every time for the same secret material.

    SHA-512 rather than SHA-256 so that reducing the digest into the curve's order leaves no
    bias worth the name.
    """
    material = getattr(settings, "POSTULO_FIELD_KEY", "") or settings.SECRET_KEY
    digest = hashlib.sha512(b"postulo-web-push-vapid:" + material.encode("utf-8")).digest()
    scalar = int.from_bytes(digest, "big") % (_P256_ORDER - 1) + 1
    return ec.derive_private_key(scalar, ec.SECP256R1())


def application_server_key() -> str:
    """The public half, as the browser's ``applicationServerKey`` wants it."""
    return b64url(_raw_public(application_key().public_key()))


def contact() -> str:
    """Who a push service writes to about this instance's traffic.

    The operator, never the project: a push service with a complaint has one about this
    instance. Its public address where there is one, otherwise its mail sender.
    """
    public = (getattr(settings, "POSTULO_PUBLIC_URL", "") or "").strip()
    if public.startswith("https://"):
        return public
    return f"mailto:{settings.DEFAULT_FROM_EMAIL}"


def vapid_authorization(
    endpoint: str, *, key: ec.EllipticCurvePrivateKey | None = None, now: float | None = None
) -> str:
    """The ``Authorization`` header for one request to ``endpoint`` (RFC 8292 §3)."""
    key = key or application_key()
    parts = urlsplit(endpoint)
    audience = f"{parts.scheme}://{parts.netloc}"
    issued = int(time.time() if now is None else now)
    compact = {"separators": (",", ":")}
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, **compact).encode())
    claims = b64url(
        json.dumps(
            {"aud": audience, "exp": issued + TOKEN_LIFETIME, "sub": contact()}, **compact
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    public = b64url(_raw_public(key.public_key()))
    return f"vapid t={header}.{claims}.{signature}, k={public}"


# ------------------------------------------------------------------------ the message


def encrypt(
    plaintext: bytes,
    subscription: Subscription,
    *,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """Encrypt one message for one browser (RFC 8291 §3, ``aes128gcm``).

    ``sender_key`` and ``salt`` are fresh for every message and exist as arguments only so the
    RFC's worked example can be reproduced byte for byte.
    """
    receiver_public = unb64url(subscription.p256dh)
    auth_secret = unb64url(subscription.auth)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_public)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    sender_public = _raw_public(sender_key.public_key())
    salt = salt if salt is not None else os.urandom(16)

    shared = sender_key.exchange(ec.ECDH(), receiver)
    key_info = b"WebPush: info\x00" + receiver_public + sender_public
    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=key_info).derive(shared)
    content_key = HKDF(
        hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00"
    ).derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)

    # 0x02 marks the last (and only) record, with no padding after it.
    ciphertext = AESGCM(content_key).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public
    return header + ciphertext


# ------------------------------------------------------------------ what a browser sent


def parse_subscription(raw) -> Subscription | None:
    """A subscription out of what the page stored, or ``None`` when there is none.

    Raises ``ValueError`` saying what is wrong with one that is there but unusable, so the
    connection form can say it rather than the first reminder failing.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as error:
        raise ValueError("The subscription is not something a browser wrote.") from error
    if not isinstance(data, dict):
        raise ValueError("The subscription is not something a browser wrote.")
    endpoint = str(data.get("endpoint") or "")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    if not endpoint.startswith("https://"):
        raise ValueError("A push address has to be HTTPS.")
    try:
        receiver = unb64url(str(keys.get("p256dh") or ""))
        auth = unb64url(str(keys.get("auth") or ""))
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver)
    except (ValueError, TypeError) as error:
        raise ValueError("The subscription's keys are not the browser's.") from error
    if len(receiver) != 65 or len(auth) != 16:
        raise ValueError("The subscription's keys are not the browser's.")
    return Subscription(endpoint=endpoint, p256dh=str(keys["p256dh"]), auth=str(keys["auth"]))


# ------------------------------------------------------------------------ sending one


def _client():
    """The guarded client, not following redirects: a push service that redirects is wrong."""
    from postulo.plugins.api import client

    return client(follow_redirects=False)


def push(subscription: Subscription, payload: dict, *, ttl: int = TIME_TO_LIVE) -> int:
    """Encrypt ``payload`` and hand it to the browser's push service. Returns the status.

    The endpoint came from a browser, so it is dialled through the same client as any address
    a person typed: the operator's rule about private addresses applies to it too.
    """
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"), subscription)
    headers = {
        "TTL": str(ttl),
        "Urgency": "normal",
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid_authorization(subscription.endpoint),
    }
    with _client() as client:
        response = client.post(subscription.endpoint, content=body, headers=headers)
    if response.status_code in (404, 410):
        raise PushFailed(
            "The browser has withdrawn this subscription.",
            status=response.status_code,
            gone=True,
        )
    if response.status_code >= 300:
        raise PushFailed(
            f"The push service answered {response.status_code}: {response.text[:200]}",
            status=response.status_code,
        )
    return response.status_code
