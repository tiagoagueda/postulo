"""What a capture keeps of the page it was read from (#256).

A capture used to read a page, keep what it understood, and throw the page away. It can
keep the page now: the source exactly as it was parsed, and a rendering of the whole thing.
These are the tests of what is kept, when, for whom, and what becomes of it; the boundary
is held next door, in `tests/security/test_captured_pages.py`.

Nothing here reaches the network, and nothing here draws a page with a real renderer:
`tests/test_page_rendering.py` does that, where there is one to draw with.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.accounts import deletion
from postulo.api.models import ApiToken
from postulo.core import export as export_module
from postulo.core import importer, site
from postulo.core.models import Errand, SiteSettings
from postulo.jobs import pages, rendering
from postulo.jobs.models import Capture, CapturedPage, CaptureStatus, RenderedBy, RenderingKind

pytestmark = pytest.mark.django_db

POSTING = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Research Engineer",
    "hiringOrganization": {"name": "Black Mesa"},
    "description": "<p>Science.</p>",
}
PAGE = (
    "<html><head><title>Research Engineer</title>"
    f'<script type="application/ld+json">{json.dumps(POSTING)}</script>'
    "</head><body><h1>Research Engineer</h1><p>Hello, Gordon.</p></body></html>"
)

#: The smallest thing that is a PNG as far as its first bytes go, which is as far as
#: Postulo looks: it keeps a picture, it does not open one.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"%" * 64


# ------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _a_media_directory_of_its_own(settings, tmp_path):
    """Every test here starts with nothing on the disk.

    The suite's media directory is one per process and a rolled-back test leaves its files
    in it, so a test asking *was anything written* would be answered by whichever test ran
    before it. These are tests about exactly that question.
    """
    settings.MEDIA_ROOT = str(tmp_path / "media")


def allow(*, source: bool | None = True, rendering: bool | None = True) -> None:
    """What an administrator has decided for the instance."""
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"capture_keep_source": source, "capture_keep_rendering": rendering}
    )
    site.forget_current()


def want(user, *, source: bool = True, rendering: bool = True) -> None:
    """What the person has switched on for themselves."""
    profile = user.profile
    profile.keep_page_source = source
    profile.keep_page_rendering = rendering
    profile.save(update_fields=["keep_page_source", "keep_page_rendering"])


@pytest.fixture
def keeping(user):
    """Both people have said yes to both things."""
    allow()
    want(user)
    return user


@pytest.fixture
def capture(user):
    return Capture.objects.create(
        owner=user,
        url="https://example.org/j/7",
        source_name="schema.org",
        data={"title": "Research Engineer", "company_name": "Black Mesa"},
    )


@pytest.fixture
def bearer(user):
    _record, raw = ApiToken.issue(user, "Extension")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_capture(client, bearer, **payload):
    return client.post(
        "/api/v1/captures",
        data=json.dumps(payload),
        content_type="application/json",
        **bearer,
    )


def put_rendering(client, bearer, pk, body=PNG, content_type="image/png", **extra):
    return client.put(
        f"/api/v1/captures/{pk}/rendering",
        data=body,
        content_type=content_type,
        **bearer,
        **extra,
    )


def path_of(field) -> Path:
    return Path(settings.MEDIA_ROOT) / field.name


# ----------------------------------------------------------------- who has to say yes


def test_nothing_is_kept_until_somebody_asks(user):
    """Off at both levels, which is the state of every instance that has not decided."""
    answer = pages.keeping_for(user)

    assert not answer.allowed_source and not answer.allowed_rendering
    assert not answer.wanted_source and not answer.wanted_rendering
    assert not answer.anything


@pytest.mark.parametrize(
    ("instance", "person", "kept"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (None, True, False),
        (False, False, False),
    ],
    ids=["both", "only the instance", "only the person", "never decided", "neither"],
)
def test_the_source_is_kept_only_when_both_have_said_so(user, instance, person, kept):
    allow(source=instance, rendering=False)
    want(user, source=person, rendering=False)

    assert pages.keeping_for(user).source is kept


def test_the_two_things_are_switched_separately(user):
    """They leak differently, so one can be kept without the other, either way round."""
    allow(source=True, rendering=False)
    want(user, source=True, rendering=True)
    answer = pages.keeping_for(user)
    assert answer.source and not answer.rendering

    allow(source=False, rendering=True)
    answer = pages.keeping_for(user)
    assert answer.rendering and not answer.source


def test_a_person_cannot_widen_what_the_instance_allows(user):
    """An operator's no is final; a preference narrows it and never widens it."""
    allow(source=False, rendering=False)
    want(user, source=True, rendering=True)

    assert not pages.keeping_for(user).anything


def test_a_request_can_narrow_it_again_and_never_widen_it(user):
    allow()
    want(user, source=True, rendering=False)
    answer = pages.keeping_for(user)

    assert not answer.narrowed(source=False).source
    assert not answer.narrowed(rendering=True).rendering, "asking does not switch it on"
    assert answer.narrowed(source=True).source


def test_the_environment_pins_the_instances_answer(user, settings, monkeypatch):
    allow(source=True, rendering=True)
    want(user)
    monkeypatch.setenv("POSTULO_CAPTURE_KEEP_SOURCE", "false")
    settings.POSTULO_CAPTURE_KEEP_SOURCE = False

    answer = pages.keeping_for(user)

    assert not answer.source, "the environment wins over what an administrator stored"
    assert answer.rendering


def test_one_persons_choice_is_not_anothers(user, other_user):
    allow()
    want(user)

    assert pages.keeping_for(user).anything
    assert not pages.keeping_for(other_user).anything


# ------------------------------------------------------------------ keeping the source


