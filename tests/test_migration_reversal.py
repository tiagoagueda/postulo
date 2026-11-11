"""Migrations that promise a way back keep the promise, or refuse it plainly (#571).

An operator who rolls back after a bad upgrade should get a working rollback or an honest
``IrreversibleError`` before anything is touched, and never an ``IntegrityError`` halfway.
"""

import contextlib

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

#: The database is set up for these tests as for any other transactional test, which a run of
#: this file alone needs (pytest-django creates the test database only for the aliases its
#: marked tests name). One worker takes the whole file under `--dist loadgroup`, so the way
#: back and the way forward the tests share are gone over once in a run (#1033).
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.xdist_group("migration_reversal"),
]

#: Where each test starts: the migration it then undoes, or is refused.
DOCUMENTS = ("documents", "0005_polymorphic_links")
ACCOUNTS = ("accounts", "0021_invite_token_fingerprint")


def migrate(*targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(list(targets))
    # The state of everything now applied, not of the targets' ancestors alone: the user
    # table is the database's, whichever migration of its own app it has reached.
    loader = MigrationExecutor(connection).loader
    return loader.project_state(list(loader.applied_migrations)).apps


@pytest.fixture(scope="module")
def rolled_back(django_db_setup, django_db_blocker):
    """One way back for the file and one way forward again, instead of one each a test (#1033).

    Each test used to go back from the latest migrations to where it starts and forward again
    afterwards, most of the same migrations each time, which was most of a minute of a
    worker. Here the schema goes back once to where both tests start, with nothing in the
    tables, as each of them met it, and a test's own `migrate` to its starting point then
    finds it there. Each test reads and writes its own app's tables, and this fixture, not
    the test before, sets where both start, so neither order of the two changes what the
    other finds.

    The way forward is gone once too, after the last test, over every row the tests wrote, as
    each test's own way forward was; and the tables are emptied after it, as a transactional
    test leaves them. So the emptying a transactional test does after itself is held back
    while this file runs: it would come before the way forward, take the rows it was to go
    over, and run on a schema that is part of the way back.

    A module's fixture is finished before a test of another file runs, whichever worker this
    is and whether or not a test here failed, so nothing outside this file meets a schema
    that is part of the way back, or a test that does not empty its tables.
    """
    with django_db_blocker.unblock(), pytest.MonkeyPatch.context() as patch:
        patch.setattr(TransactionTestCase, "_fixture_teardown", lambda test: None)
        try:
            # A way back that fails is the tests' to report, not this fixture's: each makes
            # its own from wherever this one stopped, so a migration that cannot be undone
            # fails the test whose way it is on and leaves the other to pass.
            with contextlib.suppress(Exception):
                MigrationExecutor(connection).migrate([DOCUMENTS, ACCOUNTS])
            yield
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            call_command("flush", verbosity=0, interactive=False, reset_sequences=False)


def test_documents_0005_carries_a_copy_back_to_its_upload(rolled_back):
    apps = migrate(DOCUMENTS)
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    ContentType = apps.get_model("contenttypes", "ContentType")
    UploadedDocument = apps.get_model("documents", "UploadedDocument")
    DocumentCopy = apps.get_model("documents", "DocumentCopy")

    owner = User.objects.create(email="owner@example.org")
    upload = UploadedDocument.objects.create(owner=owner, title="Certificate", file="x.pdf")
    upload_type = ContentType.objects.get_for_model(UploadedDocument)
    copy = DocumentCopy.objects.create(
        owner=owner, store="paperless", document_type=upload_type, document_id=upload.pk
    )

    apps = migrate(("documents", "0004_letter_language"))

    assert apps.get_model("documents", "DocumentCopy").objects.get(pk=copy.pk).upload_id == (
        upload.pk
    )


def test_accounts_0021_refuses_to_be_undone(rolled_back):
    apps = migrate(ACCOUNTS)
    Invite = apps.get_model("accounts", "Invite")
    Invite.objects.create(token_fingerprint="a" * 64)
    Invite.objects.create(token_fingerprint="b" * 64)

    with pytest.raises(IrreversibleError):
        migrate(("accounts", "0020_profile_keyboard_shortcuts"))
