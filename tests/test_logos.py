"""Company logos: fetched once, kept here, and served from here.

The whole point is the last part. Production sets ``img-src 'self'``, so a logo can never
be an ``<img>`` pointing at the company's own server — that would tell them, on every page
view, which companies this person is looking at and when. Everything below exists to make
"from a URL" mean "fetched once, by the server, and kept".
"""

from __future__ import annotations

import io

import httpx
import pytest
from django.urls import reverse
from PIL import Image

from postulo.jobs import logos
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


def an_image(*, size=(120, 40), fmt="PNG", colour=(20, 90, 200, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, colour).convert("RGBA" if fmt == "PNG" else "RGB").save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def web(monkeypatch):
    """A web that answers however the test says, without leaving the machine."""
    state = {
        "responses": {},
        "default": (404, b"", "text/plain"),
        "calls": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        state["calls"].append(url)
        status, body, content_type = state["responses"].get(url, state["default"])
        return httpx.Response(status, content=body, headers={"Content-Type": content_type})

    def client(**kwargs):
        kwargs.pop("event_hooks", None)
        kwargs.setdefault("timeout", 10)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(logos.http, "client", client)
    monkeypatch.setattr(logos.fetching, "validate_public_url", lambda url: url)
    return state


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Black Mesa", website="https://blackmesa.test")


# ------------------------------------------------------------------ the image


def test_a_wide_wordmark_is_fitted_rather_than_cropped():
    content = logos.process(an_image(size=(400, 100)))
    with Image.open(io.BytesIO(content.read())) as image:
        assert image.size == (logos.LOGO_SIZE, logos.LOGO_SIZE)
        assert image.mode == "RGBA"
        # The padding is transparent, so nothing of the name is lost and the tile lines up.
        assert image.getpixel((5, 5))[3] == 0


def test_something_that_is_not_an_image_is_refused():
    with pytest.raises(logos.UnusableLogo, match="could not be read as an image"):
        logos.process(b"<html>not a logo</html>")


# ---------------------------------------------------------------- fetching it


def test_a_logo_is_fetched_once_and_kept_here(web, company):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    logos.from_url(company, "https://cdn.example/logo.png")

    company.refresh_from_db()
    assert company.logo, "the file is ours now"
    assert company.logo_source == "url"
    assert company.logo_source_url == "https://cdn.example/logo.png"
    assert company.logo_fetched_at is not None
    assert web["calls"] == ["https://cdn.example/logo.png"], "one request, not one per view"


@pytest.mark.parametrize(
    "status,body,content_type,expected",
    [
        (404, b"", "text/html", "answered 404"),
        (200, b"", "image/png", "answered with nothing"),
        (200, b"<svg/>", "image/svg+xml", "SVG"),
        (200, b"hello", "text/html", "not an image Postulo keeps"),
    ],
)
def test_what_cannot_be_used_says_why(web, status, body, content_type, expected):
    web["responses"]["https://cdn.example/x"] = (status, body, content_type)
    with pytest.raises(logos.UnusableLogo, match=expected):
        logos.download("https://cdn.example/x")


def test_something_far_too_large_is_refused_before_it_is_decoded(web):
    web["responses"]["https://cdn.example/big.png"] = (
        200,
        b"\x89PNG" + b"\0" * (logos.MAX_BYTES + 1),
        "image/png",
    )
    with pytest.raises(logos.UnusableLogo, match="larger than a logo"):
        logos.download("https://cdn.example/big.png")


def test_a_private_address_is_never_fetched(monkeypatch, company):
    def refuse(url):
        raise logos.fetching.UnsafeURL("that address is not public.")

    monkeypatch.setattr(logos.fetching, "validate_public_url", refuse)
    with pytest.raises(logos.UnusableLogo, match="not public"):
        logos.from_url(company, "http://192.168.1.20/logo.png")
    company.refresh_from_db()
    assert not company.logo


# ------------------------------------------------------- finding one on a site


def a_page(**parts) -> bytes:
    head = "".join(parts.values())
    return f"<html><head>{head}</head><body>Black Mesa</body></html>".encode()


def test_the_site_is_asked_what_its_own_icon_is(web, company, monkeypatch):
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(
            url="https://blackmesa.test/",
            html=a_page(
                small='<link rel="icon" sizes="16x16" href="/small.png">',
                apple='<link rel="apple-touch-icon" sizes="180x180" href="/touch.png">',
            ).decode(),
        ),
    )
    web["responses"]["https://blackmesa.test/touch.png"] = (200, an_image(), "image/png")
    web["responses"]["https://blackmesa.test/small.png"] = (200, an_image(), "image/png")

    found = logos.find_on_website(company)
    assert found == "https://blackmesa.test/touch.png", "the largest declared icon first"
    company.refresh_from_db()
    assert company.logo and company.logo_source == "website"


def test_the_organisations_own_logo_and_the_favicon_are_tried_too(web, company, monkeypatch):
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(
            url="https://blackmesa.test/",
            html=a_page(
                ld=(
                    '<script type="application/ld+json">'
                    '{"@context":"https://schema.org","@type":"Organization",'
                    '"logo":{"url":"https://blackmesa.test/brand.png"}}'
                    "</script>"
                )
            ).decode(),
        ),
    )
    web["responses"]["https://blackmesa.test/brand.png"] = (200, an_image(), "image/png")
    assert logos.find_on_website(company) == "https://blackmesa.test/brand.png"

    # With nothing declared, the conventional address is the last thing tried.
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(url="https://blackmesa.test/", html=a_page()),
    )
    web["responses"]["https://blackmesa.test/favicon.ico"] = (200, an_image(), "image/png")
    assert logos.find_on_website(company).endswith("/favicon.ico")