def test_the_source_is_kept_exactly_as_it_was_parsed(keeping, capture):
    page = pages.keep_source(capture, PAGE)

    assert pages.read_source(page) == PAGE
    assert page.source_size == len(PAGE.encode())
    assert page.source_checksum == hashlib.sha256(PAGE.encode()).hexdigest()
    assert page.owner == keeping and page.capture == capture


def test_it_is_kept_gzipped_under_the_owner_and_never_named_html(keeping, capture):
    page = pages.keep_source(capture, PAGE)

    assert page.source.name.startswith(f"captures/{keeping.pk}/")
    assert page.source.name.endswith(".txt.gz")
    assert "html" not in page.source.name
    stored = path_of(page.source).read_bytes()
    assert gzip.decompress(stored) == PAGE.encode()
    assert page.source_stored == len(stored)


def test_the_name_is_postulos_own(keeping, capture):
    """Nothing a stranger or a client chose goes into a path on the disk."""
    capture.url = "https://example.org/../../etc/passwd"
    capture.data = {"title": "../../evil"}
    capture.save()

    page = pages.keep_source(capture, PAGE)

    assert ".." not in page.source.name and "evil" not in page.source.name
    root = Path(settings.MEDIA_ROOT).resolve()
    assert root in path_of(page.source).resolve().parents


def test_a_source_nobody_asked_for_is_not_kept(user, capture):
    with pytest.raises(pages.NotKept) as refused:
        pages.keep_source(capture, PAGE)

    assert refused.value.reason == "switched-off"
    assert not CapturedPage.objects.exists()


def test_a_source_over_the_cap_is_not_kept(keeping, capture, settings):
    settings.POSTULO_CAPTURE_SOURCE_MAX_BYTES = 100

    with pytest.raises(pages.NotKept) as refused:
        pages.keep_source(capture, "x" * 101)

    assert refused.value.reason == "too-large" and refused.value.limit == 100
    assert not CapturedPage.objects.exists()


def test_an_account_with_no_room_left_keeps_nothing_more(keeping, capture, settings):
    pages.keep_source(capture, PAGE)
    second = Capture.objects.create(owner=keeping, url="https://example.org/j/8", data={})
    settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES = pages.weight_of(keeping) + 10

    with pytest.raises(pages.NotKept) as refused:
        pages.keep_source(second, PAGE)

    assert refused.value.reason == "no-room"
    assert CapturedPage.objects.count() == 1


def test_the_room_is_each_accounts_own(keeping, other_user, capture, settings):
    pages.keep_source(capture, PAGE)
    settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES = pages.weight_of(keeping)
    want(other_user)
    theirs = Capture.objects.create(owner=other_user, url="https://example.org/j/9", data={})

    assert pages.room_left(keeping) == 0
    assert pages.keep_source(theirs, PAGE).source


def test_keeping_a_page_never_costs_the_capture(keeping, capture, settings):
    """Rule four: whatever goes wrong is a sentence, beside a capture that was made."""
    settings.POSTULO_CAPTURE_SOURCE_MAX_BYTES = 10

    page, note = pages.keep_source_quietly(capture, PAGE)

    assert page is None
    assert "was not kept" in note
    assert Capture.objects.filter(pk=capture.pk).exists()


def test_nothing_is_said_about_a_source_nobody_asked_for(user, capture):
    assert pages.keep_source_quietly(capture, PAGE) == (None, "")


def test_a_lone_surrogate_does_not_make_the_file_unreadable(keeping, capture):
    """JSON can carry one, and UTF-8 cannot: it is replaced rather than refused."""
    page = pages.keep_source(capture, "<p>\ud800</p>")

    assert pages.read_source(page) == "<p>?</p>"


# ----------------------------------------------------------- capturing, both ways in


def test_a_capture_from_the_form_keeps_the_page_it_was_given(client, keeping):
    client.force_login(keeping)

    response = client.post(
        reverse("jobs:capture_create"), {"url": "https://example.org/j/7", "html": PAGE}
    )

    assert response.status_code == 302
    capture = Capture.objects.for_user(keeping).get()
    assert pages.read_source(capture.page) == PAGE
    assert not capture.page.rendering, "a rendering is drawn when asked for, not at capture"


def test_a_capture_from_the_form_keeps_the_page_that_was_fetched(client, keeping, monkeypatch):
    from postulo.plugins.fetching import FetchedPage

    monkeypatch.setattr(
        "postulo.plugins.fetching.fetch_page",
        lambda url: FetchedPage(url="https://example.org/j/7?landed", html=PAGE),
    )
    client.force_login(keeping)

    client.post(reverse("jobs:capture_create"), {"url": "https://example.org/j/7"})

    capture = Capture.objects.for_user(keeping).get()
    assert pages.read_source(capture.page) == PAGE


def test_a_capture_keeps_nothing_by_default(client, user):
    client.force_login(user)

    client.post(reverse("jobs:capture_create"), {"url": "https://example.org/j/7", "html": PAGE})

    assert Capture.objects.for_user(user).count() == 1
    assert not CapturedPage.objects.exists()
    assert not (Path(settings.MEDIA_ROOT) / "captures" / str(user.pk)).exists()


def test_a_pasted_page_does_not_stay_in_the_work_queue(client, user):
    """The errand's row is kept for a week. It carried the pasted source for that week,
    whatever anybody had decided about keeping sources."""
    client.force_login(user)

    client.post(reverse("jobs:capture_create"), {"url": "https://example.org/j/7", "html": PAGE})

    errand = Errand.objects.for_user(user).get()
    assert errand.payload["url"] == "https://example.org/j/7"
    assert errand.payload["html"] == ""
    assert "Gordon" not in json.dumps(errand.payload)


