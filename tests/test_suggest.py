"""The suggestion contract: one registry, one endpoint, the person's own data (#1251)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from postulo.applications.forms import PostingIntakeForm
from postulo.core import suggest
from postulo.jobs.forms import ContactForm, JobPostingForm
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.resume.forms import ExperienceForm

pytestmark = pytest.mark.django_db


def posting(user, *, company="Aperture Science", title="Test Engineer", **more):
    firm, _made = Company.objects.get_or_create(owner=user, name=company)
    return JobPosting.objects.create(owner=user, company=firm, title=title, **more)


def ask(client, kind, typed=""):
    return client.get(reverse("core:suggest", args=[kind]), {"q": typed})


# ------------------------------------------------------------------------------ matching


def test_beginnings_come_first_then_words_then_the_rest():
    found = suggest.matching(["Backend lead", "Lead engineer", "Misleading", "Zed"], "lead")

    assert found == ["Lead engineer", "Backend lead", "Misleading"]


def test_case_and_accents_do_not_matter():
    assert suggest.matching(["Zürich", "Genève"], "zur") == ["Zürich"]
    assert suggest.matching(["Zürich", "Genève"], "GENE") == ["Genève"]


def test_each_value_once_and_at_most_ten():
    many = [f"Town {n}" for n in range(30)]

    assert len(suggest.matching(many, "")) == suggest.AT_MOST
    assert suggest.matching(["Lyon", "lyon", " Lyon "], "ly") == ["Lyon"]


# ------------------------------------------------------------------------------ registry


def test_a_plugin_adds_a_kind_with_one_call(user):
    suggest.register("helloworld.greeting", lambda who, typed: ["Hello", "Hola"])
    try:
        assert suggest.suggest("helloworld.greeting", user, "ho") == ["Hola"]
    finally:
        suggest._registry.pop("helloworld.greeting")


def test_a_kind_is_spelled_as_a_path_segment():
    with pytest.raises(ValueError):
        suggest.register("../etc", lambda who, typed: [])
    with pytest.raises(ValueError):
        suggest.register("Upper", lambda who, typed: [])


def test_an_unknown_kind_and_a_stranger_get_nothing(user):
    from django.contrib.auth.models import AnonymousUser

    assert suggest.suggest("nothing-here", user, "a") == []
    assert suggest.suggest("company", AnonymousUser(), "a") == []


# ------------------------------------------------------------------------------ endpoint


def test_the_endpoint_answers_with_list_items_of_the_persons_own(client, user):
    posting(user, company="Aperture Science", location="Cambridge", source="LinkedIn")
    client.force_login(user)

    html = ask(client, "company", "ape").content.decode()

    assert '<li role="option" data-value="Aperture Science">Aperture Science</li>' in html


def test_what_is_typed_is_escaped_on_the_way_out(client, user):
    posting(user, company='<img src=x onerror="a">')
    client.force_login(user)

    html = ask(client, "company", "<img").content.decode()

    assert "<img" not in html
    assert "&lt;img" in html


def test_one_person_is_never_offered_anothers_records(client, user, other_user):
    posting(other_user, company="Black Mesa", title="Secret Title", location="Lyon", source="Z")
    Contact.objects.create(
        owner=other_user,
        company=Company.objects.get(owner=other_user),
        name="Gordon",
        role="Secret role",
    )
    client.force_login(user)

    for kind in ("company", "employer", "job-title", "location", "source", "contact-role"):
        body = ask(client, kind, "").content.decode()
        for secret in ("Black Mesa", "Secret Title", "Lyon", "Secret role"):
            assert secret not in body, (kind, secret)


def test_every_builtin_kind_answers(client, user):
    posting(user, title="Staff Engineer", location="Lyon", source="A friend")
    firm = Company.objects.get(owner=user)
    Contact.objects.create(owner=user, company=firm, name="Ann", role="Recruiter")
    client.force_login(user)

    for kind, typed, wanted in [
        ("company", "aper", "Aperture Science"),
        ("employer", "aper", "Aperture Science"),
        ("job-title", "staff", "Staff Engineer"),
        ("location", "ly", "Lyon"),
        ("source", "frie", "A friend"),
        ("contact-role", "rec", "Recruiter"),
    ]:
        assert f'data-value="{wanted}"' in ask(client, kind, typed).content.decode(), kind


def test_an_unknown_kind_is_a_404(client, user):
    client.force_login(user)

    assert ask(client, "nothing-here").status_code == 404


def test_a_stranger_is_sent_to_sign_in(client):
    assert ask(client, "company").status_code == 302


def test_the_endpoint_is_read_only(client, user):
    client.force_login(user)

    assert client.post(reverse("core:suggest", args=["company"])).status_code == 405


def test_the_answer_is_not_cached_between_people(client, user):
    client.force_login(user)

    assert "no-store" in ask(client, "company")["Cache-Control"]


@override_settings(POSTULO_SUGGESTION_RATE="2/h")
def test_the_rate_is_bounded_per_account(client, user):
    cache.clear()
    client.force_login(user)

    codes = [ask(client, "company", "a").status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_the_title_offers_own_titles_before_the_classification(client, user):
    posting(user, title="Zebra wrangler")
    client.force_login(user)

    body = ask(client, "job-title", "").content.decode()

    assert body.index('data-value="Zebra wrangler"') == body.index('data-value="')
    assert body.count("data-value=") <= suggest.AT_MOST


# ------------------------------------------------------------------- the fields' markup


def rendered(form, name):
    return str(form[name])


def test_the_covered_fields_name_their_kind_and_keep_their_datalist(user):
    form = PostingIntakeForm(user=user)
    for name, kind, listed in [
        ("company_name", "company", "company-suggestions"),
        ("title", "job-title", "title-suggestions"),
        ("location", "location", "location-suggestions"),
        ("source", "source", "source-suggestions"),
    ]:
        html = rendered(form, name)
        assert f'data-suggest="{reverse("core:suggest", args=[kind])}"' in html, name
        assert f'list="{listed}"' in html, name
        assert 'autocomplete="off"' in html


def test_the_other_forms_with_the_same_fields_use_the_same_kinds(user):
    listing = JobPostingForm(user=user)
    for name, kind in [("title", "job-title"), ("location", "location"), ("source", "source")]:
        assert reverse("core:suggest", args=[kind]) in rendered(listing, name)
    assert reverse("core:suggest", args=["contact-role"]) in rendered(
        ContactForm(user=user), "role"
    )
    assert reverse("core:suggest", args=["employer"]) in rendered(
        ExperienceForm(user=user), "organisation"
    )


def test_a_box_without_a_list_is_a_plain_text_box_with_scripts_off(user):
    html = rendered(JobPostingForm(user=user), "location")

    assert "list=" not in html
    assert 'type="text"' in html
