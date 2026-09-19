"""The order of the career sections is the person's, and the arrows move an entry past its
neighbour (#203).

Until #203 the arrows nudged a number by one and swapped with nothing, and three sections
sorted by date first, so there the arrows changed nothing anybody could see. Nothing held
what a move did beyond its redirect, which is how the nudge went unnoticed. What is held
here: a swap in a dated section and in an undated one, the edges, dense numbers after a
move, where a new entry lands, the seed the migration writes, and the preference that
brings the number box back on each side of the switch.
"""

from __future__ import annotations

import datetime as dt
import importlib

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.resume import ordering
from postulo.resume.models import Certification, Education, Experience, Link, Skill, SkillGroup
from postulo.resume.registry import SECTIONS

pytestmark = pytest.mark.django_db


def job(user, role: str, start: dt.date, **extra) -> Experience:
    return Experience.objects.create(
        owner=user, role=role, organisation="Aperture", start_date=start, **extra
    )


def roles(user) -> list[str]:
    return [e.role for e in Experience.objects.for_user(user)]


def move_url(section: str, pk: int, direction: str) -> str:
    return reverse("resume:item_move", args=[section, pk, direction])


# --------------------------------------------------------------------- the swap


def test_up_and_down_swap_an_entry_with_its_neighbour(client, user):
    first = job(user, "First", dt.date(2020, 1, 1))
    second = job(user, "Second", dt.date(2021, 1, 1))
    third = job(user, "Third", dt.date(2022, 1, 1))
    ordering.renumber([first, second, third])
    client.force_login(user)

    response = client.post(move_url("experience", third.pk, "up"))

    assert response.status_code == 302
    assert roles(user) == ["First", "Third", "Second"]
    client.post(move_url("experience", first.pk, "down"))
    assert roles(user) == ["Third", "First", "Second"]


def test_the_numbers_are_dense_and_exactly_what_the_page_shows_after_a_move(user):
    a = job(user, "A", dt.date(2020, 1, 1), order=3)
    b = job(user, "B", dt.date(2021, 1, 1), order=5)
    c = job(user, "C", dt.date(2022, 1, 1), order=9)

    assert ordering.move(b, "down") is True

    assert [(e.role, e.order) for e in Experience.objects.for_user(user)] == [
        ("A", 0),
        ("C", 1),
        ("B", 2),
    ]
    assert Experience.objects.get(pk=a.pk).order == 0 and Experience.objects.get(pk=c.pk).order == 1


def test_the_edges_are_no_ops_rather_than_wraps(user):
    first = job(user, "First", dt.date(2020, 1, 1))
    last = job(user, "Last", dt.date(2021, 1, 1))
    ordering.renumber([first, last])

    assert ordering.move(first, "up") is False
    assert ordering.move(last, "down") is False
    assert ordering.move(first, "sideways") is False
    assert roles(user) == ["First", "Last"]


def test_an_undated_section_swaps_the_same_way(client, user):
    group = SkillGroup.objects.create(owner=user, name="Languages")
    python = Skill.objects.create(owner=user, group=group, name="Python")
    rust = Skill.objects.create(owner=user, group=group, name="Rust")
    go = Skill.objects.create(owner=user, group=group, name="Go")
    client.force_login(user)

    client.post(move_url("skill", go.pk, "up"))
    client.post(move_url("skill", python.pk, "down"))

    assert [s.name for s in Skill.objects.for_user(user)] == ["Go", "Python", "Rust"]
    assert Skill.objects.get(pk=rust.pk).order == 2


def test_one_person_cannot_move_another_persons_entry(client, user, other_user):
    theirs = job(other_user, "Theirs", dt.date(2020, 1, 1))
    job(other_user, "Also theirs", dt.date(2021, 1, 1))
    client.force_login(user)

    response = client.post(move_url("experience", theirs.pk, "down"))

    assert response.status_code == 404
    assert roles(other_user) == ["Theirs", "Also theirs"]


def test_the_arrows_at_either_end_are_disabled_rather_than_absent(client, user):
    job(user, "Only", dt.date(2020, 1, 1))
    job(user, "Other", dt.date(2021, 1, 1))
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()
    section = html.split('id="section-experience"')[1].split("</section>")[0]
    ups = [b for b in section.split("<button") if "Move up" in b]
    downs = [b for b in section.split("<button") if "Move down" in b]

    assert "disabled" in ups[0] and "disabled" not in ups[1]
    assert "disabled" not in downs[0] and "disabled" in downs[1]


# ------------------------------------------------------------- where a new one goes


def test_a_new_dated_entry_lands_where_its_date_puts_it(client, user):
    """Adding your latest job still puts it first, as the date ordering used to."""
    ordering.renumber(
        [job(user, "Newest", dt.date(2022, 1, 1)), job(user, "Oldest", dt.date(2018, 1, 1))]
    )
    client.force_login(user)

    client.post(
        reverse("resume:item_create", args=["experience"]),
        {"role": "Middle", "organisation": "Black Mesa", "start_date": "2020-06-01"},
    )
    client.post(
        reverse("resume:item_create", args=["experience"]),
        {"role": "Latest", "organisation": "Black Mesa", "start_date": "2024-06-01"},
    )

    assert roles(user) == ["Latest", "Newest", "Middle", "Oldest"]
    assert [e.order for e in Experience.objects.for_user(user)] == [0, 1, 2, 3]


