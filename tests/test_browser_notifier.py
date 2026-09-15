"""The browser notifier: Web Push, with the open tab as the fallback (#209).

The encryption is held to RFC 8291's own worked example, byte for byte, because a push service
does not say *why* a browser threw a message away -- a mistake in the key derivation looks,
from here, exactly like a push that worked. The rest is what a person relies on: a message
that cannot be pushed is not lost, an address a browser handed over is dialled under the same
rules as any other, and a page only asks for notices when there is a notifier to ask for.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.urls import reverse

from postulo.notifications import inbox
from postulo.notifications.base import Notification
from postulo.notifications.models import BrowserNotice
from postulo.notifications.service import notify
from postulo.plugins import http, registry
from postulo.plugins.browser import TAB, BrowserNotifier, webpush
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

# RFC 8291, Appendix A.
RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
RFC_SENDER_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_RECEIVER_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_RECEIVER_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmY"
    "WAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgS"
    "xsj_Qulcy4a-fN"
)

ENDPOINT = "https://push.example.net/send/abc123"


def private_key(encoded: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(webpush.unb64url(encoded), "big"), ec.SECP256R1())


def a_browser() -> tuple[ec.EllipticCurvePrivateKey, dict]:
    """A browser's half: its private key, and the subscription JSON it would hand over."""
    receiver = private_key(RFC_RECEIVER_PRIVATE)
    return receiver, {
        "endpoint": ENDPOINT,
        "keys": {"p256dh": RFC_RECEIVER_PUBLIC, "auth": RFC_AUTH},
    }


def browser_connection(user, *, subscription: dict | None = None, delivery="push", enabled=True):
    connection = Connection(
        owner=user,
        kind="notifier",
        plugin="browser",
        label="Laptop",
        enabled=enabled,
        config={"delivery": delivery},
    )
    if subscription is not None:
        connection.secrets = {"subscription": json.dumps(subscription)}
    connection.save()
    return connection


