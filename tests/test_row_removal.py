"""One row off *Your details* at once, after a dialog asked (#303).

The page used to be one form with one *Save*, and a row went by ticking its *Remove* box and
saving everything else with it. Now each saved row -- a telephone number, a social profile, a
code repository, a website, a postal address, an identifier -- has a bin that opens a dialog,
and the dialog is a form of its own posting to that row's address. So the questions here are
the ones a new address raises: whose row it is, what happens to the primary, which row may not
go at all, and what the page draws so that a person without a script and a person with one
both get a working question.

The contact form and the company form draw the same rows and keep what they had: the box, and
the removal at the foot of the page.

A row gone at once also makes every other copy of the page out of date -- a second tab, the
one the Back button brings back -- so the last part is what such a copy does when it is saved:
the row it still carries has already been removed, and the rest is saved as typed.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest
from django.test import Client
from django.urls import reverse

from postulo.accounts import removals
from postulo.accounts.models import PersonIdentifier
from postulo.core import phone_numbers
from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress, WebLink
from postulo.plugins.phone_numbers import PHONE_NUMBERS
from postulo.plugins.social_profiles import SOCIAL_PROFILES

from .test_recovery_number import a_gateway, confirmed

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


# --------------------------------------------------------------------------- the rows


def a_number(user, digits="+351912345678", *, holder=None, primary=False) -> PhoneNumber:
    return PhoneNumber.objects.create(
        owner=user, holder=holder or user.profile, number=digits, is_primary=primary
    )


def a_link(user, url="https://example.org/me", *, kind=WebLink.Kind.SOCIAL, primary=False):
    return WebLink.objects.create(
        owner=user, holder=user.profile, kind=kind, url=url, is_primary=primary
    )


def a_handle(user, handle="@alex:example.org", *, primary=False) -> MessagingHandle:
    return MessagingHandle.objects.create(
        owner=user, holder=user.profile, service="matrix", handle=handle, is_primary=primary
    )


def an_address(user, street="Rua do Exemplo 1", *, primary=False) -> PostalAddress:
    return PostalAddress.objects.create(
        owner=user,
        holder=user.profile,
        street=street,
        postcode="1000-001",
        municipality="Lisboa",
        country="PT",
        is_primary=primary,
    )


def an_identifier(user, scheme="wikidata", value="Q95") -> PersonIdentifier:
    return PersonIdentifier.objects.create(profile=user.profile, scheme=scheme, value=value)


#: Every kind of row, with how to make one and the block it is sent back to.
KINDS = {
    "remove_number": (a_number, "section-phones"),
    "remove_link": (a_link, "section-links-social"),
    "remove_messaging": (a_handle, "section-messaging"),
    "remove_address": (an_address, "section-addresses"),
    "remove_identifier": (an_identifier, "section-identifiers"),
}


def remove(client, name, row, **extra):
    return client.post(reverse(f"accounts:{name}", args=[row.pk]), **extra)


def contact(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


def switch_off(person, plugin):
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(plugin=plugin, person=person, state=PluginPolicy.State.FORCED_OFF)


# ------------------------------------------------------------------- whose row it is


@pytest.mark.parametrize("name", KINDS)
def test_the_owner_removes_a_row_and_is_sent_back_to_its_block(client, user, name):
    make, anchor = KINDS[name]
    row = make(user)
    client.force_login(user)

    response = remove(client, name, row)

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('accounts:profile')}#{anchor}"
    assert not type(row).objects.filter(pk=row.pk).exists()
    said = [str(message) for message in response.wsgi_request._messages]
    assert len(said) == 1 and said[0].endswith("removed."), said


def test_the_sentence_keeps_the_value_to_itself(client, user):
    """The sentence is the reader's language and the value is what somebody typed, so the
    value sits between FIRST STRONG ISOLATE and POP DIRECTIONAL ISOLATE: in a right-to-left
    sentence a number's groups are otherwise laid out last group first. Both ways of hearing
    it -- the script's answer, and the message of a page drawn again -- carry the same."""
    first, second = a_number(user, "+351912345671"), a_number(user, "+351912345672")
    client.force_login(user)

    answer = json.loads(remove(client, "remove_number", first, **HTMX).content)
    response = remove(client, "remove_number", second)

    assert answer["said"] == "\u2068+351 912 345 671\u2069 removed."
    [message] = [str(message) for message in response.wsgi_request._messages]
    assert message == "\u2068+351 912 345 672\u2069 removed."


@pytest.mark.parametrize("name", KINDS)
def test_with_htmx_the_answer_is_for_the_script(client, user, name):
    """No redirect and no message: the page stays where it is, and the script is told what
    to say and how many rows the block has left."""
    make, _anchor = KINDS[name]
    row = make(user)
    client.force_login(user)

    response = remove(client, name, row, **HTMX)

    assert response.status_code == 200
    answer = json.loads(response.content)
    assert answer["removed"] is True
    assert answer["said"].endswith("removed.")
    assert answer["count"] == 0
    assert not list(response.wsgi_request._messages), "a message would wait for the next page"


@pytest.mark.parametrize("name", KINDS)
def test_somebody_elses_row_is_not_found(client, user, other_user, name):
    make, _anchor = KINDS[name]
    theirs = make(other_user)
    client.force_login(user)

    assert remove(client, name, theirs).status_code == 404
    assert remove(client, name, theirs, **HTMX).status_code == 404
    assert type(theirs).objects.filter(pk=theirs.pk).exists()