def test_an_ongoing_education_goes_to_the_top_and_a_dated_one_by_its_end(user):
    done = Education.objects.create(
        owner=user, institution="MIT", qualification="BSc", end_date=dt.date(2015, 6, 1)
    )
    ordering.renumber([done])
    later = Education.objects.create(
        owner=user, institution="ETH", qualification="MSc", end_date=dt.date(2019, 6, 1)
    )
    ordering.place_new(later)
    ongoing = Education.objects.create(owner=user, institution="Open", qualification="PhD")
    ordering.place_new(ongoing)

    assert [e.qualification for e in Education.objects.for_user(user)] == ["PhD", "MSc", "BSc"]


def test_a_new_undated_entry_goes_last(client, user):
    Link.objects.create(owner=user, title="Site", url="https://example.org", order=0)
    client.force_login(user)

    client.post(
        reverse("resume:item_create", args=["link"]),
        {"title": "Blog", "url": "https://blog.example.org", "kind": "site", "description": ""},
    )

    assert [link.title for link in Link.objects.for_user(user)] == ["Site", "Blog"]
    assert Link.objects.get(title="Blog").order == 1


def test_a_date_edited_later_moves_nothing(client, user):
    ordering.renumber(
        [job(user, "Top", dt.date(2022, 1, 1)), job(user, "Below", dt.date(2018, 1, 1))]
    )
    below = Experience.objects.get(role="Below")
    client.force_login(user)

    client.post(
        reverse("resume:item_update", args=["experience", below.pk]),
        {"role": "Below", "organisation": "Aperture", "start_date": "2030-01-01"},
    )

    assert roles(user) == ["Top", "Below"], "the order is the person's now, not the date's"


# ---------------------------------------------------------------- the migration


def test_the_seed_is_the_old_ordering_exactly(user, other_user):
    """Ongoing first, then the newest date, then the old number, then the pk -- per person,
    so nothing moves on the day of the upgrade."""
    from django.apps import apps

    migration = importlib.import_module("postulo.resume.migrations.0005_the_order_is_the_persons")
    job(user, "2019", dt.date(2019, 1, 1), order=0)
    job(user, "2021 second", dt.date(2021, 1, 1), order=1)
    job(user, "2021 first", dt.date(2021, 1, 1), order=0)
    job(user, "2023", dt.date(2023, 1, 1), order=7)
    theirs = job(other_user, "Theirs", dt.date(2010, 1, 1), order=4)
    cert = Certification.objects.create(owner=user, name="Old", issued_on=dt.date(2012, 1, 1))
    ongoing = Certification.objects.create(owner=user, name="Undated")
    recent = Certification.objects.create(owner=user, name="Recent", issued_on=dt.date(2020, 1, 1))

    migration.seed_from_dates(apps, None)

    assert [(e.role, e.order) for e in Experience.objects.for_user(user)] == [
        ("2023", 0),
        ("2021 first", 1),
        ("2021 second", 2),
        ("2019", 3),
    ]
    assert Experience.objects.get(pk=theirs.pk).order == 0, "numbered within their own record"
    assert [(c.name, c.order) for c in Certification.objects.for_user(user)] == [
        ("Undated", 0),
        ("Recent", 1),
        ("Old", 2),
    ]
    assert Certification.objects.get(pk=ongoing.pk).order == 0
    assert cert.pk and recent.pk


# --------------------------------------------------------------- the preference


def test_the_order_box_is_hidden_from_every_kind_of_entry_by_default(client, user):
    client.force_login(user)

    for slug in SECTIONS:
        html = client.get(reverse("resume:item_create", args=[slug])).content.decode()
        assert 'name="order"' not in html, slug
        assert "set with the arrows" in html, slug


def test_the_preference_brings_the_number_back_and_keeps_what_is_typed(client, user):
    profile, _made = Profile.objects.get_or_create(user=user)
    profile.show_career_order = True
    profile.save()
    client.force_login(user)

    for slug in SECTIONS:
        html = client.get(reverse("resume:item_create", args=[slug])).content.decode()
        assert 'name="order"' in html, slug
        assert "set with the arrows" not in html, slug

    ordering.renumber([job(user, "Existing", dt.date(2020, 1, 1))])
    client.post(
        reverse("resume:item_create", args=["experience"]),
        {"role": "Typed", "organisation": "Aperture", "start_date": "2025-01-01", "order": "7"},
    )

    assert Experience.objects.get(role="Typed").order == 7, "the number typed is kept exactly"
    assert roles(user) == ["Existing", "Typed"]


def test_the_preference_is_set_under_accessibility_and_travels_with_the_export(client, user):
    """Under *Accessibility* since #281; it was filed under *Appearance* before."""
    from postulo.core.export import build_document

    client.force_login(user)
    response = client.post(reverse("settings:accessibility"), {"show_career_order": "on"})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).show_career_order is True
    assert build_document(user)["account"]["profile"]["show_career_order"] is True

    response = client.post(reverse("settings:accessibility"), {})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).show_career_order is False

    page = client.get(reverse("settings:accessibility")).content.decode()
    assert 'name="show_career_order"' in page and "Your career" in page
    assert (
        'name="show_career_order"'
        not in client.get(reverse("settings:appearance")).content.decode()
    )
