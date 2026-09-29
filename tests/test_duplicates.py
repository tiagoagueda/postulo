"""Is this company, or this person, already in the record under another spelling? (#239)

Companies were matched on a name that was exactly the same, case aside, so *Acme*, *Acme
Ltd* and *ACME GmbH* became three employers; nothing compared contacts at all.

What is held to here is the stance as much as the comparison. **It tells and never
refuses** (#178): nothing stops a record being saved, nothing is matched on the way in and
nothing is merged by itself. **It is worked out when the page is drawn**, so there is no
table of suspicions to go stale. And it is **each person's own**: somebody else's *Acme*
is not a duplicate of mine, and must never be named on my page.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs import duplicates
from postulo.jobs.models import Company, CompanyIdentifier, Contact

pytestmark = pytest.mark.django_db


def company(owner, name, **fields) -> Company:
    return Company.objects.create(owner=owner, name=name, **fields)


def contact(owner, name, **fields) -> Contact:
    return Contact.objects.create(owner=owner, name=name, **fields)


def found(candidates) -> list[str]:
    return [candidate.record.name for candidate in candidates]


# ------------------------------------------------------------- a name, its form aside


@pytest.mark.parametrize(
    "spelling",
    [
        "Acme",
        "ACME",
        "Acme Ltd",
        "Acme Ltd.",
        "Acme Limited",
        "ACME GmbH",
        "Acme, S.A.",
        "Acme SA",
        "Acme S. A.",
        "Acme Lda",
        "Acme, Lda.",
        "Acme SARL",
        "Acme S.à r.l.",
        "Acme BV",
        "Acme B.V.",
        "Acme AB",
        "AB Acme",
        "Acme Oy",
        "Oy Acme Ab",
        "Acme Inc",
        "Acme, Inc.",
        "Acme GmbH & Co. KG",
        "Acme Sp. z o.o.",
        "Acme s.r.o.",
        "Acme S.p.A.",
        "Acme S.r.l.",
        "Acme S.L.",
        "Acme S.A. de C.V.",
        "Acme Pty Ltd",
        "Acme d.o.o.",
        "Acme A/S",
        "  acme   plc ",
    ],
)
def test_every_spelling_of_one_company_is_one_name(spelling):
    assert duplicates.bare_name(spelling) == "acme"


def test_every_form_on_the_list_comes_off_the_end_of_a_name():
    """The list is the one place they are kept, so the list is what is tested: a form added
    to it is a form this checks."""
    for form in duplicates.LEGAL_FORMS:
        assert duplicates.bare_name(f"Acme {form}") == "acme", form


def test_the_forms_written_first_come_off_the_front():
    for form in duplicates.LEADING_FORMS:
        assert duplicates.bare_name(f"{form} Acme") == duplicates.bare_name("Acme"), form


def test_only_the_forms_written_first_come_off_the_front():
    """A word taken off the front of a name is a bigger claim than one off the end."""
    assert duplicates.bare_name("AG Insurance") == "aginsurance"
    assert duplicates.bare_name("Inc Magazine") == "incmagazine"


@pytest.mark.parametrize("name", ["Limited", "Ltd", "GmbH", "S.A.", "AB"])
def test_a_name_is_never_stripped_to_nothing(name):
    assert duplicates.bare_name(name) != ""


def test_accents_spacing_and_hyphens_are_not_a_difference():
    assert duplicates.bare_name("Nestlé S.A.") == duplicates.bare_name("Nestle")
    assert (
        duplicates.bare_name("Coca-Cola Company")
        == duplicates.bare_name("Coca Cola")
        == duplicates.bare_name("CocaCola")
    )


def test_scripts_other_than_latin_have_forms_too():
    assert duplicates.bare_name("ООО Пример") == duplicates.bare_name("Пример")
    assert duplicates.bare_name("Παράδειγμα Α.Ε.") == duplicates.bare_name("Παράδειγμα")


def test_two_companies_are_not_one_because_both_are_limited():
    assert duplicates.bare_name("Acme Ltd") != duplicates.bare_name("Apex Ltd")


# -------------------------------------------------------------------- a website


@pytest.mark.parametrize(
    "address",
    [
        "https://acme.example",
        "https://www.acme.example/",
        "http://ACME.example/careers?x=1",
        "https://acme.example:8443/about",
        "acme.example",
    ],
)
def test_one_site_is_one_domain_however_it_is_written(address):
    assert duplicates.domain_of(address) == "acme.example"


@pytest.mark.parametrize(
    "address",
    [
        "",
        "   ",
        "not an address",
        "https://www.linkedin.com/company/acme",
        "https://uk.linkedin.com/company/acme",
        "https://github.com/acme",
        "https://[",
    ],
)
def test_an_address_that_says_nothing_about_whose_it_is(address):
    """A page on somebody else's platform is shared with everybody else who has one."""
    assert duplicates.domain_of(address) == ""