@pytest.mark.parametrize("name", KINDS)
def test_nobody_signed_in_is_sent_to_sign_in(client, user, name):
    make, _anchor = KINDS[name]
    row = make(user)

    response = remove(client, name, row)

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]
    assert type(row).objects.filter(pk=row.pk).exists()


@pytest.mark.parametrize("name", KINDS)
def test_a_link_cannot_remove_anything(client, user, name):
    """A GET is not a removal: a prefetched or followed link must never take a row off. It
    leads back to the row's block, because it is where somebody lands who confirmed after
    their session had ended and then signed in again: sign-in sends them on to the address
    that refused them, and that used to be a page with nothing on it."""
    make, anchor = KINDS[name]
    row = make(user)
    client.force_login(user)

    response = client.get(reverse(f"accounts:{name}", args=[row.pk]))

    assert response.status_code == 302
    assert response["Location"] == f"{reverse('accounts:profile')}#{anchor}"
    assert type(row).objects.filter(pk=row.pk).exists()
    assert not list(response.wsgi_request._messages)


@pytest.mark.parametrize("name", KINDS)
def test_a_get_says_no_more_about_somebody_elses_row_than_a_post(client, user, other_user, name):
    """Looked up before anything is answered, so a row that is not this person's -- or not
    there at all -- is a 404 to a GET as well, and never a redirect that confirms it."""
    make, _anchor = KINDS[name]
    theirs = make(other_user)
    client.force_login(user)

    assert client.get(reverse(f"accounts:{name}", args=[theirs.pk])).status_code == 404
    assert client.get(reverse(f"accounts:{name}", args=[theirs.pk + 1000])).status_code == 404


def test_a_get_for_a_block_that_is_switched_off_is_not_found(client, user):
    number = a_number(user, primary=True)
    switch_off(user, PHONE_NUMBERS)
    client.force_login(user)

    assert client.get(reverse("accounts:remove_number", args=[number.pk])).status_code == 404


@pytest.mark.parametrize("name", KINDS)
def test_a_forged_request_is_refused(user, name):
    make, _anchor = KINDS[name]
    row = make(user)
    forged = Client(enforce_csrf_checks=True)
    forged.force_login(user)

    assert remove(forged, name, row).status_code == 403
    assert type(row).objects.filter(pk=row.pk).exists()


def a_contacts_row(user, name):
    """A row of this kind that this account owns and a contact holds."""
    someone = contact(user)
    if name == "remove_number":
        return a_number(user, holder=someone)
    if name == "remove_link":
        return WebLink.objects.create(
            owner=user, holder=someone, kind=WebLink.Kind.SOCIAL, url="https://example.org/them"
        )
    return PostalAddress.objects.create(
        owner=user, holder=someone, street="Rua do Contacto 3", country="PT"
    )


@pytest.mark.parametrize("name", ["remove_number", "remove_link", "remove_address"])
def test_a_contacts_row_is_not_one_of_your_details(client, user, name):
    """This account owns it, and it is removed on the contact's page, not here. Every kind
    a contact can hold: looking a row up by its owner alone would pass every other test in
    this file and take a contact's link or address off through the profile's address."""
    theirs = a_contacts_row(user, name)
    client.force_login(user)

    assert remove(client, name, theirs).status_code == 404
    assert remove(client, name, theirs, **HTMX).status_code == 404
    assert client.get(reverse(f"accounts:{name}", args=[theirs.pk])).status_code == 404
    assert type(theirs).objects.filter(pk=theirs.pk).exists()


def test_a_block_that_is_switched_off_removes_nothing(client, user):
    """Switching a feature off keeps its rows out of sight and untouched; an address that
    reached one of them would be the switch deleting something after all."""
    number = a_number(user, primary=True)
    link = a_link(user, primary=True)
    switch_off(user, PHONE_NUMBERS)
    switch_off(user, SOCIAL_PROFILES)
    client.force_login(user)

    assert remove(client, "remove_number", number).status_code == 404
    assert remove(client, "remove_link", link).status_code == 404
    assert PhoneNumber.objects.filter(pk=number.pk).exists()
    assert WebLink.objects.filter(pk=link.pk).exists()


# --------------------------------------------------------------------- the primary


def test_removing_the_primary_number_hands_it_on_as_saving_does(client, user):
    gone = a_number(user, "+351912345670", primary=True)
    older = a_number(user, "+351912345671")
    a_number(user, "+351912345672")
    client.force_login(user)

    answer = json.loads(remove(client, "remove_number", gone, **HTMX).content)

    older.refresh_from_db()
    assert older.is_primary, "the next one in the list becomes the primary"
    assert answer["primary"] == older.pk
    assert answer["count"] == 2
    assert PhoneNumber.objects.filter(owner=user, is_primary=True).count() == 1


def test_removing_another_number_leaves_the_primary_where_it_is(client, user):
    kept = a_number(user, "+351912345670", primary=True)
    gone = a_number(user, "+351912345671")
    client.force_login(user)

    answer = json.loads(remove(client, "remove_number", gone, **HTMX).content)

    kept.refresh_from_db()
    assert kept.is_primary and answer["primary"] == kept.pk