@pytest.fixture
def push_service(monkeypatch, settings):
    """A push service that records what it was sent and answers with ``status``."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True  # no DNS for push.example.net in a test
    received: list[httpx.Request] = []
    answer = {"status": 201}

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(answer["status"])

    monkeypatch.setattr(
        webpush,
        "_client",
        lambda: http.client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    return received, answer


# ----------------------------------------------------------------------- the encryption


def test_the_encryption_matches_the_rfcs_worked_example():
    subscription = webpush.Subscription(ENDPOINT, RFC_RECEIVER_PUBLIC, RFC_AUTH)

    body = webpush.encrypt(
        RFC_PLAINTEXT,
        subscription,
        sender_key=private_key(RFC_SENDER_PRIVATE),
        salt=webpush.unb64url(RFC_SALT),
    )

    assert webpush.b64url(body) == RFC_BODY


def test_every_message_gets_a_fresh_key_and_salt():
    subscription = webpush.Subscription(ENDPOINT, RFC_RECEIVER_PUBLIC, RFC_AUTH)

    one = webpush.encrypt(RFC_PLAINTEXT, subscription)
    two = webpush.encrypt(RFC_PLAINTEXT, subscription)

    assert one[:16] != two[:16], "the salt"
    assert one[21:86] != two[21:86], "the sender's public key"


def test_the_instance_key_is_stable_and_follows_the_secret_material(settings):
    first = webpush.application_server_key()
    assert webpush.application_server_key() == first
    assert len(webpush.unb64url(first)) == 65 and webpush.unb64url(first)[0] == 4

    settings.POSTULO_FIELD_KEY = "another key entirely, as after a rotation"
    assert webpush.application_server_key() != first


def test_the_vapid_token_verifies_against_the_key_it_names(settings):
    settings.POSTULO_PUBLIC_URL = "https://postulo.example.org"

    header = webpush.vapid_authorization(ENDPOINT, now=1_800_000_000)

    token, public = header.removeprefix("vapid t=").split(", k=")
    head, claims, signature = token.split(".")
    assert json.loads(webpush.unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(webpush.unb64url(claims)) == {
        "aud": "https://push.example.net",
        "exp": 1_800_000_000 + webpush.TOKEN_LIFETIME,
        "sub": "https://postulo.example.org",
    }
    raw = webpush.unb64url(signature)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64url(public))
    key.verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{head}.{claims}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    assert public == webpush.application_server_key()


def test_the_contact_is_the_operator_and_never_the_project(settings):
    settings.POSTULO_PUBLIC_URL = ""
    settings.DEFAULT_FROM_EMAIL = "postulo@jobs.example.org"
    assert webpush.contact() == "mailto:postulo@jobs.example.org"


# --------------------------------------------------------------- what a browser hands over


def test_a_subscription_is_read_or_refused_in_words():
    _, subscription = a_browser()

    assert webpush.parse_subscription("") is None
    assert webpush.parse_subscription(json.dumps(subscription)).endpoint == ENDPOINT
    short_point = {"p256dh": "AAAA", "auth": RFC_AUTH}
    short_auth = {"p256dh": RFC_RECEIVER_PUBLIC, "auth": "AA"}
    for broken, words in (
        ("not json", "not something a browser wrote"),
        (json.dumps({**subscription, "endpoint": "http://push.example.net/x"}), "HTTPS"),
        (json.dumps({**subscription, "keys": short_point}), "keys"),
        (json.dumps({**subscription, "keys": short_auth}), "keys"),
    ):
        with pytest.raises(ValueError, match=words):
            webpush.parse_subscription(broken)

    assert BrowserNotifier().validate({"subscription": "not json"}) == {
        "subscription": ["The subscription is not something a browser wrote."]
    }
    assert BrowserNotifier().validate({"subscription": ""}) == {}


# ------------------------------------------------------------------------------ pushing


def test_a_push_reaches_the_service_encrypted_for_that_browser(user, push_service):
    received, _answer = push_service
    receiver, subscription = a_browser()
    browser_connection(user, subscription=subscription)

    delivered = notify(
        user, Notification(event="reminder_due", title="Chase Aperture", url="/applications/1/")
    )

    assert delivered == 1
    (request,) = received
    assert str(request.url) == ENDPOINT
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert request.headers["TTL"] == str(webpush.TIME_TO_LIVE)
    assert request.headers["Authorization"].startswith("vapid t=")
    body = request.content
    assert b"Chase Aperture" not in body, "nothing readable on the way"

    # Decrypted as the browser would, from its own key: the payload is what the tab shows.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    salt, sender_public = body[:16], body[21:86]
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
    shared = receiver.exchange(ec.ECDH(), sender)
    info = b"WebPush: info\x00" + webpush.unb64url(RFC_RECEIVER_PUBLIC) + sender_public
    ikm = HKDF(hashes.SHA256(), 32, salt=webpush.unb64url(RFC_AUTH), info=info).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    payload = json.loads(AESGCM(key).decrypt(nonce, body[86:], None)[:-1])
    assert payload["title"] == "Chase Aperture"
    assert payload["url"] == "/applications/1/"
    assert not BrowserNotice.objects.exists(), "pushed, so nothing waits for a tab as well"


def test_a_withdrawn_subscription_leaves_the_notice_for_a_tab_and_says_so(user, push_service):
    _received, answer = push_service
    answer["status"] = 410
    _, subscription = a_browser()
    connection = browser_connection(user, subscription=subscription)

    delivered = notify(user, Notification(event="reminder_due", title="Chase Aperture"))

    assert delivered == 0
    assert BrowserNotice.objects.for_user(user).get().title == "Chase Aperture", "not lost"
    connection.refresh_from_db()
    assert "withdrawn" in connection.last_error

    result = BrowserNotifier().test({"subscription": json.dumps(subscription)})
    assert result.ok is False and "allow notifications again" in result.message


def test_a_push_address_is_dialled_under_the_instances_rules(user, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    _, subscription = a_browser()
    subscription["endpoint"] = "https://127.0.0.1:8443/push"

    with pytest.raises(http.DestinationRefused):
        webpush.push(webpush.parse_subscription(json.dumps(subscription)), {"title": "x"})

    browser_connection(user, subscription=subscription)
    assert notify(user, Notification(event="reminder_due", title="Refused")) == 0
    assert BrowserNotice.objects.for_user(user).filter(title="Refused").exists()


def test_tab_only_never_pushes_even_with_a_subscription(user, push_service):
    received, _answer = push_service
    _, subscription = a_browser()
    browser_connection(user, subscription=subscription, delivery=TAB)

    assert notify(user, Notification(event="capture_received", title="Captured: X")) == 1

    assert received == []
    assert BrowserNotice.objects.for_user(user).get().title == "Captured: X"
    assert BrowserNotifier().test({"delivery": TAB, "subscription": json.dumps(subscription)}).ok


def test_the_test_button_pushes_a_real_message(push_service):
    received, _answer = push_service
    _, subscription = a_browser()

    result = BrowserNotifier().test({"subscription": json.dumps(subscription)})

    assert result.ok and len(received) == 1


# ------------------------------------------------------------------------- the inbox


def test_the_same_notice_from_two_browsers_waits_once(user):
    browser_connection(user)
    browser_connection(user)

    assert notify(user, Notification(event="reminder_due", title="Once")) == 2

    assert BrowserNotice.objects.for_user(user).count() == 1


def test_shown_and_stale_notices_are_cleared_when_the_next_one_arrives(user):
    from django.utils import timezone

    inbox.leave(user, Notification(event="reminder_due", title="Shown"))
    inbox.collect(user)
    inbox.leave(user, Notification(event="reminder_due", title="Old"))
    BrowserNotice.objects.filter(title="Old").update(
        created_at=timezone.now() - inbox.KEPT_FOR - inbox.SAME_WITHIN
    )

    inbox.leave(user, Notification(event="reminder_due", title="New"))

    assert list(BrowserNotice.objects.for_user(user).values_list("title", flat=True)) == ["New"]


# --------------------------------------------------------------------------- the pages


def test_the_browser_notifier_ships_in_the_box_and_is_offered(client, user):
    assert registry.find_plugin("notifier", "browser") is not None
    client.force_login(user)

    page = client.get(reverse("connections:create", args=["notifier", "browser"])).content.decode()

    assert f'data-web-push-key="{webpush.application_server_key()}"' in page
    assert 'data-web-push-worker="/sw.js"' in page
    assert "data-web-push-allow=" in page
    assert 'name="plugin_subscription"' in page


def test_a_page_asks_for_notices_only_with_a_browser_notifier_switched_on(client, user):
    client.force_login(user)
    address = reverse("connections:list")
    assert "data-browser-notices" not in client.get(address).content.decode()

    connection = browser_connection(user, enabled=False)
    assert "data-browser-notices" not in client.get(address).content.decode()

    connection.enabled = True
    connection.save()
    page = client.get(address).content.decode()
    assert f'data-browser-notices="{reverse("notifications:waiting")}"' in page
    assert "data-browser-notices-token=" in page


def test_form_attributes_put_nothing_but_attributes_on_the_card(client, user, monkeypatch):
    def hostile(self):
        return {
            "web-push-key": '"><script>alert(1)</script>',
            'onload="alert(1)" x': "dropped",
            "UPPER": "dropped",
        }

    monkeypatch.setattr(BrowserNotifier, "form_attributes", hostile)
    client.force_login(user)

    page = client.get(reverse("connections:create", args=["notifier", "browser"])).content.decode()

    assert "<script>alert(1)</script>" not in page
    assert 'data-web-push-key="&quot;&gt;&lt;script&gt;' in page
    assert "onload=" not in page and "data-UPPER" not in page


def test_the_summary_names_the_service_and_nothing_secret():
    _, subscription = a_browser()
    plugin = BrowserNotifier()

    assert plugin.summary({"subscription": json.dumps(subscription)}) == (
        "Pushed through push.example.net"
    )
    assert plugin.summary({}) == "While Postulo is open"
    assert RFC_AUTH not in plugin.summary({"subscription": json.dumps(subscription)})


def test_base64url_round_trips_without_padding():
    for size in range(0, 40):
        data = bytes(range(size))
        encoded = webpush.b64url(data)
        assert "=" not in encoded
        assert webpush.unb64url(encoded) == data == base64.urlsafe_b64decode(encoded + "=" * 4)