# --------------------------------------------------------------------- companies


def test_the_three_from_the_issue_are_found_from_any_of_them(user):
    acme = company(user, "Acme")
    limited = company(user, "Acme Ltd")
    german = company(user, "ACME GmbH")
    company(user, "Apex Ltd")

    assert found(duplicates.for_company(acme)) == ["ACME GmbH", "Acme Ltd"]
    assert found(duplicates.for_company(limited)) == ["ACME GmbH", "Acme"]
    assert found(duplicates.for_company(german)) == ["Acme", "Acme Ltd"]


def test_each_one_says_why(user):
    acme = company(user, "Acme", website="https://acme.example")
    company(user, "Acme Ltd", website="https://www.acme.example/about")

    [candidate] = duplicates.for_company(acme)

    assert candidate.reasons == (
        "The same name, once the legal form is set aside",
        "The same website: acme.example",
    )


def test_the_same_website_under_another_name(user):
    aperture = company(user, "Aperture Science", website="https://aperture.example")
    company(user, "Aperture Laboratories", website="http://www.aperture.example/labs")
    company(user, "Black Mesa", website="https://blackmesa.example")

    assert found(duplicates.for_company(aperture)) == ["Aperture Laboratories"]


def test_two_profiles_on_one_network_are_not_one_company(user):
    first = company(user, "Aperture", website="https://www.linkedin.com/company/aperture")
    company(user, "Black Mesa", website="https://www.linkedin.com/company/black-mesa")

    assert duplicates.for_company(first) == []


def test_the_same_identifier(user):
    """Only *Other* can be on two of one person's companies at once -- every named scheme is
    unique among them -- and it is the one a national register number is typed under."""
    first = company(user, "Aperture Science")
    second = company(user, "Aperture Fixtures")
    third = company(user, "Black Mesa")
    for holder, value in ((first, "500 123 456"), (second, "500 123 456"), (third, "999")):
        CompanyIdentifier.objects.create(
            owner=user, company=holder, scheme="other", label="NIPC", value=value
        )

    [candidate] = duplicates.for_company(first)

    assert candidate.record == second
    assert candidate.reasons == ("The same identifier: NIPC: 500 123 456",)


def test_an_identifier_is_the_same_whatever_its_case(user):
    first = company(user, "Aperture Science")
    second = company(user, "Aperture Fixtures")
    CompanyIdentifier.objects.create(
        owner=user, company=first, scheme="other", label="Staff", value="AB-12"
    )
    CompanyIdentifier.objects.create(
        owner=user, company=second, scheme="other", label="staff", value="ab-12"
    )

    assert found(duplicates.for_company(first)) == ["Aperture Fixtures"]