def test_removing_the_last_number_leaves_no_primary(client, user):
    only = a_number(user, primary=True)
    client.force_login(user)

    answer = json.loads(remove(client, "remove_number", only, **HTMX).content)

    assert answer["primary"] is None and answer["count"] == 0


def test_a_links_primary_is_handed_on_within_its_own_kind(client, user):
    """One primary per kind: a website does not inherit a social profile's."""
    gone = a_link(user, "https://example.org/a", primary=True)
    heir = a_link(user, "https://example.org/b")
    website = a_link(user, "https://example.org/site", kind=WebLink.Kind.WEBSITE, primary=True)
    client.force_login(user)

    answer = json.loads(remove(client, "remove_link", gone, **HTMX).content)

    heir.refresh_from_db()
    website.refresh_from_db()
    assert heir.is_primary and answer["primary"] == heir.pk
    assert website.is_primary
    assert answer["count"] == 1, "the count is the social profiles', not every link's"


def test_removing_the_primary_address_hands_it_on(client, user):
    gone = an_address(user, "Rua A 1", primary=True)
    heir = an_address(user, "Rua B 2")
    client.force_login(user)

    answer = json.loads(remove(client, "remove_address", gone, **HTMX).content)

    heir.refresh_from_db()
    assert heir.is_primary and answer["primary"] == heir.pk


def test_the_rule_is_the_one_saving_follows(user):
    """Not a second rule that happens to agree: the formset's save and the removal call the
    same function, so the two ways of losing a primary cannot drift apart."""
    a_number(user, "+351912345670")
    second = a_number(user, "+351912345671")
    PhoneNumber.objects.filter(owner=user).update(is_primary=False)
    second.delete()

    heir = phone_numbers.ensure_one_primary(user.profile)

    assert heir is not None and heir.is_primary
    assert phone_numbers.primary_for(user.profile).pk == heir.pk


# ------------------------------------------------------------- the way back in (#144)


def test_the_number_that_gets_you_back_in_is_refused_with_the_reason(client, user):
    with a_gateway():
        way_in = confirmed(user, "+351912345670")
        phone_numbers.set_recovery(way_in, owner=user)
        client.force_login(user)

        response = remove(client, "remove_number", way_in)

    assert response.status_code == 302
    assert PhoneNumber.objects.filter(pk=way_in.pk).exists()
    said = [str(message) for message in response.wsgi_request._messages]
    assert said and "Choose another number" in said[0]
    # To the top of the page, where the reason is drawn, and not to the row's block: there
    # the message is a screen above what is in view, and the block looks untouched.
    assert response["Location"] == reverse("accounts:profile")


def test_with_htmx_the_refusal_is_said_in_the_dialog(client, user):
    with a_gateway():
        way_in = confirmed(user, "+351912345670")
        phone_numbers.set_recovery(way_in, owner=user)
        client.force_login(user)

        answer = json.loads(remove(client, "remove_number", way_in, **HTMX).content)

    assert answer["removed"] is False
    assert "get you back in" in answer["said"]
    assert PhoneNumber.objects.filter(pk=way_in.pk).exists()


def test_a_chosen_number_with_no_gateway_is_no_way_in_and_may_go(client, user):
    """Nothing can reach it, and the page offers no way to choose another: refusing would
    keep it for ever."""
    with a_gateway():
        chosen = confirmed(user, "+351912345670")
        phone_numbers.set_recovery(chosen, owner=user)
    client.force_login(user)

    assert remove(client, "remove_number", chosen).status_code == 302
    assert not PhoneNumber.objects.filter(pk=chosen.pk).exists()


# ------------------------------------------------------------------ what the page draws


def profile_html(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("accounts:profile")).content.decode()


def test_every_saved_row_has_a_bin_named_by_its_value(client, user):
    number = a_number(user, primary=True)
    link = a_link(user, primary=True)
    address = an_address(user, primary=True)
    identifier = an_identifier(user)

    html = profile_html(client, user)

    for kind, row, words in (
        ("number", number, "Remove +351 912 345 678"),
        ("link", link, "Remove example.org/me"),
        ("address", address, "Remove Rua do Exemplo 1, 1000-001, Lisboa, Portugal"),
        ("identifier", identifier, "Remove Wikidata Q95"),
    ):
        button = re.search(
            rf'<button type="button"[^>]*popovertarget="remove-{kind}-{row.pk}"[^>]*>', html
        )
        assert button, f"no bin for the {kind}"
        assert f'aria-label="{words}"' in button.group(0)
        assert 'data-variant="destructive-ghost"' in button.group(0)
        assert 'data-size="icon-xs"' in button.group(0), "the 24-pixel square"
        assert 'aria-haspopup="dialog"' in button.group(0)


