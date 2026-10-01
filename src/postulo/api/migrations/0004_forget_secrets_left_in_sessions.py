"""Take the secrets of new API tokens out of the sessions they were left in (#441).

A new token's secret used to wait in the session for the page after a redirect, which read
it and removed it. A session is a row in ``django_session``, signed and not encrypted, and
when that page was never drawn the secret stayed in the row: in the database, and in every
backup of it. The view no longer puts it there. This removes the ones already put, so that
upgrading makes "a copy of the database is not a set of working credentials" true of the
rows an instance already has, and not only of tokens made from now on.

Only that one key is removed. Whoever the session belongs to stays signed in.
"""

from django.db import migrations

#: The key the token views used, spelt out here because the views no longer have it.
LEFT_BEHIND = "postulo_new_capture_token"


def forget_secrets_left_in_sessions(apps, schema_editor):
    from django.contrib.sessions.backends.db import SessionStore

    Session = apps.get_model("sessions", "Session")
    store = SessionStore()
    for row in Session.objects.all().iterator():
        # A row this instance can no longer read (its secret key changed since) decodes to
        # nothing, and is left exactly as it is: it cannot be read by anybody else either.
        held = store.decode(row.session_data)
        if LEFT_BEHIND in held:
            del held[LEFT_BEHIND]
            row.session_data = store.encode(held)
            row.save(update_fields=["session_data"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0003_idempotentrequest"),
        ("sessions", "0001_initial"),
    ]

    operations = [
        # Nothing to put back: the secrets were never meant to be there.
        migrations.RunPython(forget_secrets_left_in_sessions, migrations.RunPython.noop),
    ]
