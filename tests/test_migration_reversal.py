"""Migrations that promise a way back keep the promise, or refuse it plainly (#571).

An operator who rolls back after a bad upgrade should get a working rollback or an honest
``IrreversibleError`` before anything is touched, and never an ``IntegrityError`` halfway.
"""

import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def migrate(*targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(list(targets))
    # The state of everything now applied, not of the targets' ancestors alone: the user
    # table is the database's, whichever migration of its own app it has reached.
    loader = MigrationExecutor(connection).loader
    return loader.project_state(list(loader.applied_migrations)).apps


@pytest.fixture
def at_the_latest_migrations():
    """Whatever a test rolls back, put the schema where the other tests expect it."""
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_documents_0005_carries_a_copy_back_to_its_upload(at_the_latest_migrations):
    apps = migrate(("documents", "0005_polymorphic_links"))
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


def test_accounts_0021_refuses_to_be_undone(at_the_latest_migrations):
    apps = migrate(("accounts", "0021_invite_token_fingerprint"))
    Invite = apps.get_model("accounts", "Invite")
    Invite.objects.create(token_fingerprint="a" * 64)
    Invite.objects.create(token_fingerprint="b" * 64)

    with pytest.raises(IrreversibleError):
        migrate(("accounts", "0020_profile_keyboard_shortcuts"))
