"""Pictures of people: uploads re-encoded, a Gravatar fetched once, initials otherwise."""

import io
import zipfile
from typing import ClassVar

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.urls import reverse
from PIL import Image

from postulo.accounts import avatars
from postulo.accounts.models import Profile
from postulo.core import export as export_module
from postulo.core import importer

pytestmark = pytest.mark.django_db


def picture_bytes(size=(300, 200), fmt="JPEG", orientation=None) -> bytes:
    image = Image.new("RGB", size, "orange")
    out = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        exif[0x010E] = "taken at 51.5,-0.1"  # an ImageDescription, standing in for a location
        image.save(out, format=fmt, exif=exif.tobytes())
    else:
        image.save(out, format=fmt)
    return out.getvalue()


def profile_page(client, user, **fields):
    data = {"first_name": "Alex", "last_name": "Morgan", **fields}
    return client.post(reverse("accounts:profile"), data)


# ------------------------------------------------------------------ processing


def test_an_upload_becomes_a_square_png_without_its_metadata():
    original = picture_bytes((300, 200), orientation=6)
    content = avatars.process(original)
    with Image.open(io.BytesIO(content.read())) as image:
        assert image.format == "PNG"
        assert image.size == (avatars.AVATAR_SIZE, avatars.AVATAR_SIZE)
        assert not image.getexif(), "nothing the phone knew survives"


def test_rubbish_and_bombs_are_refused():
    with pytest.raises(avatars.UnusableImage):
        avatars.process(b"%PDF-1.4 not a picture")
    huge = Image.new("1", (9000, 9000))
    out = io.BytesIO()
    huge.save(out, format="PNG")
    with pytest.raises(avatars.UnusableImage):
        avatars.process(out.getvalue())


def test_the_gravatar_address_is_sha256_of_the_lowercased_email_and_asks_for_a_404():
    url = avatars.gravatar_url("  Applicant@Example.org ")
    assert url.startswith("https://gravatar.com/avatar/")
    assert avatars.gravatar_hash("applicant@example.org") in url
    assert url.endswith("?s=256&d=404")


# --------------------------------------------------------------------- uploads


def test_uploading_a_picture_shows_it_in_the_header_and_serves_it_privately(
    client, user, other_user
):
    client.force_login(user)
    response = profile_page(
        client,
        user,
        picture=SimpleUploadedFile("me.jpg", picture_bytes(), content_type="image/jpeg"),
    )
    assert response.status_code == 302
    profile = Profile.objects.get(user=user)
    assert profile.avatar.name.startswith(f"avatars/{user.pk}/")
    assert profile.picture == profile.avatar

    header = client.get(reverse("core:home")).content.decode()
    avatar_url = reverse("accounts:avatar", args=[user.pk])
    assert f'<img src="{avatar_url}?v=' in header

    served = client.get(avatar_url)
    assert served.status_code == 200
    assert served["Content-Type"] == "image/png"
    assert served["Cache-Control"] == "private, max-age=86400"
    body = b"".join(served.streaming_content) if served.streaming else served.content
    served.close()
    assert body.startswith(b"\x89PNG")

    client.force_login(other_user)
    assert client.get(avatar_url).status_code == 404, "another person's picture is not theirs"
    assert client.get(reverse("accounts:avatar", args=[other_user.pk])).status_code == 404


def test_an_administrator_may_see_anyones_picture(client, user, other_user):
    other_user.is_staff = True
    other_user.save()
    profile = Profile.objects.get(user=user)
    avatars.store(profile, "avatar", avatars.process(picture_bytes()), "avatar")
    profile.save()
    client.force_login(other_user)
    response = client.get(reverse("accounts:avatar", args=[user.pk]))
    assert response.status_code == 200
    response.close()


def test_the_form_refuses_what_it_should(client, user):
    client.force_login(user)
    too_big = SimpleUploadedFile(
        "big.png", b"x" * (avatars.MAX_UPLOAD_BYTES + 1), content_type="image/png"
    )
    response = profile_page(client, user, picture=too_big)
    assert response.status_code == 200 and "over 5 MB" in response.content.decode()

    wrong_type = SimpleUploadedFile("cv.pdf", b"%PDF-1.4", content_type="application/pdf")
    response = profile_page(client, user, picture=wrong_type)
    assert response.status_code == 200 and "PNG, JPEG, WebP or GIF" in response.content.decode()

    corrupt = SimpleUploadedFile("x.png", b"not really png bytes", content_type="image/png")
    response = profile_page(client, user, picture=corrupt)
    assert response.status_code == 200 and "could not be read" in response.content.decode()
    assert not Profile.objects.get(user=user).avatar


def test_removing_the_picture_brings_the_initials_back(client, user):
    client.force_login(user)
    profile_page(
        client,
        user,
        picture=SimpleUploadedFile("me.png", picture_bytes(fmt="PNG"), content_type="image/png"),
    )
    profile = Profile.objects.get(user=user)
    stored = profile.avatar.path
    assert profile.avatar

    profile_page(client, user, remove_picture="on")
    profile.refresh_from_db()
    assert not profile.avatar
    import os

    assert not os.path.exists(stored), "the file goes with the field"
    header = client.get(reverse("core:home")).content.decode()
    assert "<img" not in header.split("Account menu")[1].split("</summary>")[0]
    assert ">AM</span>" in header


# -------------------------------------------------------------------- gravatar


class FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


