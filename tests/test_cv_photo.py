"""A photograph for CVs, of its own, printed only where a CV says so (#668).

The avatar is a square tile made for the interface and may be an illustration; a CV photo
is another file, kept as it was framed, and reaches a document only as a ``data:`` address
and only when the CV's own switch is on. What is held here:

- the two pictures are independent, and the photo is neither cropped nor kept with what
  the file knew about where it was taken;
- it goes with its owner;
- the renderer is handed an empty ``photo`` unless the CV says so, and never past the
  master switch;
- both built-in themes print the frame, and are what they were without a photo;
- the switch, the API, the archive and the importer carry it.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from postulo.accounts import avatars
from postulo.accounts.models import Profile, ProfilePicture
from postulo.api.models import ApiToken
from postulo.core import export as export_module
from postulo.core import importer
from postulo.documents import printing, rendering
from postulo.documents.models import CV

pytestmark = pytest.mark.django_db


def image_bytes(size=(300, 400), colour="orange", fmt="PNG", exif=False) -> bytes:
    image = Image.new("RGB", size, colour)
    out = io.BytesIO()
    if exif:
        tags = Image.Exif()
        tags[0x010E] = "taken at 51.5,-0.1"
        image.save(out, format=fmt, exif=tags.tobytes())
    else:
        image.save(out, format=fmt)
    return out.getvalue()


def upload(name="me.png", data=None, kind="image/png"):
    return SimpleUploadedFile(name, data if data is not None else image_bytes(), content_type=kind)


def profile_post(client, **files):
    return client.post(
        reverse("accounts:profile"), {"first_name": "Alex", "last_name": "Morgan", **files}
    )


def stored(user, kind) -> bytes | None:
    row = ProfilePicture.objects.filter(profile__user=user, kind=kind).first()
    return bytes(row.data) if row else None


def keep_photo(user, **kwargs) -> None:
    avatars.store(user.profile, ProfilePicture.CV, avatars.process_cv_photo(image_bytes(**kwargs)))


def a_cv(user, **fields) -> CV:
    return CV.objects.create(owner=user, name="Main", theme="plain", **fields)


# ------------------------------------------------------------------ the picture itself


def test_the_photo_and_the_avatar_are_independent(client, user):
    client.force_login(user)
    profile_post(client, picture=upload(data=image_bytes((200, 200), "red")))
    profile_post(client, cv_photo=upload(data=image_bytes((300, 400), "blue")))
    profile = Profile.objects.get(user=user)
    assert profile.has_avatar and profile.has_cv_photo
    avatar_before = stored(user, ProfilePicture.UPLOAD)

    profile_post(client, cv_photo=upload(data=image_bytes((300, 400), "green")))
    assert stored(user, ProfilePicture.UPLOAD) == avatar_before, "the avatar was left alone"

    photo_before = stored(user, ProfilePicture.CV)
    profile_post(client, picture=upload(data=image_bytes((200, 200), "yellow")))
    assert stored(user, ProfilePicture.CV) == photo_before, "and so was the photo"

    profile_post(client, remove_cv_photo="on")
    profile.refresh_from_db()
    assert not profile.has_cv_photo and stored(user, ProfilePicture.CV) is None
    assert profile.has_avatar, "removing one leaves the other"


def test_the_avatar_view_never_serves_the_cv_photo(client, user):
    keep_photo(user)
    client.force_login(user)
    assert user.profile.picture is None
    assert client.get(reverse("accounts:avatar", args=[user.pk])).status_code == 404


def test_the_photo_is_not_cropped():
    content = avatars.process_cv_photo(image_bytes((300, 400)))
    with Image.open(io.BytesIO(content.read())) as image:
        assert image.size == (300, 400), "the person's own framing is kept"


def test_what_the_file_knew_is_dropped():
    content = avatars.process_cv_photo(image_bytes(exif=True, fmt="JPEG"))
    with Image.open(io.BytesIO(content.read())) as image:
        assert image.format == "PNG" and not image.getexif()


def test_a_file_that_is_not_an_image_is_refused(client, user):
    client.force_login(user)
    response = profile_post(client, cv_photo=upload("me.png", b"not a picture at all"))
    assert response.status_code == 200
    assert response.context["form"].errors.get("cv_photo")
    assert not Profile.objects.get(user=user).has_cv_photo
    with pytest.raises(avatars.UnusableImage):
        avatars.process_cv_photo(b"<svg xmlns='http://www.w3.org/2000/svg'/>")


def test_a_photo_over_the_cap_is_refused(client, user):
    client.force_login(user)
    big = upload(data=b"0" * (avatars.MAX_UPLOAD_BYTES + 1))
    response = profile_post(client, cv_photo=big)
    assert response.context["form"].errors.get("cv_photo")


def test_deleting_the_account_removes_the_photo(user):
    keep_photo(user)
    assert ProfilePicture.objects.filter(kind=ProfilePicture.CV).count() == 1
    user.delete()
    assert not ProfilePicture.objects.exists()


# ------------------------------------------------------------- what a document is handed


def test_a_cv_with_the_switch_off_is_handed_no_photo(user):
    keep_photo(user)
    cv = a_cv(user)
    assert rendering.contact_details(user, cv)["photo"] == ""
    assert rendering.contact_details(user)["photo"] == "", "a letter's sender block has none"


def test_a_cv_with_the_switch_on_is_handed_a_data_address(user):
    keep_photo(user)
    cv = a_cv(user, show_photo=True)
    assert rendering.contact_details(user, cv)["photo"].startswith("data:image/png;base64,")


def test_the_switch_on_and_no_photo_prints_nothing(user):
    cv = a_cv(user, show_photo=True)
    assert rendering.contact_details(user, cv)["photo"] == ""


def test_the_master_switch_takes_the_photo_with_the_name(user):
    keep_photo(user)
    cv = a_cv(user, show_photo=True, show_contact_details=False)
    assert rendering.cv_contact(cv) is None
    assert "data:image" not in rendering.render_cv_html(cv)


# ---------------------------------------------------------------------------- the themes


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_both_themes_print_the_frame_and_are_unchanged_without_one(user, theme, kind):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    cv = CV.objects.create(owner=user, name="Main", theme=theme, kind=kind, show_photo=True)
    without = rendering.render_cv_html(cv)
    assert "photo" not in without and "has-photo" not in without

    keep_photo(user)
    html = rendering.render_cv_html(CV.objects.get(pk=cv.pk))
    assert re.search(r'<img class="photo" src="data:image/png;base64,[^"]+" alt="">', html)
    assert "35mm" in html and "object-fit: cover" in html

    cv.show_photo = False
    cv.save()
    assert rendering.render_cv_html(CV.objects.get(pk=cv.pk)) == without


def test_a_theme_that_ignores_the_photo_is_not_broken(user):
    keep_photo(user)
    cv = a_cv(user, show_photo=True)
    details = rendering.contact_details(user, cv)
    assert set(details) >= {"name", "email", "phone", "details", "photo"}


# ----------------------------------------------------- the switch, the API, the archive


def test_the_switch_is_one_of_the_prints_switches():
    assert printing.SWITCHES["photo"] == "show_photo"
    assert not CV._meta.get_field("show_photo").default


def test_the_cv_form_carries_the_switch(user, client):
    keep_photo(user)
    client.force_login(user)
    response = client.post(
        reverse("documents:cv_create"),
        {
            "name": "Mine",
            "kind": "cv",
            "theme": "plain",
            "language": "",
            "show_contact_details": "on",
            "show_photo": "on",
            "prints_phone": "default",
            "prints_email": "default",
            "prints_social": "default",
            "prints_repository": "default",
            "prints_website": "default",
            "prints_identifiers": "default",
        },
    )
    assert response.status_code == 302, getattr(response, "context", None)
    assert CV.objects.get(owner=user, name="Mine").show_photo


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_reads_and_writes_the_switch(user, client):
    cv = a_cv(user)
    body = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user)).json()
    assert body["prints"]["photo"] is False
    response = client.patch(
        f"/api/v1/cvs/{cv.pk}",
        data=json.dumps({"prints": {"photo": True}}),
        content_type="application/json",
        **bearer(user, "write"),
    )
    assert response.status_code == 200, response.content
    assert response.json()["prints"]["photo"] is True
    cv.refresh_from_db()
    assert cv.show_photo


def test_the_archive_carries_the_photo_and_the_switch(user, other_user):
    keep_photo(user, size=(210, 270), colour="purple")
    a_cv(user, show_photo=True)
    document = export_module.build_document(user)
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION
    assert document["account"]["cv_photo_file"]
    assert document["account"]["avatar_file"] == ""
    assert document["documents"]["cvs"][0]["prints"]["photo"] is True

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))
    other = Profile.objects.get(user=other_user)
    assert other.has_cv_photo and not other.has_avatar
    assert stored(other_user, ProfilePicture.CV) == stored(user, ProfilePicture.CV)
    assert CV.objects.get(owner=other_user, name="Main").show_photo


def test_an_older_archive_restores_with_no_photo_and_the_switch_off(user, other_user):
    keep_photo(user)
    a_cv(user, show_photo=True)
    buffer = export_module.write_archive(user)
    with zipfile.ZipFile(buffer) as archive:
        document = json.loads(archive.read(export_module.MANIFEST_NAME))
    document["postulo"]["format"] = export_module.FORMAT_VERSION - 1
    del document["account"]["cv_photo_file"]
    del document["documents"]["cvs"][0]["prints"]["photo"]
    older = io.BytesIO()
    with zipfile.ZipFile(older, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    older.seek(0)
    importer.load(other_user, zipfile.ZipFile(older))
    assert not Profile.objects.get(user=other_user).has_cv_photo
    assert not CV.objects.get(owner=other_user, name="Main").show_photo


def test_the_candidate_file_leaves_the_photo_out(user):
    keep_photo(user)
    from postulo.core.export import build_candidate_document

    assert "cv_photo" not in json.dumps(build_candidate_document(user))
