"""Server settings > Backups: the boundary around a file holding everybody's data (#242).

What has to hold: only an administrator reaches any of it, and as a 404; the two sensitive
actions need a recent authentication and leave a line in the log; an archive is only ever
named, never a path; and an upload that is too big or not ours is refused before it is kept.
"""

from __future__ import annotations

import io
import logging
import os

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.core import backups
from tests.test_backups_page import make_archive

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture(autouse=True)
def _a_directory_of_its_own(settings, tmp_path):
    settings.POSTULO_BACKUP_DIR = tmp_path / "backups"
    settings.POSTULO_BACKUP_DIR.mkdir()
    settings.POSTULO_BACKGROUND_WORK = False


@pytest.fixture
def admin(db):
    person = User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )
    EmailAddress.objects.create(user=person, email=person.email, verified=True, primary=True)
    return person


def sign_in_properly(client, person):
    """Through the form, so allauth records the authentication that reauthentication reads."""
    response = client.post(
        reverse("account_login"), {"login": person.username, "password": PASSWORD}
    )
    assert response.status_code == 302, response.content[:300]


def every_address(name: str) -> list[tuple[str, str]]:
    return [
        ("get", reverse("server:backups")),
        ("post", reverse("server:backups")),
        ("post", reverse("server:backup_run")),
        ("post", reverse("server:backup_upload")),
        ("get", reverse("server:backup_download", args=[name])),
        ("post", reverse("server:backup_verify", args=[name])),
        ("get", reverse("server:backup_delete", args=[name])),
        ("post", reverse("server:backup_delete", args=[name])),
        ("get", reverse("server:backup_restore", args=[name])),
    ]


# ------------------------------------------------------------------------ who


def test_everybody_but_an_administrator_gets_a_404(client, user):
    archive = make_archive()
    client.force_login(user)

    for method, url in every_address(archive.name):
        assert getattr(client, method)(url).status_code == 404, (method, url)
    assert archive.exists()


def test_a_signed_out_visitor_is_sent_to_sign_in(client):
    archive = make_archive()

    for method, url in every_address(archive.name):
        response = getattr(client, method)(url)
        assert response.status_code == 302 and "login" in response["Location"], (method, url)


# ------------------------------------------------------------------------ re-authentication


def test_download_needs_a_recent_authentication(client, admin):
    archive = make_archive()
    client.force_login(admin)  # no record of having authenticated: not recent enough

    response = client.get(reverse("server:backup_download", args=[archive.name]))

    assert response.status_code == 302 and "reauthenticate" in response["Location"]


def test_download_streams_an_attachment_and_names_who_took_it(client, admin, caplog):
    archive = make_archive()
    sign_in_properly(client, admin)

    with caplog.at_level(logging.INFO):
        response = client.get(reverse("server:backup_download", args=[archive.name]))

    assert response.status_code == 200 and response.streaming
    assert (
        "attachment" in response["Content-Disposition"]
        and archive.name in response["Content-Disposition"]
    )
    assert response["Cache-Control"] == "no-store"
    assert b"".join(response.streaming_content) == archive.read_bytes()
    assert any(
        archive.name in r.getMessage() and "admin-one" in r.getMessage() for r in caplog.records
    )


def test_delete_needs_a_recent_authentication_for_the_page_and_for_the_post(client, admin):
    archive = make_archive()
    client.force_login(admin)
    url = reverse("server:backup_delete", args=[archive.name])

    for response in (client.get(url), client.post(url)):
        assert response.status_code == 302 and "reauthenticate" in response["Location"]
    assert archive.exists()


def test_delete_removes_the_file_and_says_who(client, admin, caplog):
    keep = make_archive("20260101-030000")
    gone = make_archive("20260102-030000")
    backups.verify(keep)
    sign_in_properly(client, admin)

    with caplog.at_level(logging.INFO):
        page = client.get(reverse("server:backup_delete", args=[gone.name]))
        response = client.post(reverse("server:backup_delete", args=[gone.name]))

    assert gone.name in page.content.decode()
    assert response.status_code == 302 and not gone.exists() and keep.exists()
    assert any(
        gone.name in r.getMessage() and "admin-one" in r.getMessage() for r in caplog.records
    )


