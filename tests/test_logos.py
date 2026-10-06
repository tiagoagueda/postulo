"""Company logos: fetched once, kept here, and served from here.

The whole point is the last part. Production sets ``img-src 'self'``, so a logo can never
be an ``<img>`` pointing at the company's own server — that would tell them, on every page
view, which companies this person is looking at and when. Everything below exists to make
"from a URL" mean "fetched once, by the server, and kept".
"""

from __future__ import annotations

import io
import os
from types import SimpleNamespace

import httpx
import pytest
from django.db import DatabaseError, OperationalError
from django.urls import reverse
from PIL import Image

from postulo.core import pictures
from postulo.core.models import Errand
from postulo.jobs import logos
from postulo.jobs.models import Company, CompanyLogo

pytestmark = pytest.mark.django_db


def an_image(*, size=(120, 40), fmt="PNG", colour=(20, 90, 200, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, colour).convert("RGBA" if fmt == "PNG" else "RGB").save(out, format=fmt)
    return out.getvalue()


def a_photograph(size=(900, 900)) -> bytes:
    """Noise, so it does not compress away and the budget actually has to engage.

    `random` rather than `secrets` on purpose: this is a picture, and a seeded one so the
    test weighs the same every run.
    """
    import random

    generator = random.Random(7)  # noqa: S311
    image = Image.new("RGB", size)
    image.putdata(
        [
            (generator.randrange(256), generator.randrange(256), generator.randrange(256))
            for _ in range(size[0] * size[1])
        ]
    )
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
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

    # Both: a logo is fetched with the public-only client (#215), and `fetch_page` — which
    # `find_on_website` uses to read the company's own page — opens one too.
    monkeypatch.setattr(logos.http, "client", client)
    monkeypatch.setattr(logos.http, "public_only_client", client)
    monkeypatch.setattr(logos.fetching, "validate_public_url", lambda url: url)
    return state


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Black Mesa", website="https://blackmesa.test")


# ------------------------------------------------------------------ the image


def test_a_wide_wordmark_keeps_its_own_shape_and_size(company):
    """The 256-pixel square is gone (#264): a logo is bounded by bytes, not by dimensions.

    Nothing of the name was lost before either -- it was letterboxed onto a transparent
    square. What is different is that the square is CSS's job now, and what is kept is what
    was given: a design that wants more than 56 pixels later is not blocked by a decision
    taken today, and the original bytes are not kept to go back to.
    """
    content = logos.process(an_image(size=(400, 100)))

    with Image.open(io.BytesIO(content.read())) as image:
        assert image.size == (400, 100), "kept, not flattened onto a square"
        assert image.mode == "RGBA"


def test_a_picture_too_heavy_for_the_budget_is_reduced_until_it_fits(monkeypatch):
    """The budget exists to stop one file being unreasonable, not to pick a size.

    Re-encoding can *grow* a file -- a photographic logo arriving as JPEG and leaving as
    PNG -- so the rule cannot be "refuse what is too big"; it has to reduce until it fits.
    """
    monkeypatch.setattr(logos, "MAX_STORED_BYTES", 20_000)
    content = logos.process(a_photograph((900, 900)))

    written = content.read()
    assert len(written) <= 20_000
    with Image.open(io.BytesIO(written)) as image:
        assert max(image.size) < 900, "reduced"
        assert max(image.size) >= 64, "and not reduced to nothing"


def test_a_file_that_cannot_be_made_to_fit_is_refused_rather_than_looping(monkeypatch):
    """The floor under the reduction. Without it a pathological file spins for ever."""
    monkeypatch.setattr(logos, "MAX_STORED_BYTES", 100)

    with pytest.raises(logos.UnusableLogo, match="cannot be made small enough"):
        logos.process(a_photograph((900, 900)))


def test_something_that_is_not_an_image_is_refused():
    with pytest.raises(logos.UnusableLogo, match="could not be read as an image"):
        logos.process(b"<html>not a logo</html>")


# --------------------------------------------------------------------- a vector


def test_an_svg_is_kept_as_an_svg(company):
    """The format a logo most often comes in, and the reason #264 exists."""
    content = logos.process(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10">'
        b'<rect width="40" height="10" fill="#123456"/></svg>'
    )

    written = content.read()
    assert pictures.media_type_of_stored(written) == "image/svg+xml"
    assert b"<rect" in written and b"#123456" in written


@pytest.mark.parametrize(
    "hostile, gone",
    [
        (b"<script>alert(1)</script>", b"script"),
        (b'<image href="https://evil.test/p.png"/>', b"evil.test"),
        (b"<style>@import url(https://evil.test/a.css);</style>", b"evil.test"),
        (
            b'<foreignObject><b xmlns="http://www.w3.org/1999/xhtml">x</b></foreignObject>',
            b"foreignObject",
        ),
    ],
)
def test_an_svg_is_stripped_of_what_it_must_not_carry(hostile, gone):
    """An SVG is a document, not a picture, and the stored file is served from our origin.

    `tests/security/test_svg.py` is the fuller set; these are here so the logo pipeline
    itself is known to run the sanitiser rather than merely to have one available.
    """
    written = logos.process(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        + hostile
        + b'<rect width="4" height="4"/></svg>'
    ).read()

    assert gone not in written
    assert b"<rect" in written, "and the drawing survives"


def test_an_svg_that_is_not_an_svg_is_refused():
    with pytest.raises(logos.UnusableLogo, match="could not be read as an image"):
        logos.process(b"<svg>unclosed and broken")


# ---------------------------------------------------------------- fetching it


def test_a_logo_is_fetched_once_and_kept_here(web, company):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    logos.from_url(company, "https://cdn.example/logo.png")

    company.refresh_from_db()
    assert company.has_logo, "the file is ours now"
    assert company.logo_source == "url"
    assert company.logo_source_url == "https://cdn.example/logo.png"
    assert company.logo_fetched_at is not None
    assert web["calls"] == ["https://cdn.example/logo.png"], "one request, not one per view"


@pytest.mark.parametrize(
    "status,body,content_type,expected",
    [
        (404, b"", "text/html", "answered 404"),
        (200, b"", "image/png", "answered with nothing"),
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


def test_a_private_address_is_never_fetched(company):
    """Nothing is stubbed here on purpose (#215).

    The guarded client refuses the address itself — an address literal resolves to itself, so
    this never leaves the machine — and it refuses it whatever the operator decided about
    *connections*, because a logo is public by definition.
    """
    with pytest.raises(logos.UnusableLogo, match="private or local network"):
        logos.from_url(company, "http://192.168.1.20/logo.png")
    company.refresh_from_db()
    assert not company.has_logo


def test_a_logo_is_public_even_where_connections_may_reach_the_network(company, settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True

    with pytest.raises(logos.UnusableLogo, match="private or local network"):
        logos.from_url(company, "http://192.168.1.20/logo.png")
    assert not company.has_logo


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
    assert company.has_logo and company.logo_source == "website"


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


def test_a_site_that_blocks_the_icon_says_why(web, company, monkeypatch):
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(
            url="https://blackmesa.test/",
            html=a_page(icon='<link rel="icon" href="/only.png">').decode(),
        ),
    )
    web["responses"]["https://blackmesa.test/only.png"] = (403, b"", "text/plain")
    with pytest.raises(logos.UnusableLogo, match=r"Nothing on blackmesa\.test.*403"):
        logos.find_on_website(company)


def test_the_favicon_is_tried_however_many_icons_a_page_declares(web, company, monkeypatch):
    icons = "".join(
        f'<link rel="apple-touch-icon" sizes="{n}x{n}" href="/touch{n}.png">'
        for n in (57, 60, 72, 76, 114, 120, 144)
    )
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(
            url="https://blackmesa.test/", html=a_page(icons=icons).decode()
        ),
    )
    with pytest.raises(logos.UnusableLogo):
        logos.find_on_website(company)
    assert "https://blackmesa.test/favicon.ico" in web["calls"]


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
    assert company.has_logo and company.logo_source == "url"

    response = client.post(
        reverse("jobs:company_create"),
        {"name": "Aperture", "logo_url": "https://cdn.example/missing.png"},
        follow=True,
    )
    assert "The logo was not changed" in response.content.decode()
    aperture = Company.objects.for_user(user).get(name="Aperture")
    assert not aperture.has_logo, "the company is still saved without it"


def test_a_refused_identifier_stops_the_logo_fields_too(client, user, company, web):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    bad = {
        "identifiers-TOTAL_FORMS": "1",
        "identifiers-INITIAL_FORMS": "0",
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
        "identifiers-0-scheme": "wikidata",
        "identifiers-0-value": "not-an-id",
        "identifiers-0-label": "",
    }
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_create"),
        {"name": "Aperture", "logo_url": "https://cdn.example/logo.png", **bad},
    )
    assert response.status_code == 200
    assert not Company.objects.for_user(user).filter(name="Aperture").exists()
    assert web["calls"] == [], "nothing fetched for a company that was not saved"

    logos.from_url(company, "https://cdn.example/logo.png")
    web["calls"].clear()
    response = client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {"name": "Renamed", "website": company.website, "remove_logo": "on", **bad},
    )
    assert response.status_code == 200
    company.refresh_from_db()
    assert company.name == "Black Mesa"
    assert company.has_logo and company.logo_source == "url", "a refused edit leaves the logo alone"


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
    assert company.has_logo and company.logo_source == "upload"

    client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {"name": company.name, "website": company.website, "remove_logo": "on"},
    )
    company.refresh_from_db()
    assert not company.has_logo and company.logo_source == ""


