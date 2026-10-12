"""The VAPID public key is not a pure function of the secret key (#821)."""

from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings

from postulo.core.models import SiteSettings
from postulo.plugins.browser import webpush
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


def legacy_public() -> str:
    material = getattr(settings, "POSTULO_FIELD_KEY", "") or settings.SECRET_KEY
    digest = hashlib.sha512(b"postulo-web-push-vapid:" + material.encode()).digest()
    scalar = int.from_bytes(digest, "big") % (webpush._P256_ORDER - 1) + 1
    key = ec.derive_private_key(scalar, ec.SECP256R1())
    return webpush.b64url(webpush._raw_public(key.public_key()))


def test_a_new_instance_gets_a_salted_key_that_is_not_the_old_derivation():
    published = webpush.application_server_key()
    assert SiteSettings.get().web_push_salt
    assert published != legacy_public()


def test_the_key_is_the_same_every_time_once_the_salt_is_made():
    assert webpush.application_server_key() == webpush.application_server_key()


def test_two_instances_with_the_same_secret_publish_different_keys():
    first = webpush.application_server_key()
    row = SiteSettings.get()
    row.web_push_salt = "00" * 32
    row.save()
    assert webpush.application_server_key() != first


def test_an_instance_with_subscriptions_keeps_the_old_key_so_they_go_on_working(user):
    Connection.objects.create(
        owner=user, kind="notifier", plugin="browser", label="Laptop", config={}
    )
    assert webpush.application_server_key() == legacy_public()
    assert SiteSettings.get().web_push_salt == "", "no salt was made behind them"