def test_the_same_number_under_another_name_is_another_identifier(user):
    """*Other* is a free slot, and 12 as a staff number is not 12 as an office."""
    first = company(user, "Aperture Science")
    second = company(user, "Black Mesa")
    CompanyIdentifier.objects.create(
        owner=user, company=first, scheme="other", label="Staff", value="12"
    )
    CompanyIdentifier.objects.create(
        owner=user, company=second, scheme="other", label="Office", value="12"
    )

    assert duplicates.for_company(first) == []


def test_a_company_is_not_a_duplicate_of_itself(user):
    only = company(user, "Acme Ltd", website="https://acme.example")

    assert duplicates.for_company(only) == []


def test_somebody_elses_company_is_never_a_duplicate_of_mine(user, other_user):
    mine = company(user, "Acme", website="https://acme.example")
    company(other_user, "Acme Ltd", website="https://acme.example")

    assert duplicates.for_company(mine) == []


def test_a_company_not_yet_saved_has_nothing_to_be_compared_with(user):
    company(user, "Acme")

    assert duplicates.for_company(Company(owner=user, name="Acme Ltd")) == []


# ---------------------------------------------------------------------- contacts


def test_the_same_name_case_accents_and_spacing_aside(user):
    rene = contact(user, "René  Dupont")
    contact(user, "rene dupont")
    contact(user, "Renée Dupond")

    [candidate] = duplicates.for_contact(rene)

    assert candidate.record.name == "rene dupont"
    assert candidate.reasons == ("The same name",)


def test_the_same_email_address_whatever_the_name(user):
    first = contact(user, "Cave Johnson", email="cave@aperture.example")
    contact(user, "C. Johnson", email="Cave@Aperture.example")
    contact(user, "Caroline", email="caroline@aperture.example")

    [candidate] = duplicates.for_contact(first)

    assert candidate.record.name == "C. Johnson"
    assert candidate.reasons == ("The same email address: cave@aperture.example",)


def test_two_people_with_no_address_do_not_share_one(user):
    first = contact(user, "Cave Johnson")
    contact(user, "Caroline")

    assert duplicates.for_contact(first) == []


def test_somebody_who_changed_employer_is_found_at_the_other_one(user):
    """Recorded twice exactly because they moved, so where they work is not compared."""
    here = company(user, "Aperture Science")
    there = company(user, "Black Mesa")
    first = contact(user, "Doug Rattmann", company=here)
    contact(user, "Doug Rattmann", company=there)

    [candidate] = duplicates.for_contact(first)

    assert candidate.record.company == there


def test_somebody_elses_contact_is_never_a_duplicate_of_mine(user, other_user):
    mine = contact(user, "Cave Johnson", email="cave@aperture.example")
    contact(other_user, "Cave Johnson", email="cave@aperture.example")

    assert duplicates.for_contact(mine) == []


def test_which_of_several_people_have_one(user, other_user):
    here = company(user, "Aperture Science")
    twice = contact(user, "Doug Rattmann", company=here)
    once = contact(user, "Caroline", company=here)
    contact(user, "Doug Rattmann")
    contact(other_user, "Caroline")

    assert duplicates.contacts_with_any(user, [twice, once]) == {twice.pk}
    assert duplicates.contacts_with_any(user, []) == set()


# ------------------------------------------------------ it tells, and never refuses


def test_a_company_that_looks_like_another_is_saved_all_the_same(client, user):
    company(user, "Acme")
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_create"),
        {
            "name": "Acme Ltd",
            "kind": "employer",
            "identifiers-TOTAL_FORMS": "0",
            "identifiers-INITIAL_FORMS": "0",
        },
    )

    assert response.status_code == 302, response.context["form"].errors
    made = Company.objects.get(owner=user, name="Acme Ltd")
    page = client.get(made.get_absolute_url()).content.decode()
    assert "data-possible-duplicates" in page, "and the page it leads to says what it noticed"