def test_the_logo_is_served_from_this_instance_and_only_to_its_owner(
    client, user, other_user, company, web
):
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    logos.from_url(company, "https://cdn.example/logo.png")

    client.force_login(user)
    response = client.get(reverse("jobs:company_logo", args=[company.pk]))
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, max-age=86400"
    assert response.content[1:4] == b"PNG"
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]

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
    # Said in words in the refresh dialog (#674), never drawn: nothing is loaded from it.
    assert html.count("cdn.example") == html.count("<bdi>https://cdn.example/logo.png</bdi>"), (
        "the far address appears only as text in the dialog, never as an attribute"
    )
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
    assert not company.has_logo


def test_logo_actions_are_private_to_the_owner(client, other_user, company):
    client.force_login(other_user)
    for action in ("website", "refresh", "remove"):
        url = reverse("jobs:company_logo_action", args=[company.pk, action])
        assert client.post(url).status_code == 404
        assert client.post(url, headers={"HX-Request": "true"}).status_code == 404


def _a_site_with_an_icon(web, monkeypatch):
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(
            url="https://blackmesa.test/",
            html=a_page(icon='<link rel="icon" href="/icon.png">').decode(),
        ),
    )
    web["responses"]["https://blackmesa.test/icon.png"] = (200, an_image(), "image/png")