def test_a_page_that_could_not_be_kept_is_said_where_the_capture_is_watched(
    client, keeping, settings
):
    settings.POSTULO_CAPTURE_SOURCE_MAX_BYTES = 10
    client.force_login(keeping)

    response = client.post(
        reverse("jobs:capture_create"), {"url": "https://example.org/j/7", "html": PAGE}
    )

    page = client.get(response.url).content.decode()
    assert "Read it." in page and "was not kept" in page
    assert Capture.objects.for_user(keeping).count() == 1


def test_the_api_keeps_the_page_an_extension_sent(client, keeping, bearer):
    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)

    assert response.status_code == 201
    body = response.json()["page"]
    assert body["source"] is True and body["rendering"] is False
    assert body["accepts_rendering"] is True
    assert body["rendering_url"].endswith(f"/api/v1/captures/{response.json()['id']}/rendering")
    assert set(body["rendering_types"]) == set(RenderingKind.values)
    assert body["rendering_max_bytes"] == site.capture_rendering_max_bytes()
    assert body["note"] == ""
    capture = Capture.objects.for_user(keeping).get()
    assert pages.read_source(capture.page) == PAGE


def test_the_api_says_so_when_nothing_is_kept(client, user, bearer):
    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)

    body = response.json()["page"]
    assert body["source"] is False and body["accepts_rendering"] is False
    assert not CapturedPage.objects.exists()


def test_a_request_may_ask_for_less_for_one_capture(client, keeping, bearer):
    response = post_capture(
        client, bearer, url="https://example.org/j/7", html=PAGE, keep={"source": False}
    )

    assert response.status_code == 201
    body = response.json()["page"]
    assert body["source"] is False and body["note"] == ""
    assert not CapturedPage.objects.exists()
    # A rendering is not this request's to refuse: not keeping one is not sending one.
    assert body["accepts_rendering"] is True


def test_the_request_says_nothing_about_a_rendering(client, keeping, bearer):
    """A field for it would promise something the server has nothing to keep it with."""
    response = post_capture(
        client,
        bearer,
        url="https://example.org/j/7",
        html=PAGE,
        keep={"source": True, "rendering": False},
    )

    assert response.status_code == 422
    assert not Capture.objects.exists()


def test_a_request_cannot_ask_for_more(client, user, bearer):
    allow(source=False, rendering=False)
    want(user)

    response = post_capture(
        client, bearer, url="https://example.org/j/7", html=PAGE, keep={"source": True}
    )

    assert response.status_code == 201
    assert not CapturedPage.objects.exists()
    assert response.json()["page"]["source"] is False
    assert response.json()["page"]["accepts_rendering"] is False


def test_a_misspelt_keep_is_refused_rather_than_ignored(client, keeping, bearer):
    """A client that wrote `sorce: false` meant something, and keeping it anyway is the
    one reading of that mistake it would mind."""
    response = post_capture(
        client, bearer, url="https://example.org/j/7", html=PAGE, keep={"sorce": False}
    )

    assert response.status_code == 422
    assert not Capture.objects.exists()


def test_a_preview_keeps_nothing(client, keeping, bearer):
    response = client.post(
        "/api/v1/captures/preview",
        data=json.dumps({"url": "https://example.org/j/7", "html": PAGE}),
        content_type="application/json",
        **bearer,
    )

    assert response.status_code == 200
    assert not CapturedPage.objects.exists()


def test_the_queue_says_what_each_capture_kept(client, keeping, bearer):
    post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)

    (row,) = client.get("/api/v1/captures", **bearer).json()["items"]

    assert row["page"]["source"] is True
    assert row["page"]["accepts_rendering"] is True


def test_a_replayed_capture_says_what_the_first_one_said(client, keeping, bearer):
    headers = {**bearer, "HTTP_IDEMPOTENCY_KEY": "one-posting"}
    first = post_capture(client, headers, url="https://example.org/j/7", html=PAGE)
    second = post_capture(client, headers, url="https://example.org/j/7", html=PAGE)

    assert first.json() == second.json()
    assert CapturedPage.objects.count() == 1


# ---------------------------------------------------------- a rendering, sent from outside


def test_an_extension_sends_a_rendering_after_the_capture(client, keeping, bearer):
    made = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE).json()

    response = put_rendering(client, bearer, made["id"])

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["id"] == made["id"]
    assert body["page"]["rendering"] is True
    assert body["page"]["rendering_type"] == "image/png"
    assert body["page"]["accepts_rendering"] is False, "it has one now"
    page = CapturedPage.objects.get()
    assert path_of(page.rendering).read_bytes() == PNG
    assert page.rendering_size == len(PNG)
    assert page.rendering_checksum == hashlib.sha256(PNG).hexdigest()
    assert page.rendered_by == RenderedBy.CLIENT
    assert page.source, "and the source it was kept beside is still there"


