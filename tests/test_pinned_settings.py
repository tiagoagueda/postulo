"""The Server pages' settings that the environment can hold, and the page that says so (#1186).

Every `SiteSettings` field an administrator sets has a variable, and where the variable is set
the environment wins: the row is read with its value laid over, nothing that reads it needs to
know, the stored value is never overwritten, and the page shows the field read-only with the
variable named beside it.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.core import configuration, pins, site
from postulo.core.models import SiteSettings
from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org",
        password="a-fairly-long-password-42",
        username="admin-one",
        is_staff=True,
        is_superuser=True,
    )


#: Each field, a value for its variable, and what the row then holds.
CASES = {
    "instance_name": ("Jobs at Example", "Jobs at Example"),
    "tagline": ("Find work", "Find work"),
    "motto": ("Hire kindly", "Hire kindly"),
    "default_language": ("fr-FR", "fr-FR"),
    "offered_languages": ("fr-FR, pt-PT", ["fr-FR", "pt-PT"]),
    "default_paper": ("letter-portrait", "letter-portrait"),
    "email_sign_in": ("yes", True),
    "geocoder_consent": ("on", True),
    "retention_days": ("90d", 90),
    "privacy_notice": ("We keep little.", "We keep little."),
    "identifier_schemes": ("[]", "[]"),
    "backup_schedule": ("weekly", "weekly"),
    "backup_hour": ("4", 4),
    "backup_weekday": ("6", 6),
    "backup_keep": ("14", 14),
    "backup_recipient": ("age1abc", "age1abc"),
    "backup_remote_keep": ("0", 0),
    "backup_destination": ("s3", "s3"),
    "backup_destination_config": ('{"bucket": "b", "key": "k"}', {"bucket": "b", "key": "k"}),
    "email_transport": ("ses", "ses"),
    "transport_config": ('{"region": "eu"}', {"region": "eu"}),
    "text_transport": ("sms", "sms"),
    "text_config": ('{"from": "Postulo"}', {"from": "Postulo"}),
}


def test_every_field_the_environment_pins_without_an_accessor_is_covered_here():
    assert set(CASES) | {"register_keys"} == set(pins.FIELDS)


@pytest.mark.parametrize("field", sorted(CASES))
def test_the_environment_is_laid_over_the_row_and_the_stored_value_survives(field, monkeypatch):
    raw, expected = CASES[field]
    stored = SiteSettings.get()
    before = getattr(stored, field)
    monkeypatch.setenv(pins.FIELDS[field], raw)
    site.forget_current()

    row = SiteSettings.get()

    assert getattr(row, field) == expected
    assert getattr(site.current(), field) == expected
    assert site.overridden_by(field) == pins.FIELDS[field]
    # A save never copies the environment into the database: it is the stored value again
    # the day the variable goes.
    row.save()
    monkeypatch.delenv(pins.FIELDS[field])
    site.forget_current()
    assert getattr(SiteSettings.get(), field) == before
    assert getattr(SiteSettings.objects.get(pk=1), field) == before


def test_a_secret_configuration_replaces_the_stored_secrets_too(monkeypatch):
    row = SiteSettings.get()
    row.transport_secrets = {"token": "stored"}
    row.save()
    monkeypatch.setenv("POSTULO_EMAIL_TRANSPORT_CONFIG", '{"token": "from-the-environment"}')

    pinned = SiteSettings.objects.get(pk=1)

    assert pinned.transport_config == {"token": "from-the-environment"}
    assert pinned.transport_secrets == {}
    monkeypatch.delenv("POSTULO_EMAIL_TRANSPORT_CONFIG")
    assert SiteSettings.objects.get(pk=1).transport_secrets == {"token": "stored"}


def test_the_register_keys_come_from_the_environment_whole(monkeypatch):
    row = SiteSettings.get()
    row.register_keys = {"stored": "key"}
    row.save()
    monkeypatch.setenv("POSTULO_REGISTER_KEYS", json.dumps({"fr": "k1"}))

    assert SiteSettings.objects.get(pk=1).register_keys == {"fr": "k1"}
    monkeypatch.delenv("POSTULO_REGISTER_KEYS")
    assert SiteSettings.objects.get(pk=1).register_keys == {"stored": "key"}


def test_a_refresh_keeps_the_environment_over_the_row_and_the_stored_value_under_it(monkeypatch):
    row = SiteSettings.get()
    row.tagline = "stored"
    row.save()
    monkeypatch.setenv("POSTULO_INSTANCE_TAGLINE", "pinned")
    row = SiteSettings.get()
    SiteSettings.objects.filter(pk=1).update(tagline="changed elsewhere")

    row.refresh_from_db()

    assert row.tagline == "pinned"
    row.save()
    assert SiteSettings.objects.values_list("tagline", flat=True).get() == "changed elsewhere"


def test_an_unset_variable_pins_nothing_and_an_empty_one_pins_the_default_only_for_text(
    monkeypatch,
):
    monkeypatch.setenv("POSTULO_BACKUP_HOUR", "")
    monkeypatch.setenv("POSTULO_INSTANCE_NAME", "")
    monkeypatch.setenv("POSTULO_INSTANCE_TAGLINE", "")
    SiteSettings.objects.update_or_create(pk=1, defaults={"tagline": "stored", "backup_hour": 9})
    site.forget_current()

    row = SiteSettings.get()

    assert row.backup_hour == 9 and row.instance_name == "Postulo"
    assert row.tagline == "", "an empty text pins the field to nothing"


def test_a_configuration_that_is_not_a_json_object_stops_the_start(monkeypatch):
    environ = {"POSTULO_EMAIL_TRANSPORT_CONFIG": "[1, 2]"}

    [problem] = configuration.problems(environ)

    assert problem.level == "error"
    assert "JSON object" in problem.hint
    assert "[1, 2]" not in problem.message, "a secret's value is never printed"


def test_the_retention_is_counted_in_days_a_part_of_a_day_counting_as_one(monkeypatch):
    monkeypatch.setenv("POSTULO_RETENTION_SECONDS", "36h")

    assert SiteSettings.get().retention_days == 2


# --------------------------------------------------------------------------------- the pages


def _page(client, admin, name):
    signed_in_recently(client, admin)
    return client.get(reverse(name), follow=True).content.decode()


def test_the_defaults_page_shows_each_pinned_field_read_only_with_its_variable(
    client, admin, monkeypatch
):
    monkeypatch.setenv("POSTULO_INSTANCE_NAME", "Jobs at Example")
    monkeypatch.setenv("POSTULO_LANGUAGE_DEFAULT", "fr-FR")
    monkeypatch.setenv("POSTULO_PAPER_DEFAULT", "letter-portrait")
    monkeypatch.setenv("POSTULO_LANGUAGES_OFFERED", "fr-FR,pt-PT,en-GB")

    html = _page(client, admin, "server:defaults")

    for field, variable in (
        ("instance_name", "POSTULO_INSTANCE_NAME"),
        ("default_language", "POSTULO_LANGUAGE_DEFAULT"),
        ("default_paper", "POSTULO_PAPER_DEFAULT"),
    ):
        assert f'data-pinned="{field}"' in html, field
        assert variable in html, variable
    assert 'data-pinned="offered_languages"' in html
    assert "Jobs at Example" in html
    assert 'name="offered_languages_submit"' not in html, "nothing left to save there"


def test_a_post_cannot_change_a_pinned_field(client, admin, monkeypatch):
    SiteSettings.objects.update_or_create(pk=1, defaults={"instance_name": "Stored"})
    monkeypatch.setenv("POSTULO_INSTANCE_NAME", "Pinned")
    signed_in_recently(client, admin)

    client.post(
        reverse("server:defaults"),
        {"instance_name": "Typed", "tagline": "kept", "default_paper": "a4-portrait"},
    )

    row = SiteSettings.objects.get(pk=1)
    assert row.tagline == "kept", "what is not pinned is saved"
    monkeypatch.delenv("POSTULO_INSTANCE_NAME")
    assert SiteSettings.objects.get(pk=1).instance_name == "Stored"


def test_the_data_protection_and_backup_pages_say_which_variable_holds_a_field(
    client, admin, monkeypatch
):
    monkeypatch.setenv("POSTULO_RETENTION_SECONDS", "90d")
    monkeypatch.setenv("POSTULO_PRIVACY_NOTICE", "We keep little.")
    monkeypatch.setenv("POSTULO_BACKUP_KEEP", "14")
    monkeypatch.setenv("POSTULO_BACKUP_RECIPIENT", "age1abc")

    data = _page(client, admin, "server:data_protection")
    backups = _page(client, admin, "server:backups")
    destination = _page(client, admin, "server:backup_destination")

    assert 'data-pinned="retention_days"' in data and "POSTULO_RETENTION_SECONDS" in data
    assert 'data-pinned="privacy_notice"' in data and "We keep little." in data
    assert 'data-pinned="backup_keep"' in backups and "POSTULO_BACKUP_KEEP" in backups
    assert 'data-pinned="backup_recipient"' in destination


def test_the_sign_in_and_maps_pages_hold_theirs_too(client, admin, monkeypatch):
    monkeypatch.setenv("POSTULO_EMAIL_CODE_SIGN_IN", "true")
    monkeypatch.setenv("POSTULO_GEOCODER_CONSENT", "true")

    assert 'data-pinned="email_sign_in"' in _page(client, admin, "server:signin")
    assert 'data-pinned="geocoder_consent"' in _page(client, admin, "server:maps")


def test_the_register_page_says_the_keys_come_from_the_environment(client, admin, monkeypatch):
    monkeypatch.setenv("POSTULO_REGISTER_KEYS", '{"fr": "k"}')

    html = _page(client, admin, "server:register_lookup")

    assert 'data-pinned="register_keys"' in html and "POSTULO_REGISTER_KEYS" in html


def test_the_registry_says_what_the_model_says():
    """A variable's default and words are the field's own, so not setting it changes nothing."""
    from django.db.models import NOT_PROVIDED

    from postulo.documents import paper

    for field, name in pins.FIELDS.items():
        if field == "register_keys":
            continue
        variable = configuration.VARIABLES[name]
        column = SiteSettings._meta.get_field(field)
        if column.default is not NOT_PROVIDED and variable.default is not None:
            default = column.default() if callable(column.default) else column.default
            assert (
                list(default) == list(variable.default)
                if isinstance(default, (list, tuple))
                else default == variable.default
            ), name
    paper_keys = {key for key, _label in paper.CHOICES}
    wanted = set(configuration.VARIABLES["POSTULO_PAPER_DEFAULT"].choices)
    assert wanted == paper_keys
    stored = {key for key, _label in SiteSettings._meta.get_field("backup_schedule").choices}
    assert set(configuration.VARIABLES["POSTULO_BACKUP_SCHEDULE"].choices) == stored