def test_an_htmx_press_is_answered_with_the_errand_state_for_the_dialog(
    client, user, company, web, monkeypatch
):
    """The dialog on the company's page shows the answer where the button was (#674)."""
    _a_site_with_an_icon(web, monkeypatch)
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "website"]),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, "no redirect to the errand's own page"
    html = response.content.decode()
    assert 'id="errand-state"' in html and "Found a logo" in html
    assert "data-dialog-done" in html and "Take me to it" not in html
    assert "<html" not in html
    company.refresh_from_db()
    assert company.logo_source == "website"


def test_a_plain_press_still_lands_on_the_errand_page(client, user, company, web, monkeypatch):
    _a_site_with_an_icon(web, monkeypatch)
    client.force_login(user)
    response = client.post(reverse("jobs:company_logo_action", args=[company.pk, "website"]))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:errand", args=[1])[:-2])


def test_a_failure_in_the_dialog_gives_its_reason_and_the_other_ways(
    client, user, company, web, monkeypatch
):
    monkeypatch.setattr(
        logos.fetching,
        "fetch_page",
        lambda url: logos.fetching.FetchedPage(url="https://blackmesa.test/", html=a_page()),
    )
    web["responses"]["https://blackmesa.test/favicon.ico"] = (403, b"", "text/plain")
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_logo_action", args=[company.pk, "website"]),
        headers={"HX-Request": "true"},
    )
    html = response.content.decode()
    assert "answered 403" in html
    assert reverse("jobs:company_update", args=[company.pk]) in html


def test_the_state_fragment_keeps_its_dialog_wording_while_it_polls(
    client, user, company, web, monkeypatch
):
    from postulo.core import errands

    errand = errands.send("logo", user, subject=company, company_id=company.pk, action="website")
    errand.state = "running"
    errand.save()
    client.force_login(user)
    page = client.get(reverse("core:errand_state", args=[errand.pk])).content.decode()
    assert "You can leave this page" in page
    boxed = client.get(
        reverse("core:errand_state", args=[errand.pk]) + "?dialog=1"
    ).content.decode()
    assert "You can close this" in boxed and "?dialog=1" in boxed