@pytest.mark.parametrize(
    ("kind", "body", "extension"),
    [
        ("image/png", PNG, ".png"),
        ("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 32, ".jpg"),
        ("image/webp", b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32, ".webp"),
        ("application/pdf", PDF, ".pdf"),
    ],
)
def test_each_of_the_four_kinds_is_taken(client, keeping, bearer, capture, kind, body, extension):
    response = put_rendering(client, bearer, capture.pk, body, kind)

    assert response.status_code == 201, response.content
    page = CapturedPage.objects.get()
    assert page.rendering_type == kind
    assert page.rendering.name.endswith(extension)
    assert page.rendering.name.startswith(f"captures/{keeping.pk}/")


def test_a_parameter_on_the_media_type_is_not_a_different_kind(client, keeping, bearer, capture):
    response = put_rendering(client, bearer, capture.pk, PNG, "image/png; charset=binary")

    assert response.status_code == 201


def test_a_rendering_alone_is_kept_where_the_source_is_not(client, user, bearer, capture):
    allow(source=False, rendering=True)
    want(user)

    response = put_rendering(client, bearer, capture.pk)

    assert response.status_code == 201
    page = CapturedPage.objects.get()
    assert page.rendering and not page.source


def test_the_same_rendering_sent_again_is_the_same_answer(client, keeping, bearer, capture):
    """A client that never saw the first reply sends it again, and is right to."""
    first = put_rendering(client, bearer, capture.pk)
    second = put_rendering(client, bearer, capture.pk)

    assert first.status_code == 201 and second.status_code == 200
    assert first.json() == second.json()
    assert CapturedPage.objects.count() == 1
    files = list((Path(settings.MEDIA_ROOT) / "captures" / str(keeping.pk)).rglob("*.png"))
    assert len(files) == 1, "and nothing was written twice"


#: A second picture, different from the first by its bytes.
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


@pytest.fixture
def a_rival(monkeypatch):
    """Another request keeps a different rendering a moment before this one claims its own.

    Both found the column empty; only one of them can be what the capture kept.
    """
    from django.core.files.base import ContentFile

    real = pages._claim_rendering

    def racing(capture, content, **held):
        real(
            Capture.objects.get(pk=capture.pk),
            ContentFile(OTHER_PNG),
            kind="image/png",
            size=len(OTHER_PNG),
            checksum=hashlib.sha256(OTHER_PNG).hexdigest(),
            rendered_by=RenderedBy.CLIENT,
        )
        return real(capture, content, **held)

    monkeypatch.setattr(pages, "_claim_rendering", racing)


@pytest.mark.parametrize("with_a_source", [False, True], ids=["no row yet", "a row already"])
def test_two_renderings_at_once_keep_one_and_leave_no_file_behind(
    keeping, capture, a_rival, with_a_source
):
    if with_a_source:
        pages.keep_source(capture, PAGE)

    with pytest.raises(pages.NotKept) as refused:
        pages.attach_rendering(capture, io.BytesIO(PNG), content_type="image/png", length=len(PNG))

    assert refused.value.reason == "already-kept"
    page = CapturedPage.objects.get()
    assert path_of(page.rendering).read_bytes() == OTHER_PNG, "the first one kept stays"
    pictures = list((Path(settings.MEDIA_ROOT) / "captures").rglob("*.png"))
    assert pictures == [path_of(page.rendering)], "and the loser's file went at once"
    assert bool(page.source) is with_a_source


def test_a_rendering_drawn_twice_at_once_keeps_one(keeping, capture, a_rival, monkeypatch):
    monkeypatch.setattr(rendering, "renderer", lambda: rendering.Renderer("stand-in", ()))
    monkeypatch.setattr(rendering, "draw", lambda html, *, using=None: PDF)
    pages.keep_source(capture, PAGE)

    with pytest.raises(pages.NotKept) as refused:
        pages.draw_rendering(capture)

    assert refused.value.reason == "already-kept"
    assert not list((Path(settings.MEDIA_ROOT) / "captures").rglob("*.pdf"))


def test_a_page_that_cannot_be_written_costs_nothing_but_itself(
    client, keeping, bearer, monkeypatch
):
    """Rule four, when the database says no: the capture is made and answered, the answer
    says the source was not kept, and the file written for it is not left on the disk."""
    from django.db import IntegrityError

    def refuse(self, *args, **kwargs):
        raise IntegrityError("the row would not go in")

    monkeypatch.setattr(CapturedPage, "save", refuse)

    response = post_capture(client, bearer, url="https://example.org/j/7", html=PAGE)

    assert response.status_code == 201
    page = response.json()["page"]
    assert page["source"] is False and "could not be kept" in page["note"]
    assert Capture.objects.for_user(keeping).count() == 1
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.gz"))


def test_a_rendering_the_account_does_not_keep_is_refused(client, user, bearer, capture):
    allow(source=True, rendering=True)
    want(user, source=True, rendering=False)

    response = put_rendering(client, bearer, capture.pk)

    assert response.status_code == 409
    body = response.json()
    assert body["type"].endswith("#not-kept") and body["reason"] == "switched-off"
    assert not CapturedPage.objects.exists()


def test_an_account_with_no_room_is_told_so(client, keeping, bearer, capture, settings):
    settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES = 10

    response = put_rendering(client, bearer, capture.pk)

    assert response.status_code == 409
    assert response.json()["reason"] == "no-room"
    assert not CapturedPage.objects.exists()


def test_an_administrators_cap_is_the_cap(client, keeping, bearer, capture):
    SiteSettings.objects.filter(pk=1).update(capture_rendering_max_mb=1)
    site.forget_current()

    response = put_rendering(client, bearer, capture.pk, b"\x89PNG\r\n\x1a\n" + b"0" * 1_100_000)

    assert site.capture_rendering_max_bytes() == 1024 * 1024
    assert response.status_code == 413
    assert response.json()["max_bytes"] == 1024 * 1024


def test_the_environment_pins_the_cap(settings, monkeypatch):
    SiteSettings.objects.update_or_create(pk=1, defaults={"capture_rendering_max_mb": 50})
    site.forget_current()
    assert site.capture_rendering_max_bytes() == 50 * 1024 * 1024

    monkeypatch.setenv("POSTULO_CAPTURE_RENDERING_MAX_BYTES", "4096")
    settings.POSTULO_CAPTURE_RENDERING_MAX_BYTES = 4096

    assert site.capture_rendering_max_bytes() == 4096


def test_the_call_is_in_the_description_a_client_reads():
    from postulo.api.api import api

    schema = api.get_openapi_schema()
    call = schema["paths"]["/api/v1/captures/{pk}/rendering"]["put"]

    assert set(call["requestBody"]["content"]) == set(RenderingKind.values)
    assert {401, 403, 404, 409, 411, 413, 415, 422, 429} <= {
        int(status) for status in call["responses"] if str(status).isdigit()
    }
    assert "page" in schema["components"]["schemas"]["CaptureOut"]["properties"]
    assert "keep" in schema["components"]["schemas"]["CaptureIn"]["properties"]


# --------------------------------------------------------------- the pages that show it


@pytest.fixture
def kept(keeping, capture):
    """A capture that kept both: its source, and a picture a browser sent."""
    pages.keep_source(capture, PAGE)
    pages.attach_rendering(capture, io.BytesIO(PNG), content_type="image/png", length=len(PNG))
    return capture


def test_the_page_shows_what_was_kept(client, kept):
    client.force_login(kept.owner)

    response = client.get(reverse("jobs:capture_page", args=[kept.pk]))

    assert response.status_code == 200
    html = response.content.decode()
    assert "The page as captured" in html
    assert reverse("jobs:capture_page_rendering", args=[kept.pk]) in html
    assert reverse("jobs:capture_page_source", args=[kept.pk]) in html
    assert kept.page.source_checksum in html
    assert 'data-rendered-by="client"' in html


def test_the_source_is_on_the_page_as_text(client, kept):
    client.force_login(kept.owner)

    html = client.get(reverse("jobs:capture_page", args=[kept.pk])).content.decode()

    assert "&lt;h1&gt;Research Engineer&lt;/h1&gt;" in html
    assert "<h1>Research Engineer</h1>" not in html


def test_a_long_source_is_shown_from_the_beginning_and_says_so(client, keeping, capture):
    long = "<p>" + "a" * (pages.EXCERPT_CHARACTERS + 500) + "</p>THE-END"
    pages.keep_source(capture, long)
    client.force_login(keeping)

    html = client.get(reverse("jobs:capture_page", args=[capture.pk])).content.decode()

    assert "THE-END" not in html
    assert "data-source-cut" in html
    download = client.get(reverse("jobs:capture_page_source", args=[capture.pk]))
    assert download.content.decode().endswith("THE-END"), "the download is the whole of it"


def test_a_capture_that_kept_nothing_says_why(client, user, capture):
    client.force_login(user)

    html = client.get(reverse("jobs:capture_page", args=[capture.pk])).content.decode()

    assert "Nothing was kept of this page" in html
    assert "This instance does not keep the pages" in html
    assert reverse("settings:capture") in html


def test_it_says_which_of_the_two_has_not_said_yes(client, user, capture):
    allow()
    client.force_login(user)

    html = client.get(reverse("jobs:capture_page", args=[capture.pk])).content.decode()

    assert "Your captures do not keep the page" in html


def test_the_review_screen_points_at_what_was_kept(client, kept):
    client.force_login(kept.owner)

    html = client.get(reverse("jobs:capture_review", args=[kept.pk])).content.decode()

    assert "data-kept-page" in html
    assert reverse("jobs:capture_page", args=[kept.pk]) in html


def test_the_review_screen_says_nothing_where_nothing_was_kept(client, user, capture):
    client.force_login(user)

    html = client.get(reverse("jobs:capture_review", args=[capture.pk])).content.decode()

    assert "data-kept-page" not in html


def test_the_listing_a_capture_became_points_at_it_too(client, kept):
    """Where somebody looks once the advert has gone: the listing outlives the posting."""
    client.force_login(kept.owner)
    client.post(
        reverse("jobs:capture_review", args=[kept.pk]),
        {
            "company_name": "Black Mesa",
            "title": "Research Engineer",
            "salary_currency": "EUR",
            "salary_period": "year",
        },
    )
    kept.refresh_from_db()

    html = client.get(kept.posting.get_absolute_url()).content.decode()

    assert reverse("jobs:capture_page", args=[kept.pk]) in html


def test_a_file_that_has_gone_from_the_disk_does_not_break_the_page(client, kept):
    path_of(kept.page.source).unlink()
    client.force_login(kept.owner)

    response = client.get(reverse("jobs:capture_page", args=[kept.pk]))

    assert response.status_code == 200
    assert "data-source-unreadable" in response.content.decode()
    assert client.get(reverse("jobs:capture_page_source", args=[kept.pk])).status_code == 404


def test_a_picture_is_drawn_by_the_page_and_a_pdf_is_handed_over(client, keeping, capture):
    pages.attach_rendering(
        capture, io.BytesIO(PDF), content_type="application/pdf", length=len(PDF)
    )
    client.force_login(keeping)

    html = client.get(reverse("jobs:capture_page", args=[capture.pk])).content.decode()
    download = client.get(reverse("jobs:capture_page_rendering", args=[capture.pk]))

    assert "<img" not in html[html.index("data-captured-page") :]
    assert "Download the PDF" in html
    assert download["Content-Type"] == "application/pdf"
    assert download["Content-Disposition"].startswith("attachment;")
    download.close()


# ----------------------------------------------------------------------- forgetting


def test_what_was_kept_can_be_thrown_away_and_the_capture_stays(
    client, kept, django_capture_on_commit_callbacks
):
    source, picture = path_of(kept.page.source), path_of(kept.page.rendering)
    client.force_login(kept.owner)
    address = reverse("jobs:capture_page_forget", args=[kept.pk])

    assert "Are you sure?" in client.get(address).content.decode()
    assert source.is_file(), "asking deletes nothing"
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(address)

    assert response.status_code == 302
    assert not CapturedPage.objects.exists()
    assert not source.exists() and not picture.exists()
    assert Capture.objects.filter(pk=kept.pk).exists()


@pytest.mark.parametrize(
    ("what", "gone", "stays"),
    [("rendering", "rendering", "source"), ("source", "source", "rendering")],
)
def test_one_half_can_go_without_the_other(
    client, kept, django_capture_on_commit_callbacks, what, gone, stays
):
    going = path_of(getattr(kept.page, gone))
    staying = path_of(getattr(kept.page, stays))
    client.force_login(kept.owner)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("jobs:capture_page_forget", args=[kept.pk]), {"what": what})

    page = CapturedPage.objects.get()
    assert not getattr(page, gone) and getattr(page, stays)
    assert not going.exists() and staying.is_file()
    if gone == "rendering":
        assert page.rendering_type == "" and page.rendering_size == 0 and page.rendered_by == ""
    else:
        assert page.source_size == 0 and page.source_checksum == ""