def test_recording_an_application_matches_nothing_on_the_way_in(user):
    """*Acme Ltd* and *Acme GmbH* may be two companies, and only the person knows."""
    from postulo.applications.services import get_or_create_company

    acme = company(user, "Acme")

    assert get_or_create_company(user, "Acme Ltd") != acme
    assert Company.objects.for_user(user).count() == 2


def test_a_contact_that_looks_like_another_is_saved_all_the_same(client, user):
    contact(user, "Cave Johnson", email="cave@aperture.example")
    client.force_login(user)

    response = client.post(
        reverse("jobs:contact_create"),
        {"name": "Cave Johnson", "email": "cave@aperture.example"},
    )

    assert response.status_code == 302
    assert Contact.objects.for_user(user).filter(name="Cave Johnson").count() == 2


# ------------------------------------------------------------------- on the page


def test_the_company_page_says_what_it_noticed_and_offers_to_merge(client, user):
    acme = company(user, "Acme", website="https://acme.example")
    limited = company(user, "Acme Ltd", website="https://acme.example", location="Leeds")
    client.force_login(user)

    page = client.get(acme.get_absolute_url()).content.decode()
    notice = page[page.index("data-possible-duplicates") :]
    notice = notice[: notice.index("</section>")]

    assert "Possible duplicates" in notice
    assert "Acme Ltd" in notice and "Leeds" in notice
    assert "The same name, once the legal form is set aside" in notice
    assert "The same website: acme.example" in notice
    merge = f"{reverse('jobs:company_merge', args=[acme.pk])}?with={limited.pk}"
    assert f'href="{merge}"' in notice
    assert limited.get_absolute_url() in notice, "and the other one can be looked at first"


def test_the_company_page_says_nothing_where_there_is_nothing(client, user):
    only = company(user, "Acme")
    company(user, "Black Mesa")
    client.force_login(user)

    page = client.get(only.get_absolute_url()).content.decode()

    assert "data-possible-duplicates" not in page
    assert reverse("jobs:company_merge", args=[only.pk]) in page, "merging is offered all the same"


def test_the_notice_never_names_somebody_elses_company(client, user, other_user):
    mine = company(user, "Acme")
    company(other_user, "Acme Secret Holdings Ltd", website="https://acme.example")
    company(other_user, "Acme Ltd")
    client.force_login(user)

    page = client.get(mine.get_absolute_url()).content.decode()

    assert "data-possible-duplicates" not in page
    assert "Secret" not in page


def test_it_is_worked_out_when_the_page_is_drawn(client, user):
    """Nothing is kept about who looks like whom, so nothing about it can go stale."""
    acme = company(user, "Acme")
    other = company(user, "Acme Ltd")
    client.force_login(user)
    assert "data-possible-duplicates" in client.get(acme.get_absolute_url()).content.decode()

    other.name = "Apex Ltd"
    other.save()

    assert "data-possible-duplicates" not in client.get(acme.get_absolute_url()).content.decode()