def test_the_company_page_opens_the_logo_dialog_with_a_button(client, user, company):
    client.force_login(user)
    html = client.get(company.get_absolute_url()).content.decode()
    assert 'popovertarget="logo-dialog"' in html
    assert 'id="logo-dialog"' in html and 'id="company-head"' in html
    assert "blackmesa.test" in html


def test_two_people_recording_the_same_company_keep_their_own(user, other_user, web):
    """Companies are owner-scoped on purpose; a shared file would say who else applied."""
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    mine = Company.objects.create(owner=user, name="Black Mesa")
    theirs = Company.objects.create(owner=other_user, name="Black Mesa")
    logos.from_url(mine, "https://cdn.example/logo.png")
    logos.from_url(theirs, "https://cdn.example/logo.png")
    assert CompanyLogo.objects.get(company=mine).pk != CompanyLogo.objects.get(company=theirs).pk
    assert CompanyLogo.objects.filter(company__owner=user).count() == 1
    assert CompanyLogo.objects.filter(company__owner=other_user).count() == 1


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
    assert restored.has_logo, "the picture came with it; nothing was fetched"
    assert restored.logo_source == "url"
    assert restored.logo_fetched_at is not None
    assert web["calls"] == ["https://cdn.example/logo.png"], "still just the one fetch"


# --------------------------------------------------------- the two ways in agree


def test_an_uploaded_svg_is_accepted_rather_than_called_unreadable(user):
    """The papercut #264 names, and the reason the field stopped being an `ImageField`.

    An `ImageField` verifies with Pillow, which cannot read an SVG — so the route somebody
    with an SVG actually uses told them their perfectly valid file "could not be read as an
    image", while the by-URL route said the useful thing. Now both accept it.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from postulo.jobs.forms import CompanyForm

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="8" height="8"/></svg>'
    form = CompanyForm(
        {"name": "Black Mesa"},
        {"logo_upload": SimpleUploadedFile("mark.svg", svg, content_type="image/svg+xml")},
        user=user,
    )

    assert form.is_valid(), form.errors
    assert "logo_upload" not in form.errors


def test_an_upload_that_is_not_a_picture_says_so_at_the_form(user):
    """And the form is where somebody finds out, rather than two functions later."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from postulo.jobs.forms import CompanyForm

    form = CompanyForm(
        {"name": "Black Mesa"},
        {"logo_upload": SimpleUploadedFile("mark.png", b"not a picture", "image/png")},
        user=user,
    )

    assert not form.is_valid()
    assert "could not be read as an image" in str(form.errors["logo_upload"])


def test_an_icons_sizes_are_read_in_time_that_follows_their_length():
    """`sizes` is the page's to write. Read without a look-behind, a run of digits with no
    `x` after it was tried again from every digit in it: 40,000 of them took fifteen seconds
    and a page may be two megabytes, so *Find logo* on a hostile site held a worker for
    hours (#321)."""
    import time

    assert logos._largest("16x16 32X32 180x180") == 180
    assert logos._largest("any") == 0
    assert logos._largest("") == 0
    assert logos._largest(None) == 0

    started = time.perf_counter()
    assert logos._largest("1" * 400_000) == 0
    assert logos._largest("1" * 400_000 + "x16") == 0, "no size is 400,000 digits long"
    assert logos._largest("7" * 200_000 + " 48x48") == 48
    assert time.perf_counter() - started < 2.0


# ------------------------------------------------ a logo is a row, written with its flag (#662)


def _stored(company, data=None):
    from django.core.files.base import ContentFile

    logos.store(company, ContentFile(data or an_image()), source="upload")
    company.refresh_from_db()


def test_a_logo_is_one_row_and_a_flag_that_agree(company):
    assert not company.has_logo and not CompanyLogo.objects.filter(company=company).exists()

    _stored(company)
    assert company.has_logo
    row = CompanyLogo.objects.get(company=company)
    assert row.media_type == "image/png" and bytes(row.data)[1:4] == b"PNG"

    logos.clear(company)
    company.refresh_from_db()
    assert not company.has_logo and not CompanyLogo.objects.filter(company=company).exists()


def test_a_replaced_logo_is_still_one_row(company):
    _stored(company)
    first = CompanyLogo.objects.get(company=company)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>'
    _stored(company, logos.process(svg).read())

    assert CompanyLogo.objects.filter(company=company).count() == 1
    row = CompanyLogo.objects.get(company=company)
    assert row.pk == first.pk and row.media_type == "image/svg+xml"