def test_a_rolled_back_deletion_keeps_the_files(kept):
    """After the commit, so a deletion that did not happen does not take the file."""
    from django.db import transaction

    source = path_of(kept.page.source)

    with pytest.raises(RuntimeError), transaction.atomic():
        kept.page.delete()
        raise RuntimeError("changed my mind")

    assert source.is_file()


def test_deleting_a_capture_takes_its_files(kept, django_capture_on_commit_callbacks):
    """A cascade runs the collector and not the model's own method, which is why the
    files are removed by a signal: it is the one thing every deletion sends."""
    source, picture = path_of(kept.page.source), path_of(kept.page.rendering)

    with django_capture_on_commit_callbacks(execute=True):
        kept.delete()

    assert not source.exists() and not picture.exists()


def test_discarding_a_capture_keeps_its_page_until_it_expires(client, kept):
    """Discarding is a status and not a deletion, so that a wrong key can be undone."""
    client.force_login(kept.owner)

    client.post(reverse("jobs:capture_discard", args=[kept.pk]))

    kept.refresh_from_db()
    assert kept.status == CaptureStatus.DISCARDED
    assert path_of(kept.page.source).is_file()


def test_deleting_the_account_removes_every_kept_file(
    kept, other_user, django_capture_on_commit_callbacks
):
    want(other_user)
    theirs = Capture.objects.create(owner=other_user, url="https://example.org/j/9", data={})
    their_page = pages.keep_source(theirs, PAGE)
    mine = [path_of(kept.page.source), path_of(kept.page.rendering)]
    owner = kept.owner

    with django_capture_on_commit_callbacks(execute=True):
        report = deletion.delete_account(owner)

    assert not any(path.exists() for path in mine)
    assert report.files_removed >= 2
    assert not (Path(settings.MEDIA_ROOT) / "captures" / str(owner.pk)).exists()
    assert path_of(their_page.source).is_file(), "and nobody else's"