def test_two_links_nobody_named_are_told_apart_by_their_addresses(client, user):
    """A link with no name used to be called by its host, as a document prints it. Two
    repositories on one forge then had the same bin, the same dialog and the same sentence
    afterwards, and the open dialog covers the one box that tells them apart. So it is the
    address, less the scheme; a link somebody named keeps its name."""
    kind = WebLink.Kind.REPOSITORY
    first = a_link(user, "https://codeberg.org/alex/postulo", kind=kind, primary=True)
    second = a_link(user, "https://codeberg.org/alex/dotfiles", kind=kind)
    named = WebLink.objects.create(
        owner=user, holder=user.profile, kind=kind, url="https://codeberg.org/alex", label="Forge"
    )

    html = profile_html(client, user)

    for row, words in (
        (first, "codeberg.org/alex/postulo"),
        (second, "codeberg.org/alex/dotfiles"),
        (named, "Forge"),
    ):
        assert removals.words(removals.LINK, row) == words
        button = re.search(
            rf'<button type="button"[^>]*popovertarget="remove-link-{row.pk}"[^>]*>', html
        )
        assert f'aria-label="Remove {words}"' in button.group(0)
        assert f'<h2 id="remove-link-{row.pk}-title">Remove <bdi>{words}</bdi>?</h2>' in html
    answer = json.loads(remove(client, "remove_link", second, **HTMX).content)
    assert answer["said"] == "\u2068codeberg.org/alex/dotfiles\u2069 removed."


def test_each_dialog_carries_the_sentence_for_a_row_that_has_gone_already(client, user):
    """A copy of the page that is out of date still draws a row taken off somewhere else,
    and its address answers 404. The script takes the row off that copy too and says so,
    with a sentence the server wrote: the value isolated, and escaped like any other."""
    number = a_number(user, primary=True)
    link = WebLink.objects.create(
        owner=user,
        holder=user.profile,
        kind=WebLink.Kind.SOCIAL,
        url="https://example.org/x",
        label='"><i id=nasty>',
    )

    html = profile_html(client, user)

    dialog = re.search(rf'<dialog popover id="remove-number-{number.pk}"[^>]*>', html).group(0)
    assert 'data-gone-said="\u2068+351 912 345 678\u2069 had already been removed."' in dialog
    assert "<i id=nasty>" not in html, "the name reached the page as markup"
    dialog = re.search(rf'<dialog popover id="remove-link-{link.pk}"[^>]*>', html).group(0)
    assert (
        'data-gone-said="\u2068&quot;&gt;&lt;i id=nasty&gt;\u2069 had already been removed."'
        in dialog
    )


def test_each_bin_opens_a_dialog_that_posts_to_its_row(client, user):
    number = a_number(user, primary=True)
    a_number(user, "+351912345679")

    html = profile_html(client, user)

    dialog = html[html.index(f'<dialog popover id="remove-number-{number.pk}"') :]
    dialog = dialog[: dialog.index("</dialog>")]
    action = reverse("accounts:remove_number", args=[number.pk])
    assert 'role="alertdialog"' in dialog
    assert f'aria-labelledby="remove-number-{number.pk}-title"' in dialog
    assert f'aria-describedby="remove-number-{number.pk}-description"' in dialog
    assert f'<form method="post" action="{action}" hx-post="{action}"' in dialog
    assert "csrfmiddlewaretoken" in dialog
    assert "<bdi>+351 912 345 678</bdi>" in dialog
    assert "<span data-hands-on>The next one in the list becomes the primary.</span>" in dialog
    assert "<noscript>" in dialog, "with scripts off, the sentence says what is lost"
    assert 'popovertargetaction="hide"' in dialog and "autofocus" in dialog
    # The dialogs are outside the page's form: a form cannot hold another.
    assert html.index(f'id="remove-number-{number.pk}"') > html.index("</form>")


def test_a_dialog_says_nothing_about_the_primary_when_it_stays(client, user):
    """The sentence is in the dialog of every row of a block that has a primary, hidden on
    all but the row it is true of, so that the script can move it when a row goes without a
    reload. Hidden, it is neither read nor part of the dialog's description."""
    a_number(user, primary=True)
    other = a_number(user, "+351912345679")

    html = profile_html(client, user)

    dialog = html[html.index(f'<dialog popover id="remove-number-{other.pk}"') :]
    dialog = dialog[: dialog.index("</dialog>")]
    assert "<span data-hands-on hidden>The next one in the list" in dialog
    assert "<span data-hands-on>" not in dialog


def test_the_only_row_hands_the_primary_to_nobody(client, user):
    only = a_number(user, primary=True)

    html = profile_html(client, user)

    dialog = html[html.index(f'<dialog popover id="remove-number-{only.pk}"') :]
    dialog = dialog[: dialog.index("</dialog>")]
    assert "<span data-hands-on hidden>" in dialog


def test_an_identifiers_dialog_never_mentions_a_primary(client, user):
    """Identifiers have none, so the sentence is not there to show."""
    identifier = an_identifier(user)

    html = profile_html(client, user)

    dialog = html[html.index(f'<dialog popover id="remove-identifier-{identifier.pk}"') :]
    dialog = dialog[: dialog.index("</dialog>")]
    assert "data-hands-on" not in dialog and "becomes the primary" not in dialog


def test_primary_is_a_star_and_the_boxes_are_gone(client, user):
    number = a_number(user, primary=True)
    a_link(user, primary=True)

    html = profile_html(client, user)

    assert 'name="phone_numbers-0-DELETE"' not in html
    assert 'name="social_profiles-0-DELETE"' not in html
    stars = re.findall(r'<label class="primary-choice"[^>]*>.*?</label>', html, re.DOTALL)
    assert stars, "no star"
    chosen = [star for star in stars if "checked" in star]
    assert any('value="phone_numbers-0"' in star for star in chosen)
    for star in stars:
        assert 'type="radio" class="sr-only"' in star, "still a radio, for the keyboard"
        assert '<span class="sr-only">Primary</span>' in star, "and still named"
        assert 'data-icon="star"' in star
    assert f"remove-number-{number.pk}" in html