def test_the_notice_costs_the_same_however_many_companies_there_are(client, user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    acme = company(user, "Acme")
    company(user, "Acme Ltd")
    client.force_login(user)

    def cost() -> int:
        client.get(acme.get_absolute_url())
        with CaptureQueriesContext(connection) as captured:
            assert client.get(acme.get_absolute_url()).status_code == 200
        return len(captured)

    few = cost()
    for number in range(40):
        company(user, f"Employer {number}", website=f"https://employer-{number}.example")

    assert cost() == few


def test_the_contacts_page_says_what_it_noticed(client, user):
    here = company(user, "Aperture Science")
    first = contact(user, "Cave Johnson", company=here, email="cave@aperture.example")
    second = contact(user, "Cave Johnson", role="Founder")
    client.force_login(user)

    page = client.get(reverse("jobs:contact_update", args=[first.pk])).content.decode()
    notice = page[page.index("data-possible-duplicates") :]
    notice = notice[: notice.index("</section>")]

    assert "The same name" in notice and "Founder" in notice
    merge = f"{reverse('jobs:contact_merge', args=[first.pk])}?with={second.pk}"
    assert f'href="{merge}"' in notice


def test_a_new_contact_has_nothing_to_be_compared_with(client, user):
    contact(user, "Cave Johnson")
    client.force_login(user)

    page = client.get(reverse("jobs:contact_create")).content.decode()

    assert "data-possible-duplicates" not in page
    assert "Merge with another contact" not in page


def test_the_company_page_marks_the_people_recorded_twice(client, user):
    here = company(user, "Aperture Science")
    twice = contact(user, "Doug Rattmann", company=here)
    contact(user, "Caroline", company=here)
    contact(user, "Doug Rattmann")
    client.force_login(user)

    page = client.get(here.get_absolute_url()).content.decode()

    assert page.count("data-person-alike") == 1
    assert reverse("jobs:contact_merge", args=[twice.pk]) in page


# ------------------------------------------------------------------ the components


def drawn(source: str, **context) -> str:
    """A template from a string, through the project's engine, on one line.

    A string never passes through the loader that compiles ``<c-…>`` tags, so the native
    ``{% cotton %}`` tag is used, as `tests/test_components.py` does.
    """
    from django.template import Context, engines

    html = engines["django"].engine.from_string(source).render(Context(context))
    return " ".join(html.split())


def test_a_person_is_told_apart_by_what_they_do_where_and_their_address(user):
    here = company(user, "Aperture Science")
    person = contact(user, "Cave Johnson", company=here, role="Founder", email="cave@a.example")

    html = drawn('{% cotton told-apart :record="record" / %}', record=person)

    assert "<bdi>Founder</bdi> · <bdi>Aperture Science</bdi> · <bdi>cave@a.example</bdi>" in html
    assert 'class="block text-xs wrap-anywhere"' in html


def test_a_company_is_told_apart_by_where_it_is(user):
    html = drawn(
        '{% cotton told-apart :record="record" class="text-ink-500" / %}',
        record=company(user, "Acme", location="Leeds"),
    )

    assert "<bdi>Leeds</bdi>" in html and "·" not in html
    assert 'class="block text-xs wrap-anywhere text-ink-500"' in html


def test_a_record_with_nothing_to_tell_it_apart_draws_nothing(user):
    assert drawn('{% cotton told-apart :record="record" / %}', record=contact(user, "Chell")) == ""


def test_the_notice_draws_nothing_where_nothing_was_noticed():
    assert drawn('{% cotton duplicates :candidates="found" / %}', found=[]) == ""
    assert drawn('{% cotton duplicates :candidates="found" / %}') == "", "nor where none was given"


def test_the_notice_says_it_in_words_and_offers_only_the_page_that_shows_first(user):
    """Colour is never the only thing saying something: the box is tinted, and the heading,
    the sentence and each reason say it whether or not anybody sees the tint."""
    acme = company(user, "Acme")
    company(user, "Acme Ltd", location="Leeds")
    found = [(candidate, "/merge/?with=2") for candidate in duplicates.for_company(acme)]

    html = drawn('{% cotton duplicates :candidates="found" / %}', found=found)

    assert 'class="alert mb-6 flex-col items-stretch" data-variant="info"' in html
    assert 'aria-labelledby="possible-duplicates"' in html
    assert '<h2 id="possible-duplicates" class="font-medium">Possible duplicates</h2>' in html
    assert "Nothing is merged unless you say so" in html
    assert "<li>The same name, once the legal form is set aside</li>" in html
    assert '<a href="/merge/?with=2" class="btn" data-variant="outline" data-size="sm">' in html
    assert 'Merge<span class="sr-only">: <bdi>Acme Ltd</bdi></span>' in html, (
        "a page of these is a page of buttons that all say Merge, unless each says which"
    )
    assert "<form" not in html and "<button" not in html, "a link to a page, and nothing that acts"