def test_prune_media_knows_a_kept_page_is_not_an_orphan(kept, capsys):
    """Left out of the list, every kept page would be an orphan and `--remove` would
    delete the lot."""
    call_command("prune_media", "--remove")

    assert path_of(kept.page.source).is_file()
    assert path_of(kept.page.rendering).is_file()
    assert "orphan  captures/" not in capsys.readouterr().out


# ------------------------------------------------------------------------- expiring


def aged(capture: Capture, days: int) -> Capture:
    Capture.objects.filter(pk=capture.pk).update(
        created_at=timezone.now() - dt.timedelta(days=days)
    )
    return capture


def test_the_page_of_a_capture_never_confirmed_expires(
    kept, settings, django_capture_on_commit_callbacks
):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = 30
    source = path_of(kept.page.source)
    aged(kept, 31)

    with django_capture_on_commit_callbacks(execute=True):
        gone = pages.expire_unconfirmed()

    assert gone == 1
    assert not CapturedPage.objects.exists() and not source.exists()
    assert Capture.objects.filter(pk=kept.pk).exists(), "the capture is never touched"


def test_a_discarded_capture_loses_its_page_the_same_way(kept, settings):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = 30
    Capture.objects.filter(pk=kept.pk).update(status=CaptureStatus.DISCARDED)
    aged(kept, 31)

    assert pages.expire_unconfirmed() == 1


def test_a_capture_somebody_saved_keeps_its_page(kept, settings):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = 30
    Capture.objects.filter(pk=kept.pk).update(status=CaptureStatus.ACCEPTED)
    aged(kept, 400)

    assert pages.expire_unconfirmed() == 0
    assert CapturedPage.objects.count() == 1


def test_a_page_inside_its_days_is_left_alone(kept, settings):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = 30
    aged(kept, 29)

    assert pages.expire_unconfirmed() == 0


def test_nought_days_keeps_them_all(kept, settings):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = 0
    aged(kept, 4000)

    assert pages.expire_unconfirmed() == 0


def test_the_scheduler_does_the_expiring(kept, settings, capsys):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = 30
    aged(kept, 31)

    call_command("send_due_reminders")

    assert not CapturedPage.objects.exists()
    assert "1 kept pages of unconfirmed captures removed" in capsys.readouterr().out


# ------------------------------------------------------------ drawing one from the source


@pytest.fixture
def stand_in(monkeypatch):
    """A renderer that answers at once, in place of one this machine may not have."""
    drawn: list[str] = []

    def draw(html, *, using=None):
        drawn.append(html)
        return PDF

    monkeypatch.setattr(rendering, "renderer", lambda: rendering.Renderer("stand-in", ()))
    monkeypatch.setattr(rendering, "draw", draw)
    return drawn