class FakeClient:
    """Stands in for the guarded HTTP client; records what was asked for."""

    calls: ClassVar[list[str]] = []
    answer: ClassVar[FakeResponse] = FakeResponse(404)

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        FakeClient.calls.append(url)
        return FakeClient.answer


@pytest.fixture
def gravatar(monkeypatch):
    FakeClient.calls = []
    FakeClient.answer = FakeResponse(404)
    monkeypatch.setattr(avatars.http, "client", FakeClient)
    return FakeClient


def test_opting_in_fetches_once_server_side_and_serves_the_copy(client, user, gravatar):
    gravatar.answer = FakeResponse(200, picture_bytes((80, 80), fmt="PNG"))
    client.force_login(user)
    response = profile_page(client, user, use_gravatar="on")
    assert response.status_code == 302
    assert gravatar.calls == [avatars.gravatar_url(user.email)], "one request, from the server"

    profile = Profile.objects.get(user=user)
    assert profile.use_gravatar and profile.gravatar_image and profile.gravatar_checked_at
    assert profile.picture == profile.gravatar_image

    page = client.get(reverse("accounts:profile")).content.decode()
    assert "From Gravatar" in page
    assert "gravatar.com" not in client.get(reverse("core:home")).content.decode(), (
        "the page never points the browser at Gravatar"
    )

    for _ in range(3):
        client.get(reverse("core:home"))
    assert len(gravatar.calls) == 1, "page views fetch nothing"


def test_no_gravatar_means_initials_and_an_honest_note(client, user, gravatar):
    client.force_login(user)
    response = profile_page(client, user, use_gravatar="on")
    assert response.status_code == 302
    profile = Profile.objects.get(user=user)
    assert profile.use_gravatar and not profile.gravatar_image and profile.gravatar_checked_at
    assert profile.picture is None
    page = client.get(reverse("accounts:profile"), follow=True).content.decode()
    assert "data-no-gravatar" in page and "Gravatar has no picture" in page


def test_the_upload_beats_the_gravatar_and_opting_out_deletes_the_copy(client, user, gravatar):
    gravatar.answer = FakeResponse(200, picture_bytes((80, 80), fmt="PNG"))
    client.force_login(user)
    profile_page(
        client,
        user,
        use_gravatar="on",
        picture=SimpleUploadedFile("me.png", picture_bytes(fmt="PNG"), content_type="image/png"),
    )
    profile = Profile.objects.get(user=user)
    assert profile.picture == profile.avatar, "uploaded first, Gravatar second"
    copy = profile.gravatar_image.path

    profile_page(client, user, picture="", remove_picture="")  # use_gravatar unticked
    profile.refresh_from_db()
    import os

    assert not profile.use_gravatar and not profile.gravatar_image
    assert not os.path.exists(copy)
    assert profile.gravatar_checked_at is None


def test_refresh_asks_again_and_a_new_primary_address_refetches(client, user, gravatar):
    client.force_login(user)
    profile_page(client, user, use_gravatar="on")
    assert len(gravatar.calls) == 1

    response = client.post(reverse("accounts:avatar_refresh"))
    assert response.status_code == 302 and len(gravatar.calls) == 2

    from allauth.account.signals import email_changed

    email_changed.send(
        sender=None, request=None, user=user, from_email_address=None, to_email_address=None
    )
    assert len(gravatar.calls) == 3

    profile = Profile.objects.get(user=user)
    profile.use_gravatar = False
    profile.save()
    email_changed.send(
        sender=None, request=None, user=user, from_email_address=None, to_email_address=None
    )
    assert len(gravatar.calls) == 3, "nobody asked"


def test_a_failing_gravatar_is_reported_not_fatal(client, user, monkeypatch):
    class Boom(FakeClient):
        def get(self, url):
            raise OSError("no route")

    monkeypatch.setattr(avatars.http, "client", Boom)
    client.force_login(user)
    response = profile_page(client, user, use_gravatar="on")
    assert response.status_code == 302
    page = client.get(reverse("accounts:profile")).content.decode()
    assert "could not be reached" in page
    profile = Profile.objects.get(user=user)
    assert profile.use_gravatar and not profile.gravatar_image


# ----------------------------------------------------------------- export, import


def test_the_uploaded_picture_travels_in_the_export(user, other_user):
    profile = Profile.objects.get(user=user)
    avatars.store(profile, "avatar", avatars.process(picture_bytes()), "avatar")
    profile.use_gravatar = True
    profile.save()

    document = export_module.build_document(user)
    assert document["account"]["avatar_file"] == f"media/{profile.avatar.name}"
    assert document["account"]["profile"]["use_gravatar"] is True
    archive = zipfile.ZipFile(export_module.write_archive(user))
    assert f"media/{profile.avatar.name}" in archive.namelist()

    importer.load(other_user, archive)
    restored = Profile.objects.get(user=other_user)
    assert restored.avatar and restored.avatar.name.startswith(f"avatars/{other_user.pk}/")
    assert restored.use_gravatar is True
    assert not restored.gravatar_image, "a Gravatar copy is refetched, never copied"


# ------------------------------------------------------------------ the tag


def test_the_tag_still_draws_initials_for_a_user_without_a_profile():
    class Bare:
        pk = None
        first_name = "Alex"
        last_name = "Morgan"
        display_name = "Alex Morgan"

    rendered = Template("{% load postulo %}{% avatar u %}").render(Context({"u": Bare()}))
    assert ">AM</span>" in rendered and "<img" not in rendered