def test_a_site_with_nothing_usable_says_so(web, company, monkeypatch):
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(url="https://blackmesa.test/", html=a_page()),
    )
    with pytest.raises(logos.UnusableLogo, match=r"Nothing on blackmesa\.test"):
        logos.find_on_website(company)


def test_a_company_with_no_website_has_nowhere_to_look(user):
    company = Company.objects.create(owner=user, name="Aperture")
    with pytest.raises(logos.UnusableLogo, match="no website"):
        logos.find_on_website(company)


# --------------------------------------------------------------- the interface


def test_the_form_fetches_a_logo_and_keeps_the_company_when_it_cannot(client, user, web):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    client.force_login(user)
    client.post(
        reverse("jobs:company_create"),
        {"name": "Black Mesa", "logo_url": "https://cdn.example/logo.png"},
    )
    company = Company.objects.for_user(user).get(name="Black Mesa")
    assert company.logo and company.logo_source == "url"

    response = client.post(
        reverse("jobs:company_create"),
        {"name": "Aperture", "logo_url": "https://cdn.example/missing.png"},
        follow=True,
    )
    assert "The logo was not changed" in response.content.decode()
    aperture = Company.objects.for_user(user).get(name="Aperture")
    assert not aperture.logo, "the company is still saved without it"


def test_a_logo_can_be_uploaded_and_removed(client, user, company):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(user)
    client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {
            "name": company.name,
            "website": company.website,
            "logo_upload": SimpleUploadedFile("logo.png", an_image(), content_type="image/png"),
        },
    )
    company.refresh_from_db()
    assert company.logo and company.logo_source == "upload"

    client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {"name": company.name, "website": company.website, "remove_logo": "on"},
    )
    company.refresh_from_db()
    assert not company.logo and company.logo_source == ""