def test_a_rendering_is_drawn_from_the_kept_source_when_asked_for(
    client, keeping, capture, stand_in
):
    pages.keep_source(capture, PAGE)
    client.force_login(keeping)

    response = client.post(reverse("jobs:capture_page_draw", args=[capture.pk]))

    assert response.status_code == 302
    watched = client.get(response.url).content.decode()
    assert "Drawn from the kept source" in watched
    assert stand_in == [PAGE], "the source, exactly, and nothing else"
    page = CapturedPage.objects.get()
    assert page.rendering_type == RenderingKind.PDF
    assert page.rendered_by == RenderedBy.INSTANCE
    assert page.rendering.name.endswith(".pdf")
    assert path_of(page.rendering).read_bytes() == PDF


def test_the_button_is_offered_where_there_is_something_to_draw_from(
    client, keeping, capture, stand_in
):
    pages.keep_source(capture, PAGE)
    client.force_login(keeping)

    html = client.get(reverse("jobs:capture_page", args=[capture.pk])).content.decode()

    assert reverse("jobs:capture_page_draw", args=[capture.pk]) in html
    assert "Draw it from the source" in html


def test_where_nothing_can_draw_safely_the_source_is_kept_and_the_page_says_so(
    client, keeping, capture, monkeypatch
):
    """The instance degrades to the source only, and says so, rather than drawing with
    something that could run what it was handed."""
    monkeypatch.setattr(rendering, "renderer", lambda: None)
    pages.keep_source(capture, PAGE)
    client.force_login(keeping)

    html = client.get(reverse("jobs:capture_page", args=[capture.pk])).content.decode()
    response = client.post(reverse("jobs:capture_page_draw", args=[capture.pk]))

    assert "data-no-renderer" in html and "Draw it from the source" not in html
    assert response.status_code == 302 and response.url == reverse(
        "jobs:capture_page", args=[capture.pk]
    )
    assert not CapturedPage.objects.get().rendering


def test_nothing_is_drawn_without_a_source_to_draw_from(keeping, capture, stand_in):
    with pytest.raises(pages.NotKept) as refused:
        pages.draw_rendering(capture)

    assert refused.value.reason == "no-source"
    assert stand_in == []


def test_nothing_is_drawn_for_an_account_that_keeps_no_rendering(user, capture, stand_in):
    allow()
    want(user, source=True, rendering=False)
    pages.keep_source(capture, PAGE)

    with pytest.raises(pages.NotKept) as refused:
        pages.draw_rendering(capture)

    assert refused.value.reason == "switched-off"
    assert stand_in == []


def test_a_rendering_that_is_there_is_not_drawn_over(kept, stand_in):
    with pytest.raises(pages.NotKept) as refused:
        pages.draw_rendering(kept)

    assert refused.value.reason == "already-kept"
    assert kept.page.rendered_by == RenderedBy.CLIENT


def test_a_page_that_could_not_be_drawn_is_said_and_nothing_is_kept(
    client, keeping, capture, monkeypatch
):
    def fail(html, *, using=None):
        raise rendering.CannotDraw("The page could not be drawn.")

    monkeypatch.setattr(rendering, "renderer", lambda: rendering.Renderer("stand-in", ()))
    monkeypatch.setattr(rendering, "draw", fail)
    pages.keep_source(capture, PAGE)
    client.force_login(keeping)

    response = client.post(reverse("jobs:capture_page_draw", args=[capture.pk]))

    watched = client.get(response.url).content.decode()
    assert "That did not work." in watched and "could not be drawn" in watched
    assert not CapturedPage.objects.get().rendering


def test_a_rendering_that_was_thrown_away_can_be_drawn_again(kept, stand_in):
    pages.forget(kept.page, source=False, rendering=True)

    page = pages.draw_rendering(Capture.objects.get(pk=kept.pk))

    assert page.rendered_by == RenderedBy.INSTANCE and page.source


# ------------------------------------------------------------ taking it out, putting it back


def archive_of(user) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(export_module.write_archive(user).getvalue()))


def test_an_export_carries_what_was_kept(kept):
    archive = archive_of(kept.owner)
    document = json.loads(archive.read("postulo.json"))

    (entry,) = document["captures"]
    page = entry["page"]
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION >= 19
    assert page["source_file"] == f"media/{kept.page.source.name}"
    assert page["rendering_file"] == f"media/{kept.page.rendering.name}"
    assert page["rendering_type"] == "image/png" and page["rendered_by"] == "client"
    assert page["source_checksum"] == kept.page.source_checksum
    assert gzip.decompress(archive.read(page["source_file"])) == PAGE.encode()
    assert archive.read(page["rendering_file"]) == PNG
    assert document["counts"]["captured_pages"] == 1
    assert export_module.counts(kept.owner) == document["counts"]


def test_a_capture_that_kept_nothing_says_nothing(user, capture):
    document = export_module.build_document(user)

    assert document["captures"][0]["page"] is None
    assert document["counts"]["captured_pages"] == 0


def test_an_archive_puts_back_what_was_kept(kept, other_user):
    """Into an account that never switched keeping on: importing the archive is asking."""
    buffer = export_module.write_archive(kept.owner)

    report = importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    page = CapturedPage.objects.for_user(other_user).get()
    assert report.captured_pages == 1
    assert pages.read_source(page) == PAGE
    assert path_of(page.rendering).read_bytes() == PNG
    assert page.rendering_type == "image/png" and page.rendered_by == "client"
    assert page.source_checksum == hashlib.sha256(PAGE.encode()).hexdigest()
    for held in (page.source, page.rendering):
        assert held.name.startswith(f"captures/{other_user.pk}/"), "under the importer's own"
    assert page.source.name != kept.page.source.name, "and named afresh"


def test_an_archive_brings_nothing_an_administrator_said_no_to(kept, other_user):
    buffer = export_module.write_archive(kept.owner)
    allow(source=False, rendering=False)

    report = importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    assert Capture.objects.for_user(other_user).count() == 1, "the capture itself comes back"
    assert not CapturedPage.objects.for_user(other_user).exists()
    assert report.captured_pages == 0
    assert any("does not keep them" in line for line in report.skipped)