def test_the_contact_form_keeps_its_boxes(client, user):
    """The same partials, and the contact form does what it always did (#303 leaves it)."""
    someone = contact(user)
    a_number(user, holder=someone, primary=True)
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[someone.pk])).content.decode()

    assert 'name="phone_numbers-0-DELETE"' in html
    assert "primary-choice" not in html
    assert "data-remove-trigger" not in html
    assert "<dialog" not in html.replace('<dialog popover id="leave-dialog"', "")


def test_the_company_form_keeps_its_boxes(client, user):
    from postulo.jobs.models import Company, CompanyIdentifier

    company = Company.objects.create(owner=user, name="Aperture")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value="Q95")
    client.force_login(user)

    html = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    assert 'name="identifiers-0-DELETE"' in html
    assert "data-remove-trigger" not in html


def test_a_row_removed_at_once_does_not_trip_the_next_save(client, user):
    """The page's formset counted the row when it was drawn. What the script leaves behind
    -- its key and its removal -- is passed over by the save, as Django passes over any row
    already gone, and the rest of the page is saved as typed."""
    gone = a_number(user, "+351912345670", primary=True)
    kept = a_number(user, "+351912345671")
    client.force_login(user)
    remove(client, "remove_number", gone, **HTMX)

    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alex",
            "last_name": "Morgan",
            "headline": "Changed while the row went",
            "location": "",
            "phone_numbers-TOTAL_FORMS": "2",
            "phone_numbers-INITIAL_FORMS": "2",
            "phone_numbers-MIN_NUM_FORMS": "0",
            "phone_numbers-MAX_NUM_FORMS": "1000",
            "phone_numbers-0-id": str(gone.pk),
            "phone_numbers-0-DELETE": "on",
            "phone_numbers-1-id": str(kept.pk),
            "phone_numbers-1-kind": "",
            "phone_numbers-1-label": "",
            "phone_numbers-1-number_0": "PT",
            "phone_numbers-1-number_1": "+351912345671",
            "phone_numbers-primary": "phone_numbers-1",
        },
    )

    assert response.status_code == 302, response.content.decode()[:2000]
    user.profile.refresh_from_db()
    assert user.profile.headline == "Changed while the row went"
    assert list(PhoneNumber.objects.filter(owner=user)) == [kept]


class WhatTheFormHolds(HTMLParser):
    """Every named control of the page's own form, with the value a browser would post.

    The page has several forms -- the theme switch, each row's dialog -- so the one wanted
    is the one that holds the name. `hidden` is which of its fields are hidden inputs: what
    a row leaves behind when the script takes it off.
    """

    def __init__(self):
        super().__init__()
        self.forms: list[dict[str, str]] = []
        self.hidden: set[str] = set()
        self.inside = False
        self.select: str | None = None
        self.chosen = False
        self.textarea: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.forms.append({})
            self.inside = True
            return
        if not self.inside:
            return
        name = attrs.get("name")
        if tag == "input" and name:
            kind = attrs.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in attrs:
                    self.forms[-1][name] = attrs.get("value", "on")
            elif kind not in ("file", "submit", "button"):
                self.forms[-1][name] = attrs.get("value") or ""
                if kind == "hidden":
                    self.hidden.add(name)
        elif tag == "textarea" and name:
            self.textarea = name
            self.forms[-1][name] = ""
        elif tag == "select" and name:
            self.select, self.chosen = name, False
        elif tag == "option" and self.select:
            if "selected" in attrs or (not self.chosen and self.select not in self.forms[-1]):
                self.forms[-1][self.select] = attrs.get("value") or ""
                self.chosen = self.chosen or "selected" in attrs

    def handle_data(self, data):
        if self.textarea:
            self.forms[-1][self.textarea] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select":
            self.select = None
        elif tag == "textarea" and self.textarea:
            # A browser drops the one newline Django writes after the opening tag.
            held = self.forms[-1][self.textarea]
            self.forms[-1][self.textarea] = held.removeprefix("\r").removeprefix("\n")
            self.textarea = None


def after_the_script_took_off(html: str, row) -> dict[str, str]:
    """What *Save* posts once `app.js` has taken ``row`` off the page: every other field as
    drawn, and of the row only its hidden fields and its removal -- the key it leaves."""
    page = WhatTheFormHolds()
    page.feed(html)
    [data] = [form for form in page.forms if "first_name" in form]
    [key] = [name for name, value in data.items() if name.endswith("-id") and value == str(row.pk)]
    prefix = key.removesuffix("-id")
    block = prefix.rsplit("-", 1)[0]
    left = {
        name: value
        for name, value in data.items()
        if not name.startswith(f"{prefix}-") or name in page.hidden
    }
    left[f"{prefix}-DELETE"] = "on"
    if left.get(f"{block}-primary") == prefix:
        del left[f"{block}-primary"]  # the star went with the row
    return left