def test_the_last_trusted_archive_is_not_deleted(client, admin):
    only = make_archive()
    backups.verify(only)
    sign_in_properly(client, admin)

    client.post(reverse("server:backup_delete", args=[only.name]))

    assert only.exists()


# ------------------------------------------------------------------------ names


@pytest.mark.parametrize(
    "name",
    [
        "..%2Fsecret.tar.gz",
        "%2Fetc%2Fpasswd",
        "..",
        "passwd",
        "postulo-backup-1.tar.gz",
        "postulo-backup-20260101-030000.tar.gz%00.txt",
        "postulo-backup-20260101-030000.tar.gz.part",
        "POSTULO-BACKUP-20260101-030000.TAR.GZ",
    ],
)
def test_a_name_that_is_not_one_of_ours_is_a_404(client, admin, name):
    sign_in_properly(client, admin)
    for action in ("download", "verify", "delete", "restore"):
        ask = client.post if action == "verify" else client.get
        response = ask(f"/server/backups/{name}/{action}/")
        assert response.status_code == 404, (name, action)


def test_the_resolver_refuses_paths_and_non_names():
    make_archive()
    for bad in ("../x.tar.gz", "/etc/passwd", "a/postulo-backup-20260101-030000.tar.gz", "", None):
        with pytest.raises(backups.NotAnArchive):
            backups.archive_path(bad)


def test_a_symlink_out_of_the_directory_is_refused(settings, tmp_path):
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(b"secret")
    link = backups.directory() / "postulo-backup-20260101-030000.tar.gz"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("this system will not make a symlink here")

    with pytest.raises(backups.NotAnArchive):
        backups.archive_path(link.name)
    assert backups.archives() == []


def test_a_directory_that_is_a_symlink_to_elsewhere_serves_only_its_own_files(settings, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    settings.POSTULO_BACKUP_DIR = tmp_path / "linked"
    try:
        os.symlink(real, settings.POSTULO_BACKUP_DIR, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system will not make a symlink here")
    archive = make_archive()

    assert backups.archive_path(archive.name).name == archive.name


# ------------------------------------------------------------------------ upload


def upload(client, content: bytes, name="mine.tar.gz"):
    return client.post(
        reverse("server:backup_upload"),
        {"archive": SimpleUploadedFile(name, content, content_type="application/gzip")},
        follow=True,
    )


def test_an_upload_over_the_limit_is_refused_and_nothing_is_kept(client, admin, settings):
    settings.POSTULO_BACKUP_UPLOAD_MAX_MB = 0
    client.force_login(admin)

    response = upload(client, b"x" * 10)

    assert "larger than" in response.content.decode()
    assert list(backups.directory().iterdir()) == []


def test_something_that_is_not_an_archive_is_refused_and_nothing_is_kept(client, admin):
    client.force_login(admin)

    response = upload(client, b"just some words")

    assert "not a Postulo backup" in response.content.decode()
    assert list(backups.directory().iterdir()) == [], "not even a temporary file"


def test_a_tar_that_is_not_ours_is_refused(client, admin):
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"hi"))
    client.force_login(admin)

    response = upload(client, buffer.getvalue())

    assert "not a Postulo backup" in response.content.decode()
    assert list(backups.directory().iterdir()) == []


def test_one_of_ours_is_kept_and_leads_to_its_restore_page(client, admin):
    source = make_archive("20260101-030000")
    mine = source.read_bytes()
    source.unlink()
    client.force_login(admin)

    response = upload(client, mine)

    kept = [a.name for a in backups.archives()]
    assert len(kept) == 1 and kept[0].startswith("postulo-upload-")
    assert 'data-restore="verified"' in response.content.decode()
    assert backups.archives()[0].uploaded
