"""Messages people read that once bypassed gettext (#566)."""

from __future__ import annotations

import pytest
from django.utils.functional import Promise

from postulo.plugins import registry, secrets
from postulo.resume.forms import ExperienceForm


@pytest.mark.parametrize("name", ["browser", "email"])
def test_a_notifier_label_is_translated_when_read(name):
    label = registry.find_plugin("notifier", name).label
    assert isinstance(label, Promise), "a literal string would stay English in every language"


def test_the_end_before_start_error_goes_through_gettext(user):
    form = ExperienceForm(
        data={
            "role": "Engineer",
            "organisation": "Acme",
            "start_date": "2024-05-01",
            "end_date": "2024-01-01",
        },
        user=user,
    )
    assert not form.is_valid()
    error = form.errors.as_data()["end_date"][0]
    assert isinstance(error.message, Promise)
    assert form.errors["end_date"] == ["This is before the start date."]


def test_the_unreadable_secrets_message_is_looked_up_in_the_active_language(settings):
    token = secrets.encrypt({"token": "x"})
    settings.POSTULO_FIELD_KEY = "another"
    from unittest import mock

    with mock.patch.object(secrets, "_", side_effect=lambda text: "TRANSLATED") as lookup:
        with pytest.raises(secrets.SecretsUnreadable, match="TRANSLATED"):
            secrets.decrypt(token)
    assert "different key" in lookup.call_args.args[0]