#: What somebody typed into a row of each kind, which a save of the page as drawn must not
#: change.
WHAT_A_ROW_HOLDS = {
    "remove_number": [PhoneNumber._meta.get_field(name) for name in ("number", "kind", "label")],
    "remove_link": [WebLink._meta.get_field(name) for name in ("url", "label")],
    "remove_address": [
        PostalAddress._meta.get_field(name)
        for name in ("street", "postcode", "municipality", "region", "country", "kind", "label")
    ],
    "remove_identifier": [
        PersonIdentifier._meta.get_field(name) for name in ("scheme", "value", "label")
    ],
    "remove_messaging": [
        MessagingHandle._meta.get_field(name) for name in ("service", "handle", "label")
    ],
}


def two_rows(user, name):
    """Two rows of a kind, the first the primary where the kind has one: gone, and kept."""
    make, _anchor = KINDS[name]
    if name == "remove_identifier":
        return make(user, "wikidata", "Q95"), make(user, "linkedin", "alex-morgan")
    if name == "remove_number":
        return make(user, "+351912345670", primary=True), make(user, "+351912345671")
    if name == "remove_link":
        return make(user, "https://example.org/a", primary=True), make(
            user, "https://example.org/b"
        )
    if name == "remove_messaging":
        return make(user, "@a:example.org", primary=True), make(user, "@b:example.org")
    return make(user, "Rua A 1", primary=True), make(user, "Rua B 2")


@pytest.mark.parametrize("name", KINDS)
def test_the_page_saves_after_any_kind_of_row_went_at_once(client, user, name):
    """Each block is a formset of its own, with its own rules about duplicates and
    primaries, and each has to pass over the key a removed row leaves. The form is read
    from the page as it was drawn before the row went, which is the page the browser has."""
    gone, kept = two_rows(user, name)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert json.loads(remove(client, name, gone, **HTMX).content)["removed"] is True

    posted = after_the_script_took_off(html, gone)
    posted.update(first_name="Alex", last_name="Morgan", headline="Saved after the row went")
    response = client.post(reverse("accounts:profile"), posted)

    assert response.status_code == 302, re.findall(
        r'role="alert"[^>]*>(.*?)</', response.content.decode(), re.DOTALL
    )
    user.profile.refresh_from_db()
    assert user.profile.headline == "Saved after the row went"
    kind = getattr(removals, name.removeprefix("remove_").upper())
    assert list(removals.rows(kind, user)) == [kept]
    was = {field.name: getattr(kept, field.name) for field in WHAT_A_ROW_HOLDS[name]}
    kept.refresh_from_db()
    if kind.has_primary:
        assert kept.is_primary, "the row that inherited the primary keeps it through the save"
    now = {field.name: getattr(kept, field.name) for field in WHAT_A_ROW_HOLDS[name]}
    assert now == was, "the row that stayed was saved as something other than what it held"


def test_what_the_form_holds_includes_what_a_text_box_of_several_lines_holds(client, user):
    """The street is a `<textarea>`, whose value is its text and not an attribute. Read as
    empty, the test above saved the kept address with no street and passed."""
    an_address(user, "Rua A 1\nSegundo andar", primary=True)
    client.force_login(user)

    page = WhatTheFormHolds()
    page.feed(client.get(reverse("accounts:profile")).content.decode())
    [data] = [form for form in page.forms if "first_name" in form]

    assert data["addresses-0-street"] == "Rua A 1\nSegundo andar"
    assert data["addresses-1-street"] == "", "the empty row for a new address"


def test_a_refused_save_draws_the_removed_row_back_as_its_key(client, user):
    """And when that save is refused for something else, the page it sends back keeps the
    key hidden rather than drawing an empty row nothing answers to."""
    gone = an_identifier(user, "wikidata", "Q95")
    kept = an_identifier(user, "linkedin", "alex-morgan")
    client.force_login(user)
    remove(client, "remove_identifier", gone, **HTMX)

    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alex",
            "last_name": "Morgan",
            "headline": "",
            "location": "",
            "identifiers-TOTAL_FORMS": "3",
            "identifiers-INITIAL_FORMS": "2",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "identifiers-0-id": str(gone.pk),
            "identifiers-0-profile": str(user.profile.pk),
            "identifiers-0-DELETE": "on",
            "identifiers-1-id": str(kept.pk),
            "identifiers-1-profile": str(user.profile.pk),
            "identifiers-1-scheme": "linkedin",
            "identifiers-1-value": "alex-morgan",
            "identifiers-1-label": "",
            # A new row with no value: refused, so the page comes back.
            "identifiers-2-profile": str(user.profile.pk),
            "identifiers-2-scheme": "orcid",
            "identifiers-2-value": "",
            "identifiers-2-label": "",
        },
    )

    assert response.status_code == 200
    html = response.content.decode()
    ghost = re.search(r"<li hidden data-removed>(.*?)</li>", html, re.DOTALL)
    assert ghost, "the removed row is not kept as its key"
    assert f'name="identifiers-0-id" value="{gone.pk}"' in ghost.group(1)
    assert 'name="identifiers-0-DELETE" value="on"' in ghost.group(1)
    assert "remove-identifier-" + str(gone.pk) not in html, "and it is offered nothing"
    # The sidebar counts the rows that are kept, not the ones the form still carries.
    nav = html[html.index('data-section-link="section-identifiers"') :]
    assert '<span class="badge ms-auto" data-tone="grey">1</span>' in nav[: nav.index("</a>")]


