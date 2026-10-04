"""Remembered places, Postulo's half: learned on review, used at capture, scored, forgotten (#267).

`test_remembered_places.py` reads pages. This is what a person sees and what the rows do: a
correction on the review screen is remembered where the page showed it, the next capture from
the same site is read there and says so, a place that is wrong twice in a row is dropped, and
the rows are the person's own -- used only for their captures, listed and forgotten from their
own settings, carried in their archive and gone with their account.

The pages are the council notices in ``data/remembered/``: a first notice to learn from, and
the same council's next notice after a small redesign.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import zipfile
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.core import export as export_module
from postulo.core import importer, site
from postulo.jobs import pages, remembered
from postulo.jobs.capture_views import REMEMBERED
from postulo.jobs.models import Capture, CaptureStatus, FieldHint
from postulo.plugins import registry
from postulo.plugins.base import RememberedPlace
from postulo.plugins.builtin import PageMetadataSource

pytestmark = pytest.mark.django_db

PAGES = pathlib.Path(__file__).parent / "data" / "remembered"
SITE = "emprego.cm-lisboa.example"
FIRST = f"https://www.{SITE}/ofertas/12"
SECOND = f"https://{SITE}/ofertas/13"

COUNCIL = "Câmara Municipal de Lisboa"


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


def capture(client, url: str, html: str) -> Capture:
    """Capture a page as the web form does, with the page pasted in, and hand it back."""
    response = client.post(reverse("jobs:capture_create"), {"url": url, "html": html})
    assert response.status_code == 302, response.status_code
    return Capture.objects.filter(url=url).latest("created_at")


def as_posted(capture: Capture, **changes) -> dict:
    """The review form as it opens for this capture, with ``changes`` typed over it."""
    data = capture.posting_data
    posted = {
        "title": data.title,
        "company_name": data.company_name,
        "url": data.url or capture.url,
        "location": data.location,
        "remote_type": data.remote_type,
        "employment_type": data.employment_type,
        "source": data.source,
        "salary_min": "" if data.salary_min is None else str(data.salary_min),
        "salary_max": "" if data.salary_max is None else str(data.salary_max),
        "salary_currency": data.salary_currency or "EUR",
        "salary_period": data.salary_period or "year",
        "closes_at": data.closes_at.isoformat() if data.closes_at else "",
        "description": data.description,
    }
    return {**posted, **changes}


def review(client, capture: Capture, **changes):
    response = client.post(
        reverse("jobs:capture_review", args=[capture.pk]), as_posted(capture, **changes)
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    capture.refresh_from_db()
    return response


def places_of(owner) -> dict[str, dict]:
    return {hint.field: hint.place for hint in FieldHint.objects.for_user(owner)}


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def taught(signed_in, user):
    """The first notice captured and corrected: the council and the place typed in."""
    first = capture(signed_in, FIRST, page("notice"))
    assert first.data["company_name"] == "" and first.data["location"] == ""
    review(signed_in, first, company_name=COUNCIL, location="Lisboa")
    return first


# ------------------------------------------------------------------ learning


def test_a_correction_is_remembered_where_the_page_showed_it(taught, user):
    assert places_of(user) == {
        "company_name": {"id": "entidade"},
        "location": {"class": "aviso-local", "tag": "p"},
    }
    hint = FieldHint.objects.for_user(user).get(field="location")
    assert hint.host == SITE, "the site, without its www."
    assert hint.misses == 0


def test_a_field_left_alone_teaches_nothing(signed_in, user):
    """The title was right, or was not checked: either way it is not evidence -- though the
    page shows it, in its one heading. The company has to be typed, and is not on the page."""
    first = capture(signed_in, FIRST, page("notice"))

    review(signed_in, first, company_name="An employer the page does not name")

    assert places_of(user) == {}


def test_a_value_the_page_never_showed_teaches_nothing(signed_in, user):
    first = capture(signed_in, FIRST, page("notice"))

    review(signed_in, first, company_name="Somebody else entirely", location="Sintra")

    assert places_of(user) == {}


def test_a_decided_capture_keeps_nothing_to_learn_from(taught):
    assert taught.status == CaptureStatus.ACCEPTED
    assert taught.learning == {}


def test_a_discarded_capture_teaches_nothing_and_keeps_nothing(signed_in, user):
    first = capture(signed_in, FIRST, page("notice"))
    assert first.learning.get("places"), "kept while it waits, since no source was kept"

    signed_in.post(reverse("jobs:capture_discard", args=[first.pk]))

    first.refresh_from_db()
    assert first.learning == {}
    assert places_of(user) == {}


def test_what_waits_with_a_capture_is_not_the_page(signed_in):
    first = capture(signed_in, FIRST, page("notice"))
    kept = json.dumps(first.learning, ensure_ascii=False)

    for words in ("Técnico Superior", COUNCIL, "Procedimento concursal", "1.385,99"):
        assert words not in kept, words


# ------------------------------------------------------------------ using it


def test_the_next_capture_from_the_site_is_read_where_the_corrections_were(taught, signed_in):
    second = capture(signed_in, SECOND, page("notice-redesigned"))

    assert second.data["company_name"] == COUNCIL
    assert second.data["location"] == "Porto", "the place, not the value that taught it"
    assert second.data["title"] == "Assistente Técnico de Arquivo", "the page's own heading"


def test_the_review_screen_says_which_fields_came_from_a_remembered_place(taught, signed_in):
    second = capture(signed_in, SECOND, page("notice-redesigned"))

    html = signed_in.get(reverse("jobs:capture_review", args=[second.pk])).content.decode()

    assert "data-remembered" in html
    assert html.count(str(REMEMBERED)) == 2, "in words, at each of the two fields"
    title_help = html.split('id="id_title_helptext"')[1].split("</p>")[0]
    assert str(REMEMBERED) not in title_help, "the heading was the page's own"
    assert reverse("settings:capture") in html, "and where to forget them"


def test_a_site_with_nothing_remembered_is_read_and_shown_as_before(signed_in):
    first = capture(signed_in, FIRST, page("notice"))

    html = signed_in.get(reverse("jobs:capture_review", args=[first.pk])).content.decode()

    assert "data-remembered" not in html and str(REMEMBERED) not in html
    assert "hints" not in first.learning


def test_what_a_place_finds_is_bounded_like_any_other_tier(taught, signed_in, user):
    """A place is read as the other tiers read its field, and falls through where they
    would find nothing: more than a title's five hundred characters is not a title, and
    a word the vocabulary does not know is not an employment type."""
    FieldHint.objects.create(
        owner=user, host=SITE, field="title", place={"data": ["data-qa", "descricao"]}
    )
    FieldHint.objects.create(owner=user, host=SITE, field="employment_type", place={"id": "regime"})
    longer = page("notice-redesigned").replace(
        '<div data-qa="descricao">',
        '<p id="regime">Tempo inteiro</p><div data-qa="descricao"><p>' + "Mais. " * 60 + "</p>",
    )

    second = capture(signed_in, SECOND, longer)

    assert second.data["title"] == "Assistente Técnico de Arquivo", "the page's own heading"
    assert second.data["employment_type"] == ""
    outcomes = {entry["field"]: entry["outcome"] for entry in second.learning["hints"]}
    assert outcomes["title"] == outcomes["employment_type"] == remembered.MISSED


# ------------------------------------------------------------------ the score


def test_a_place_kept_on_review_is_right_and_its_count_starts_again(taught, signed_in, user):
    FieldHint.objects.for_user(user).filter(field="company_name").update(misses=1)
    second = capture(signed_in, SECOND, page("notice-redesigned"))

    review(signed_in, second)

    assert FieldHint.objects.for_user(user).get(field="company_name").misses == 0


def test_a_place_wrong_twice_in_a_row_is_forgotten(taught, signed_in, user):
    second = capture(signed_in, SECOND, page("notice-redesigned"))
    review(signed_in, second, location="Braga")
    assert FieldHint.objects.for_user(user).get(field="location").misses == 1

    third = capture(signed_in, f"https://{SITE}/ofertas/14", page("notice-redesigned"))
    assert third.data["location"] == "Porto", "one failure is not yet a verdict"
    review(signed_in, third, location="Braga")

    assert not FieldHint.objects.for_user(user).filter(field="location").exists()
    assert FieldHint.objects.for_user(user).filter(field="company_name").exists()


def test_a_place_that_found_nothing_where_the_person_typed_something_was_wrong(signed_in, user):
    FieldHint.objects.create(owner=user, host=SITE, field="company_name", place={"id": "gone"})
    second = capture(signed_in, SECOND, page("notice-redesigned"))
    assert second.data["company_name"] == ""

    review(signed_in, second, company_name=COUNCIL)

    hint = FieldHint.objects.for_user(user).get(field="company_name")
    assert hint.place == {"id": "entidade"}, "relearned where the value now is"
    assert hint.misses == 0


def test_a_place_that_found_nothing_where_there_was_nothing_is_not_blamed(signed_in, user):
    """An advert with no salary is not evidence against where salaries are written."""
    FieldHint.objects.create(
        owner=user, host=SITE, field="salary", place={"id": "remuneracao"}, misses=1
    )
    second = capture(signed_in, SECOND, page("notice-redesigned").replace("1.020,06 €", ""))

    review(signed_in, second, company_name=COUNCIL, salary_min="", salary_max="")

    assert FieldHint.objects.for_user(user).get(field="salary").misses == 1


def test_a_capture_never_reviewed_says_nothing_about_its_places(taught, signed_in, user):
    capture(signed_in, SECOND, page("notice-redesigned"))

    assert {hint.misses for hint in FieldHint.objects.for_user(user)} == {0}


# ------------------------------------------------------------------ a kept page


@pytest.fixture
def keeping_sources(user):
    from postulo.core import site
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"capture_keep_source": True})
    site.forget_current()
    user.profile.keep_page_source = True
    user.profile.save(update_fields=["keep_page_source"])


def test_where_the_source_is_kept_the_review_reads_the_page_from_it(
    keeping_sources, signed_in, user
):
    first = capture(signed_in, FIRST, page("notice"))
    assert first.kept_page is not None and first.kept_page.source
    assert "places" not in first.learning, "never a second copy of what the source holds"

    review(signed_in, first, company_name=COUNCIL)

    assert places_of(user) == {"company_name": {"id": "entidade"}}


def test_a_kept_source_deleted_before_review_leaves_the_lesson_behind(
    keeping_sources, signed_in, user
):
    first = capture(signed_in, FIRST, page("notice"))

    pages.forget(first.kept_page)
    first.refresh_from_db()
    assert first.learning.get("places")
    review(signed_in, first, company_name=COUNCIL)

    assert places_of(user) == {"company_name": {"id": "entidade"}}


def test_a_kept_source_expired_before_review_leaves_the_lesson_behind(
    keeping_sources, signed_in, user
):
    first = capture(signed_in, FIRST, page("notice"))
    Capture.objects.filter(pk=first.pk).update(
        created_at=timezone.now() - dt.timedelta(days=site.capture_page_keep_days() + 1)
    )

    assert pages.expire_unconfirmed() == 1
    first.refresh_from_db()
    assert first.kept_page is None
    assert first.learning.get("places")
    review(signed_in, first, company_name=COUNCIL)

    assert places_of(user) == {"company_name": {"id": "entidade"}}


# ------------------------------------------------------------------ the API


@pytest.fixture
def bearer(user):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Extension")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path: str, bearer, payload: dict):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **bearer)


def test_corrections_sent_with_a_capture_teach_as_a_review_does(client, bearer, user):
    response = post(
        client,
        "/api/v1/captures",
        bearer,
        {"url": FIRST, "html": page("notice"), "data": {"company_name": COUNCIL}},
    )

    assert response.status_code == 201, response.content
    assert places_of(user) == {"company_name": {"id": "entidade"}}


def test_a_preview_reads_with_the_places_says_which_and_scores_nothing(client, bearer, user):
    FieldHint.objects.create(
        owner=user, host=SITE, field="company_name", place={"id": "entidade"}, misses=1
    )
    FieldHint.objects.create(owner=user, host=SITE, field="location", place={"id": "gone"})

    response = post(
        client, "/api/v1/captures/preview", bearer, {"url": SECOND, "html": page("notice")}
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["data"]["company_name"] == COUNCIL
    assert body["hinted"] == ["company_name"]
    assert {hint.field: hint.misses for hint in FieldHint.objects.for_user(user)} == {
        "company_name": 1,
        "location": 0,
    }
    assert not Capture.objects.for_user(user).exists()


# ------------------------------------------------------------------ one person's own


def test_somebody_else_s_places_are_never_used_for_my_capture(taught, client, other_user):
    client.force_login(other_user)

    theirs = capture(client, SECOND, page("notice-redesigned"))

    assert theirs.data["company_name"] == ""
    assert "hints" not in theirs.learning


def test_the_sources_somebody_else_wrote_are_never_handed_a_person_s_places(monkeypatch):
    handed = []

    class Mine(PageMetadataSource):
        """A subclass of Postulo's own is somebody else's source all the same."""

        def parse(self, url, html, hints=None):
            handed.append(hints)
            return super().parse(url, html)

    monkeypatch.setattr(registry, "available_sources", lambda **_kw: [Mine()])
    remembered_place = RememberedPlace("location", {"class": "aviso-local", "tag": "p"})

    registry.parse_page(SECOND, page("notice-redesigned"), hints=[remembered_place])

    assert handed == [None]


def test_the_settings_page_lists_what_is_remembered_by_site(taught, signed_in):
    html = signed_in.get(reverse("settings:capture")).content.decode()

    assert f'data-remembered-site="{SITE}"' in html
    assert "Company" in html and "Location" in html


def test_forgetting_a_site_forgets_only_mine(taught, signed_in, user, other_user):
    FieldHint.objects.create(owner=other_user, host=SITE, field="title", place={"id": "t"})
    FieldHint.objects.create(owner=user, host="elsewhere.example", field="title", place={"id": "t"})

    response = signed_in.post(reverse("settings:capture_forget"), {"host": SITE})

    assert response.status_code == 302
    assert set(FieldHint.objects.for_user(user).values_list("host", flat=True)) == {
        "elsewhere.example"
    }
    assert FieldHint.objects.for_user(other_user).filter(host=SITE).exists()


def test_forgetting_somebody_else_s_site_forgets_nothing(signed_in, other_user):
    FieldHint.objects.create(owner=other_user, host=SITE, field="title", place={"id": "t"})

    signed_in.post(reverse("settings:capture_forget"), {"host": SITE})

    assert FieldHint.objects.for_user(other_user).count() == 1


def test_forgetting_is_a_post_and_needs_a_person(client, signed_in):
    assert signed_in.get(reverse("settings:capture_forget")).status_code == 405
    client.logout()
    response = client.post(reverse("settings:capture_forget"), {"host": SITE})
    assert response.status_code == 302 and "login" in response.url


# ------------------------------------------------------------------ archive and account


def test_the_places_are_in_the_archive_and_come_back_from_it(taught, user, other_user):
    document = export_module.build_document(user)

    # 25 is the format that added them (#267); the number itself is pinned in
    # test_phone_verification.py, where a change to it is written down.
    assert document["postulo"]["format"] >= 25
    assert {row["field"] for row in document["remembered_places"]} == {"company_name", "location"}
    assert export_module.counts(user)["remembered_places"] == 2

    report = importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))

    assert report.remembered_places == 2
    assert places_of(other_user) == places_of(user)


def test_an_archive_s_places_are_checked_not_believed(user, other_user):
    good = {"host": SITE, "field": "location", "place": {"id": "local"}, "misses": 7}
    bad = [
        {"host": SITE, "field": "not_a_field", "place": {"id": "x"}},
        {"host": SITE, "field": "title", "place": {"onclick": "alert(1)"}},
        {"host": "evil.example/path", "field": "title", "place": {"id": "x"}},
        {"host": SITE, "field": "title", "place": {"id": "x", "extra": "y"}},
        "not even a row",
    ]
    document = export_module.build_document(user)
    document["remembered_places"] = [good, *bad]
    archive = export_module.write_archive(user)
    rewritten = _with_manifest(archive, document)

    report = importer.load(other_user, zipfile.ZipFile(rewritten))

    assert report.remembered_places == 1
    hint = FieldHint.objects.for_user(other_user).get()
    assert (hint.field, hint.place) == ("location", {"id": "local"})
    assert hint.misses == FieldHint.DROPPED_AFTER - 1, "never past the point of being dropped"
    assert len([line for line in report.skipped if "remembered place" in line]) == len(bad)


def _with_manifest(archive, document):
    """The same archive with its manifest replaced."""
    import io

    source = zipfile.ZipFile(archive)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            if item.filename == export_module.MANIFEST_NAME:
                target.writestr(item, json.dumps(document, default=str))
            else:
                target.writestr(item, source.read(item))
    out.seek(0)
    return out


def test_deleting_the_account_deletes_the_places(taught, user, other_user):
    from postulo.accounts import deletion

    FieldHint.objects.create(owner=other_user, host=SITE, field="title", place={"id": "t"})

    deletion.delete_account(user)

    assert not FieldHint.objects.filter(owner_id=user.pk).exists()
    assert FieldHint.objects.for_user(other_user).count() == 1


# ------------------------------------------------------------------ the values compared


@pytest.mark.parametrize(
    "read,saved,expected",
    [
        ({"salary_min": "45000.00"}, {"salary_min": Decimal("45000")}, []),
        ({"closes_at": "2026-10-30"}, {"closes_at": dt.date(2026, 10, 30)}, []),
        ({"title": "Engineer "}, {"title": "Engineer"}, []),
        ({"title": "Engineer"}, {"title": ""}, []),
        ({"salary_currency": ""}, {"salary_currency": "EUR"}, []),
        ({"title": "Engineer"}, {"title": "Senior Engineer"}, ["title"]),
        ({}, {"salary_max": Decimal("50000")}, ["salary"]),
    ],
)
def test_only_a_field_changed_to_something_is_a_correction(read, saved, expected):
    """The review form fills a currency and a period the page never stated, so those two
    alone are never a correction; the pay is its figures."""
    assert remembered.touched(read, saved) == expected