def test_an_archive_from_before_pages_were_kept_still_imports(user, other_user, capture):
    document = export_module.build_document(user)
    for entry in document["captures"]:
        del entry["page"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))

    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    assert Capture.objects.for_user(other_user).count() == 1


# ------------------------------------------------------------------ the two settings pages


@pytest.fixture
def admin(user):
    user.is_staff = True
    user.is_superuser = True
    user.save()
    return user


def test_a_person_switches_it_on_for_themselves(client, user):
    allow()
    client.force_login(user)

    response = client.post(
        reverse("settings:capture"), {"keep_page_source": "on", "keep_page_rendering": "on"}
    )

    assert response.status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.keep_page_source and user.profile.keep_page_rendering
    assert pages.keeping_for(user).anything


def test_the_persons_page_is_in_the_sidebar_and_says_what_is_kept(client, kept):
    client.force_login(kept.owner)

    html = client.get(reverse("settings:capture")).content.decode()

    assert html.count('aria-current="page"') == 1
    assert 'data-kept-pages="1"' in html
    assert "What your captures keep" in html


def test_a_switch_the_instance_took_away_is_shown_locked_with_the_reason(client, user):
    allow(source=True, rendering=False)
    client.force_login(user)

    html = client.get(reverse("settings:capture")).content.decode()

    locked = re.search(r'<input[^>]*name="keep_page_rendering"[^>]*>', html).group(0)
    open_ = re.search(r'<input[^>]*name="keep_page_source"[^>]*>', html).group(0)
    assert "disabled" in locked and "disabled" not in open_
    assert "aria-describedby" in locked, "and the reason is what describes it"
    assert html.count("Switched off for this whole instance") == 1


def test_a_locked_switch_cannot_be_posted_on(client, user):
    """A locked box submits nothing, and a form can be posted without a browser."""
    allow(source=True, rendering=False)
    client.force_login(user)

    client.post(
        reverse("settings:capture"), {"keep_page_source": "on", "keep_page_rendering": "on"}
    )

    user.profile.refresh_from_db()
    assert user.profile.keep_page_source is True
    assert user.profile.keep_page_rendering is False


def test_a_locked_switch_keeps_what_it_was_set_to(client, user):
    """Switching off deletes nothing, and that includes a choice somebody made."""
    allow()
    want(user, source=True, rendering=True)
    allow(source=True, rendering=False)
    client.force_login(user)

    client.post(reverse("settings:capture"), {"keep_page_source": "on"})

    user.profile.refresh_from_db()
    assert user.profile.keep_page_rendering is True, "stored, and not in force"
    assert not pages.keeping_for(user).rendering


def test_an_administrator_switches_it_on_for_the_instance(client, admin):
    client.force_login(admin)

    response = client.post(
        reverse("server:capture"),
        {
            "capture_keep_source": "true",
            "capture_keep_rendering": "false",
            "capture_rendering_max_mb": "5",
        },
    )

    assert response.status_code == 302
    assert site.capture_keep_source() is True
    assert site.capture_keep_rendering() is False
    assert site.capture_rendering_max_bytes() == 5 * 1024 * 1024
    html = client.get(reverse("server:capture")).content.decode()
    assert 'data-keeping-source="yes"' in html and 'data-keeping-rendering="no"' in html


def test_the_servers_page_says_why_it_is_off_and_what_is_held(client, admin, other_user):
    allow()
    want(other_user)
    theirs = Capture.objects.create(owner=other_user, url="https://example.org/j/9", data={})
    pages.keep_source(theirs, PAGE)
    client.force_login(admin)

    html = client.get(reverse("server:capture")).content.decode()

    assert "a decision to make on purpose" in html
    assert 'data-kept-pages="1"' in html
    assert "Gordon" not in html, "how much is held, and nothing of what"


def test_a_pinned_switch_is_shown_and_cannot_be_posted_over(client, admin, settings, monkeypatch):
    monkeypatch.setenv("POSTULO_CAPTURE_KEEP_SOURCE", "false")
    settings.POSTULO_CAPTURE_KEEP_SOURCE = False
    client.force_login(admin)

    html = client.get(reverse("server:capture")).content.decode()
    client.post(
        reverse("server:capture"),
        {"capture_keep_source": "true", "capture_keep_rendering": "true"},
    )

    assert 'data-pinned="capture_keep_source"' in html
    assert "POSTULO_CAPTURE_KEEP_SOURCE" in html
    assert SiteSettings.get().capture_keep_source is None, "what is stored is untouched"
    assert site.capture_keep_source() is False
    assert site.capture_keep_rendering() is True, "the field beside it is still the page's"


def test_a_cap_outside_what_makes_sense_is_refused(client, admin):
    client.force_login(admin)

    response = client.post(reverse("server:capture"), {"capture_rendering_max_mb": "0"})

    assert response.status_code == 200
    assert SiteSettings.get().capture_rendering_max_mb is None


def test_the_metrics_count_the_kept_pages(kept):
    """Wherever the instance says what it holds, this is in it -- and only as a number."""
    from postulo.core import metrics

    records = next(metric for metric in metrics.collect() if metric.name == "postulo_records")

    assert ({"kind": "captured_pages"}, 1) in records.samples
    assert "Gordon" not in records.render()


def test_the_pages_that_count_what_an_account_holds_count_these(client, kept):
    client.force_login(kept.owner)

    assert client.get(reverse("core:export")).context["counts"]["captured_pages"] == 1
    assert "Pages kept from captures" in client.get(reverse("core:export")).content.decode()