# ------------------------------------------------- a copy of the page that is out of date


def as_drawn(html: str) -> dict[str, str]:
    """What *Save* posts from the page exactly as it was drawn, nothing taken off it."""
    page = WhatTheFormHolds()
    page.feed(html)
    [data] = [form for form in page.forms if "first_name" in form]
    return data


def alerts_in(response) -> list[str]:
    found = re.findall(r'role="alert"[^>]*>(.*?)</(?:div|p)>', response.content.decode(), re.DOTALL)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", alert)).strip() for alert in found]


@pytest.mark.parametrize("name", KINDS)
def test_a_copy_of_the_page_that_is_out_of_date_still_saves(client, user, name):
    """Two tabs, or the page the Back button brings back: the row went at its own address,
    and this copy of the page still carries it, key and boxes, with nothing saying it is to
    go. Django refuses a key that names no row -- on a hidden field no template draws, so
    the page came back with no message, as often as it was sent, and what was typed on it
    was never saved. The row has already been removed, and the save says as much: it passes
    over it and keeps the rest."""
    gone, kept = two_rows(user, name)
    client.force_login(user)
    stale = as_drawn(client.get(reverse("accounts:profile")).content.decode())
    assert json.loads(remove(client, name, gone, **HTMX).content)["removed"] is True

    stale.update(first_name="Alex", last_name="Morgan", headline="Typed in the other tab")
    response = client.post(reverse("accounts:profile"), stale)

    assert response.status_code == 302, alerts_in(response)
    user.profile.refresh_from_db()
    assert user.profile.headline == "Typed in the other tab"
    kind = getattr(removals, name.removeprefix("remove_").upper())
    assert list(removals.rows(kind, user)) == [kept], "the row came back, or took another with it"
    if kind.has_primary:
        kept.refresh_from_db()
        assert kept.is_primary, "the star this copy still had on the row that went is nobody's"


def test_a_save_refused_for_something_else_draws_the_row_that_went_as_its_key(client, user):
    """The copy is out of date *and* holds something the save refuses. The page that comes
    back draws the row that went as the hidden key it left, with no boxes, no bin and no
    dialog -- and saving it again, put right, goes through."""
    gone, kept = two_rows(user, "remove_number")
    client.force_login(user)
    stale = as_drawn(client.get(reverse("accounts:profile")).content.decode())
    remove(client, "remove_number", gone, **HTMX)

    stale.update(first_name="Alex", last_name="x" * 500)
    response = client.post(reverse("accounts:profile"), stale)

    assert response.status_code == 200
    html = response.content.decode()
    ghost = re.search(r"<li hidden data-removed>(.*?)</li>", html, re.DOTALL)
    assert ghost, "the row that went is drawn as a row"
    assert f'name="phone_numbers-0-id" value="{gone.pk}"' in ghost.group(1)
    assert 'name="phone_numbers-0-DELETE" value="on"' in ghost.group(1)
    assert 'name="phone_numbers-0-number_1"' not in html, "its boxes are drawn"
    assert f"remove-number-{gone.pk}" not in html, "it is offered a bin"
    assert f"remove-number-{kept.pk}" in html
    nav = html[html.index('data-section-link="section-phones"') :]
    assert '<span class="badge ms-auto" data-tone="grey">1</span>' in nav[: nav.index("</a>")]

    again = as_drawn(html)
    again.update(last_name="Morgan", headline="Put right")
    assert client.post(reverse("accounts:profile"), again).status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.headline == "Put right"
    assert list(PhoneNumber.objects.filter(owner=user)) == [kept]


def test_what_was_typed_into_a_row_that_went_is_not_saved_as_a_new_one(client, user):
    """Passed over means passed over: the copy's boxes for the row that went are not read
    as a row to add, whatever was typed into them since."""
    gone, kept = two_rows(user, "remove_link")
    client.force_login(user)
    stale = as_drawn(client.get(reverse("accounts:profile")).content.decode())
    remove(client, "remove_link", gone, **HTMX)

    stale.update(first_name="Alex", last_name="Morgan")
    stale["social_profiles-0-url"] = "https://example.org/typed-into-the-row-that-went"
    response = client.post(reverse("accounts:profile"), stale)

    assert response.status_code == 302, alerts_in(response)
    assert list(WebLink.objects.filter(owner=user).values_list("url", flat=True)) == [kept.url]


def test_a_kind_freed_by_a_row_that_went_can_be_taken_in_the_same_save(client, user):
    """One identifier of each kind (#307), and the row that went holds none: the copy that
    still carries a Wikidata row can add another Wikidata identifier and save."""
    gone, _kept = two_rows(user, "remove_identifier")
    client.force_login(user)
    stale = as_drawn(client.get(reverse("accounts:profile")).content.decode())
    remove(client, "remove_identifier", gone, **HTMX)

    stale.update(first_name="Alex", last_name="Morgan")
    stale.update({"identifiers-2-scheme": "wikidata", "identifiers-2-value": "Q42"})
    response = client.post(reverse("accounts:profile"), stale)

    assert response.status_code == 302, alerts_in(response)
    held = PersonIdentifier.objects.filter(profile=user.profile).values_list("scheme", "value")
    assert sorted(held) == [("linkedin", "alex-morgan"), ("wikidata", "Q42")]