def test_storing_a_logo_whose_row_cannot_be_saved_writes_nothing(company, monkeypatch):
    _stored(company)
    before = bytes(CompanyLogo.objects.get(company=company).data)

    def fail(*args, **kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr(Company, "save", fail)
    with pytest.raises(OperationalError):
        _stored(company, logos.process(an_image(size=(50, 50))).read())
    monkeypatch.undo()

    assert bytes(CompanyLogo.objects.get(company=company).data) == before, "the row rolled back"
    assert company.has_logo and company.logo_source == "upload", "and so did the company"


def test_clearing_a_logo_whose_row_cannot_be_saved_keeps_it(company, monkeypatch):
    _stored(company)

    def fail(*args, **kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr(Company, "save", fail)
    with pytest.raises(OperationalError):
        logos.clear(company)
    monkeypatch.undo()
    assert CompanyLogo.objects.filter(company=company).exists()


def test_what_was_not_checked_is_not_kept(company):
    """`pictures.keep` is the one write, and it refuses what `process` did not make (#466)."""
    from django.core.files.base import ContentFile

    unchecked = (b"GIF89a not a png", b"<html></html>", pictures.PNG_SIGNATURE + b"0" * 2_000_000)
    for data in unchecked:
        with pytest.raises(pictures.UnusablePicture):
            logos.store(company, ContentFile(data), source="upload")
    assert not CompanyLogo.objects.filter(company=company).exists()
    company.refresh_from_db()
    assert not company.has_logo


def test_a_logo_the_old_way_is_let_go_of_when_it_is_replaced_or_cleared(
    company, settings, tmp_path, django_capture_on_commit_callbacks
):
    """Until the old field is dropped, a file left from before must not outlive a removal."""
    from django.core.files.base import ContentFile

    settings.MEDIA_ROOT = str(tmp_path)
    company.logo.save("logo.png", ContentFile(an_image()), save=True)
    old = company.logo.path
    with django_capture_on_commit_callbacks(execute=True):
        _stored(company)
    company.refresh_from_db()
    assert not company.logo and not os.path.exists(old)

    company.logo.save("logo.png", ContentFile(an_image()), save=True)
    old = company.logo.path
    with django_capture_on_commit_callbacks(execute=True):
        logos.clear(company)
    assert not os.path.exists(old)


def test_finding_a_logo_for_a_company_deleted_meanwhile_is_refused(company, monkeypatch):
    from postulo.core.errands import Refused
    from postulo.jobs import slow

    def gone(company, url):
        Company.objects.filter(pk=company.pk).delete()
        raise DatabaseError("Save with update_fields did not affect any rows.")

    monkeypatch.setattr(logos, "find_on_website", lambda c: gone(c, ""))

    errand = SimpleNamespace(
        payload={"company_id": company.pk, "action": "website"}, owner_id=company.owner_id
    )
    with pytest.raises(Refused):
        slow.find_a_logo(errand)


def test_deleting_a_company_takes_its_logo(client, user, other_user, company):
    _stored(company)
    theirs = Company.objects.create(owner=other_user, name="Theirs")
    _stored(theirs)

    client.force_login(user)
    assert client.post(reverse("jobs:company_delete", args=[company.pk])).status_code == 302

    assert not Company.objects.filter(pk=company.pk).exists()
    assert not CompanyLogo.objects.filter(company_id=company.pk).exists(), "gone with it"
    assert CompanyLogo.objects.filter(company=theirs).exists(), "and nobody else's"


def test_a_company_deleted_without_the_application_takes_its_logo_too(company):
    """The path a hand-written list would have missed, the admin's among it: the database's
    own cascade, with no receiver involved."""
    _stored(company)
    Company.objects.filter(pk=company.pk).delete()
    assert not CompanyLogo.objects.exists()


def test_a_list_of_companies_never_loads_a_logo(client, user):
    """The flag is on the company's row, so the bytes are read only to serve one."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for number in range(6):
        _stored(Company.objects.create(owner=user, name=f"Company {number}"))
    client.force_login(user)

    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("jobs:company_list"))
    assert response.status_code == 200
    assert response.content.count(b"/logo/") >= 6, "every company draws its mark"
    assert not [q for q in queries if "jobs_companylogo" in q["sql"]], "and none read the bytes"


# --------------------------------------------- moving the files into rows (#662)


def test_the_migration_moves_a_file_and_reports_a_missing_and_an_undecodable_one(
    company, settings, tmp_path, capsys
):
    import importlib

    from django.apps import apps
    from django.core.files.base import ContentFile

    settings.MEDIA_ROOT = str(tmp_path)
    company.logo.save("logo.png", ContentFile(an_image()), save=True)
    Company.objects.filter(pk=company.pk).update(
        logo_source="url", logo_source_url="https://x.test/"
    )
    gone = Company.objects.create(owner=company.owner, name="Missing file")
    Company.objects.filter(pk=gone.pk).update(
        logo="logos/1/nowhere.png", logo_source="url", logo_source_url="https://x.test/gone"
    )
    broken = Company.objects.create(owner=company.owner, name="Broken file")
    broken.logo.save("logo.png", ContentFile(b"this is not a picture"), save=True)
    vector = Company.objects.create(owner=company.owner, name="Vector")
    vector.logo.save(
        "logo.svg",
        ContentFile(b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script><rect/></svg>'),
        save=True,
    )

    move = importlib.import_module("postulo.jobs.migrations.0030_move_logos_into_the_database")
    move.move(apps, None)

    for row in (company, gone, broken, vector):
        row.refresh_from_db()
    assert company.has_logo and CompanyLogo.objects.get(company=company).media_type == "image/png"
    assert vector.has_logo
    stored = bytes(CompanyLogo.objects.get(company=vector).data)
    assert b"<rect" in stored and b"script" not in stored, "sanitised again on the way"
    for lost in (gone, broken):
        assert not lost.has_logo and not CompanyLogo.objects.filter(company=lost).exists()
        assert lost.logo_source == "" and lost.logo_source_url == ""
    assert company.logo_source == "url", "what was moved keeps where it came from"
    said = capsys.readouterr().out
    assert "2 picture(s) could not be moved" in said
    assert "the file is missing" in said and "no longer decodes" in said
    assert company.logo, "the old file stays where it was, for a rollback"


@pytest.mark.django_db(transaction=True)
def test_the_company_form_downloads_the_logo_outside_any_transaction(
    client, user, web, atomic_requests, monkeypatch
):
    """The logo address is on somebody else's server, and the form must not hold the lock (#357)."""
    from django.db import connection

    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    seen: list[bool] = []
    original = logos.download

    def download(url):
        seen.append(connection.in_atomic_block)
        return original(url)

    monkeypatch.setattr(logos, "download", download)
    client.force_login(user)
    client.post(
        reverse("jobs:company_create"),
        {"name": "Black Mesa", "logo_url": "https://cdn.example/logo.png"},
    )
    black_mesa = Company.objects.for_user(user).get(name="Black Mesa")
    assert black_mesa.has_logo, "the logo still arrives"

    client.post(
        reverse("jobs:company_update", args=[black_mesa.pk]),
        {"name": "Black Mesa", "logo_url": "https://cdn.example/other.png"},
    )

    assert seen == [False, False]


def test_saving_a_new_logo_address_spends_the_fetch_allowance(client, user, web, settings):
    """The address is somebody's choice and the server goes and gets it (#407)."""
    settings.POSTULO_FETCH_RATE = "1/h"
    web["responses"]["https://cdn.example/logo.png"] = (200, an_image(), "image/png")
    client.force_login(user)

    client.post(
        reverse("jobs:company_create"),
        {"name": "Black Mesa", "logo_url": "https://cdn.example/logo.png"},
    )
    response = client.post(
        reverse("jobs:company_create"),
        {"name": "Aperture", "logo_url": "https://cdn.example/other.png"},
        follow=True,
    )

    assert web["calls"] == ["https://cdn.example/logo.png"], "the second was never fetched"
    assert "Too many requests" in response.content.decode()
    assert Company.objects.for_user(user).filter(name="Aperture").exists()


def test_find_logo_spends_the_fetch_allowance_and_is_refused_once_it_is_spent(
    client, user, company, web, settings, monkeypatch
):
    settings.POSTULO_FETCH_RATE = "1/h"
    client.force_login(user)
    url = reverse("jobs:company_logo_action", args=[company.pk, "website"])

    first = client.post(url)
    assert first.status_code == 302 and first["Location"].startswith("/working/")
    response = client.post(url, follow=True)

    assert "Too many requests" in response.content.decode()
    assert Errand.objects.filter(owner=user).count() == 1, "only the first press sent one"