def test_the_logo_is_served_from_this_instance_and_only_to_its_owner(
    client, user, other_user, company, web
):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    logos.from_url(company, "https://cdn.example/logo.png")

    client.force_login(user)
    response = client.get(reverse("jobs:company_logo", args=[company.pk]))
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, max-age=86400"
    assert b"".join(response.streaming_content)[1:4] == b"PNG"
    # A file response holds the file open until it is read and closed; leaving it open
    # would keep the handle alive into whatever test the collector happens to run in.
    response.close()

    client.force_login(other_user)
    assert client.get(reverse("jobs:company_logo", args=[company.pk])).status_code == 404


def test_a_company_page_shows_the_logo_and_never_the_far_address(client, user, company, web):
    client.force_login(user)
    html = client.get(company.get_absolute_url()).content.decode()
    assert "BM" in html, "initials until there is a logo"
    assert "Find logo" in html

    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    logos.from_url(company, "https://cdn.example/logo.png")
    html = client.get(company.get_absolute_url()).content.decode()
    assert reverse("jobs:company_logo", args=[company.pk]) in html
    assert "cdn.example" not in html, "the far address never reaches a page"
    assert "Refresh logo" in html


def test_find_logo_and_refresh_are_only_ever_pressed_by_a_person(
    client, user, company, web, monkeypatch
):
    client.force_login(user)
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(
            url="https://blackmesa.test/",
            html=a_page(icon='<link rel="icon" href="/icon.png">').decode(),
        ),
    )
    web["responses"]["https://blackmesa.test/icon.png"] = (200, an_image(), "image/png")

    client.get(company.get_absolute_url())
    assert web["calls"] == [], "opening the page fetches nothing"

    response = client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "website"]), follow=True
    )
    assert "Found a logo" in response.content.decode()
    company.refresh_from_db()
    assert company.logo_source == "website"

    response = client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "refresh"]), follow=True
    )
    assert "Fetched again" in response.content.decode()

    response = client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "remove"]), follow=True
    )
    assert "The logo is gone" in response.content.decode()
    company.refresh_from_db()
    assert not company.logo


def test_logo_actions_are_private_to_the_owner(client, other_user, company):
    client.force_login(other_user)
    for action in ("website", "refresh", "remove"):
        url = reverse("jobs:company_logo_action", args=[company.pk, action])
        assert client.post(url).status_code == 404


def test_two_people_recording_the_same_company_keep_their_own(user, other_user, web):
    """Companies are owner-scoped on purpose; a shared file would say who else applied."""
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    mine = Company.objects.create(owner=user, name="Black Mesa")
    theirs = Company.objects.create(owner=other_user, name="Black Mesa")
    logos.from_url(mine, "https://cdn.example/logo.png")
    logos.from_url(theirs, "https://cdn.example/logo.png")
    assert mine.logo.name != theirs.logo.name
    assert f"logos/{user.pk}/" in mine.logo.name
    assert f"logos/{other_user.pk}/" in theirs.logo.name


def test_a_logo_travels_in_the_export_and_comes_back(user, other_user, web):
    """The file itself, not the address: an import must not have to fetch anything."""
    import json
    import zipfile

    from postulo.core import importer
    from postulo.core.export import write_archive

    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    company = Company.objects.create(owner=user, name="Black Mesa")
    logos.from_url(company, "https://cdn.example/logo.png")

    archive = write_archive(user)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("postulo.json"))
        exported = manifest["companies"][0]
        assert exported["logo_source"] == "url"
        assert exported["logo_source_url"] == "https://cdn.example/logo.png"
        assert exported["logo_file"].startswith("media/logos/")
        assert bundle.read(exported["logo_file"])[1:4] == b"PNG"

    archive.seek(0)
    with zipfile.ZipFile(archive) as bundle:
        importer.load(other_user, bundle)
    restored = Company.objects.for_user(other_user).get(name="Black Mesa")
    assert restored.logo, "the picture came with it; nothing was fetched"
    assert restored.logo_source == "url"
    assert restored.logo_fetched_at is not None
    assert web["calls"] == ["https://cdn.example/logo.png"], "still just the one fetch"