def test_a_key_that_names_somebody_elses_row_is_passed_over_too(client, user, other_user):
    """A key the page never drew names no row of this holder, exactly as a row that went
    does, and gets exactly that: nothing is read from it and nothing is written. Somebody
    else's number, a contact's, and somebody else's identifier, each posted as a row of
    *Your details* with its removal ticked and something typed over it."""
    theirs = a_number(other_user, "+351912345600", primary=True)
    a_contacts = a_number(user, "+351912345601", holder=contact(user), primary=True)
    their_identifier = an_identifier(other_user, "wikidata", "Q1")
    client.force_login(user)
    posted = as_drawn(client.get(reverse("accounts:profile")).content.decode())

    posted.update(first_name="Alex", last_name="Morgan")
    posted.update(
        {
            "phone_numbers-TOTAL_FORMS": "3",
            "phone_numbers-INITIAL_FORMS": "2",
            "phone_numbers-0-id": str(theirs.pk),
            "phone_numbers-0-number_0": "PT",
            "phone_numbers-0-number_1": "912000000",
            "phone_numbers-1-id": str(a_contacts.pk),
            "phone_numbers-1-DELETE": "on",
            "identifiers-TOTAL_FORMS": "2",
            "identifiers-INITIAL_FORMS": "1",
            "identifiers-0-id": str(their_identifier.pk),
            "identifiers-0-profile": str(user.profile.pk),
            "identifiers-0-scheme": "wikidata",
            "identifiers-0-value": "Q2",
            "identifiers-0-DELETE": "on",
        }
    )
    response = client.post(reverse("accounts:profile"), posted)

    assert response.status_code == 302, alerts_in(response)
    for row, number in ((theirs, "+351912345600"), (a_contacts, "+351912345601")):
        row.refresh_from_db()
        assert row.number == number and row.is_primary
    their_identifier.refresh_from_db()
    assert their_identifier.value == "Q1"
    assert not removals.rows(removals.NUMBER, user).exists()
    assert not removals.rows(removals.IDENTIFIER, user).exists()


def test_a_contacts_form_that_is_out_of_date_still_saves(client, user):
    """The contact form draws the same rows from the same formsets, and keeps its *Remove*
    box. A number removed from one copy of it is passed over by the other."""
    someone = contact(user)
    gone = a_number(user, "+351912345670", holder=someone, primary=True)
    kept = a_number(user, "+351912345671", holder=someone)
    client.force_login(user)
    url = reverse("jobs:contact_update", args=[someone.pk])
    page = WhatTheFormHolds()
    page.feed(client.get(url).content.decode())
    [stale] = [form for form in page.forms if "phone_numbers-TOTAL_FORMS" in form]
    gone.delete()

    stale["role"] = "Typed in the other tab"
    response = client.post(url, stale)

    assert response.status_code == 302, alerts_in(response)
    someone.refresh_from_db()
    assert someone.role == "Typed in the other tab"
    kept.refresh_from_db()
    assert list(PhoneNumber.objects.filter(owner=user)) == [kept] and kept.is_primary


def test_the_words_and_the_address_are_one_source(user):
    """The bin in the row and the dialog after the form are drawn from one `Removal`, so the
    `popovertarget` cannot name a dialog that is not there."""
    number = a_number(user)

    offered = removals.Removal(
        kind=removals.NUMBER, pk=number.pk, value="+351 912 345 678", hands_on=False
    )

    assert offered.dialog == f"remove-number-{number.pk}"
    assert offered.action == reverse("accounts:remove_number", args=[number.pk])


# ------------------------------------------------- taken off, and another put in its place


@pytest.mark.parametrize("name", ["remove_number", "remove_address"])
def test_a_row_goes_at_once_and_its_replacement_is_typed_and_saved(client, user, name):
    """Take a number off, type its replacement into the empty row, save. Removing used to
    wait for *Save*, so when adding a number or an address crashed the page (#454) the
    transaction put the old row back; a row that goes at once stays gone, and until #454 was
    fixed the page could then add nothing in its place."""
    gone, kept = two_rows(user, name)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert json.loads(remove(client, name, gone, **HTMX).content)["removed"] is True

    posted = after_the_script_took_off(html, gone)
    posted.update(first_name="Alex", last_name="Morgan")
    if name == "remove_number":
        posted.update({"phone_numbers-2-number_0": "PT", "phone_numbers-2-number_1": "912345670"})
    else:
        # A whole address: Portugal's cannot be kept without a postcode and a town (#306).
        posted.update(
            {
                "addresses-2-street": "Rua A 1",
                "addresses-2-postcode": "1000-001",
                "addresses-2-municipality": "Lisboa",
                "addresses-2-country": "PT",
            }
        )
    response = client.post(reverse("accounts:profile"), posted)

    assert response.status_code == 302, alerts_in(response)
    kind = getattr(removals, name.removeprefix("remove_").upper())
    held = list(removals.rows(kind, user))
    assert len(held) == 2 and held[0] == kept, "the row that stayed, and the one typed"
    assert held[1].owner == user and held[1].pk != gone.pk
    kept.refresh_from_db()
    assert kept.is_primary, "the row that inherited the primary keeps it"
