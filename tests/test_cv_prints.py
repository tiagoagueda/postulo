"""A CV chooses which of its owner's details it prints (#308).

A profile holds several numbers, several addresses on the web, several email addresses and
any number of identifiers, and every CV used to print the same ones. Each CV now answers
for itself, kind by kind, and what is held here is:

- two CVs of one person print different numbers and different links, and a third prints
  none -- checked in what the renderer is handed;
- a CV nobody has opened the choice on is the document it was, byte for byte;
- the default follows the profile and a choice pins a row;
- a pinned row that is deleted is replaced by nothing, the same for every kind, and the
  page says so;
- the master switch is still the master switch;
- what was sent is frozen, and a changed choice makes a new version;
- the archive, the API and the page each carry the choice.

Who may choose which row is `tests/security/test_cv_prints.py`.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import zipfile

import pytest
from allauth.account.models import EmailAddress
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.accounts.models import PersonIdentifier
from postulo.api.models import ApiToken
from postulo.core import export as export_module
from postulo.core import importer, phone_numbers, phones, postal, web_links
from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.documents import formats, printing, rendering
from postulo.documents.forms import CVForm
from postulo.documents.models import CV, CoverLetter, CVItem, CVKind, Prints
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db

MOBILE = "+351912345678"
DESK = "+351211111111"
#: The same two as a document prints them: grouped the way Portugal writes a number (#304).
#: A chooser on the CV's page, the API and the archive carry the stored form above.
MOBILE_PRINTED = "+351 912 345 678"
DESK_PRINTED = "+351 21 111 1111"
LINKEDIN = "https://www.linkedin.com/in/alex-morgan"
MASTODON = "https://mastodon.example/@alex"
FORGE = "https://codeberg.org/alex"
OTHER_FORGE = "https://gitlab.example/alex"
SITE = "https://alex.example"
ORCID = "0000-0002-1825-0097"


# ------------------------------------------------------------------ one person's details


@pytest.fixture
def person(user):
    """Somebody with several of everything a contact block can print."""
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    profile = user.profile
    profile.headline = "Backend engineer"
    profile.form_of_address = "Dr"
    profile.pronouns = "they/them"
    profile.save()
    PhoneNumber.objects.create(
        owner=user, holder=profile, number=MOBILE, kind="mobile", is_primary=True
    )
    PhoneNumber.objects.create(owner=user, holder=profile, number=DESK, kind="work")
    for kind, url, label, primary in (
        ("social", LINKEDIN, "LinkedIn", True),
        ("social", MASTODON, "", False),
        ("repository", FORGE, "", True),
        ("repository", OTHER_FORGE, "GitLab", False),
        ("website", SITE, "", True),
    ):
        WebLink.objects.create(
            owner=user, holder=profile, kind=kind, url=url, label=label, is_primary=primary
        )
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value=ORCID)
    PersonIdentifier.objects.create(
        profile=profile, scheme="other", value="R-1234", label="ResearcherID"
    )
    PostalAddress.objects.create(
        owner=user,
        holder=profile,
        street="Rua do Exemplo 1",
        municipality="Lisboa",
        country="PT",
        is_primary=True,
    )
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    EmailAddress.objects.create(user=user, email="alex@work.example", verified=True)
    EmailAddress.objects.create(user=user, email="typo@nowhere.example", verified=False)
    return user


def number(person, value):
    return person.profile.phone_numbers.get(number=value)


def link(person, url):
    return person.profile.web_links.get(url=url)


def identifier(person, value):
    return person.profile.identifiers.get(value=value)


def address(person, email):
    return EmailAddress.objects.get(user=person, email=email)


def a_cv(person, name="Main", **fields) -> CV:
    cv = CV.objects.create(owner=person, name=name, **fields)
    job = Experience.objects.create(
        owner=person,
        organisation=f"Aperture ({name})",
        role="Engineer",
        start_date=datetime.date(2021, 3, 1),
    )
    CVItem.objects.create(
        owner=person, cv=cv, content_type=ContentType.objects.get_for_model(job), object_id=job.pk
    )
    return cv


def pin(cv, **rows) -> CV:
    """Choose a row of each kind named, the way every door does: through `printing`."""
    for key, row in rows.items():
        if key == "identifiers":
            chosen = printing.choose_identifiers(cv, Prints.CHOSEN, [one.pk for one in row])
            cv.save()
            cv.pinned_identifiers.set(chosen)
        elif row is None:
            printing.choose(cv, key, Prints.NONE)
        else:
            printing.choose(cv, key, Prints.CHOSEN, row.pk)
    cv.save()
    return cv


def handed(monkeypatch, cv) -> dict:
    """The contact block a theme is handed for this CV: the context of the render itself."""
    seen = {}
    real = rendering.render_to_string

    def watching(template, context=None, *args, **kwargs):
        seen["contact"] = context["contact"]
        return real(template, context, *args, **kwargs)

    monkeypatch.setattr(rendering, "render_to_string", watching)
    rendering.render_cv_html(cv)
    monkeypatch.setattr(rendering, "render_to_string", real)
    return seen["contact"]


# ------------------------------------------------- what every CV printed before #308


def contact_details_before_308(owner) -> dict:
    """`rendering.contact_details` as it stood at the commit before this one, word for
    word. Kept here so that "nothing changes for a CV nobody opens the choice on" is
    checked against what was printed rather than against what is printed now.

    One word is not as it was: the number is grouped the way its country writes it, which
    is #304's change to every document and not this one's."""
    profile = getattr(owner, "profile", None)
    primary = phone_numbers.primary_for(profile) if profile is not None else None
    links = web_links.primaries_for(profile) if profile is not None else {}

    def link(kind: str) -> str:
        row = links.get(kind)
        return row.url if row else ""

    identifiers = list(profile.identifiers.all()) if profile is not None else []
    details = {
        "name": owner.get_full_name() or owner.display_name,
        "email": owner.email,
        "headline": getattr(profile, "headline", ""),
        "phone": phones.readable(primary.number) if primary else "",
        "location": postal.printed_location(profile),
        "website": link(web_links.Kind.WEBSITE),
        "linkedin_url": link(web_links.Kind.SOCIAL),
        "source_repo_url": link(web_links.Kind.REPOSITORY),
        "identifiers": identifiers,
    }
    details["details"] = [
        part
        for part in (
            details["email"],
            details["phone"],
            *(f"{row.display_label} {row.value}" for row in identifiers),
            details["location"],
            details["website"],
            details["linkedin_url"],
            details["source_repo_url"],
        )
        if part
    ]
    details["brief_details"] = [
        part for part in (details["email"], details["phone"], details["location"]) if part
    ]
    return details


def as_it_was(monkeypatch):
    """Draw documents with the contact block the parent commit built."""
    monkeypatch.setattr(
        rendering,
        "cv_contact",
        lambda cv: contact_details_before_308(cv.owner) if cv.show_contact_details else None,
    )


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", [CVKind.CV, CVKind.PORTFOLIO])
def test_a_cv_nobody_opened_the_choice_on_is_the_document_it_was_byte_for_byte(
    person, monkeypatch, theme, kind
):
    """A CV made before the change has every answer at its default, which is what the
    migration gives it. Its contact block holds what the old one held, key for key, and
    the page drawn from it is the same bytes -- so an unchanged CV is still the version
    already filed (#236), and nobody's document moved under them."""
    cv = a_cv(person, theme=theme, kind=kind)
    before = contact_details_before_308(person)
    now = rendering.contact_details(person, cv)

    assert {key: now[key] for key in before} == before
    assert list(now)[: len(before)] == list(before), "and in the order they were in"
    # What is new is blank until the CV says otherwise, and the name's line is the name.
    assert now["form_of_address"] == now["pronouns"] == ""
    assert now["messaging"] == "", "a handle is not printed because it was added (#682)"
    assert now["name_line"] == before["name"] == "Alex Morgan"

    html = rendering.render_cv_html(cv)
    text = rendering.cv_text(cv)
    as_it_was(monkeypatch)
    assert rendering.render_cv_html(cv).encode() == html.encode()
    # The text is drawn through `name_line`, which the old block does not carry.
    assert text.splitlines()[0] == "Alex Morgan"
    assert MOBILE_PRINTED in html and LINKEDIN in html and FORGE in html and SITE in html
    assert f"ORCID {ORCID}" in html and "Lisboa, Portugal" in html
    assert "Dr" not in html.split("</style>")[1] and "they/them" not in html
    assert "pronouns" not in html, "not even the rule for them is written"
    assert "<h1>Alex Morgan</h1>" in html, "and the name is not wrapped in anything"


def test_the_text_of_an_untouched_cv_is_the_text_it_was(person, monkeypatch):
    """Plain text and Word are drawn from the same block: the heading was `name` and is
    `name_line` now, so the old block is given the key and everything else is as it was."""
    cv = a_cv(person)
    text = rendering.cv_text(cv)
    monkeypatch.setattr(
        rendering,
        "cv_contact",
        lambda cv: {
            **contact_details_before_308(cv.owner),
            "name_line": contact_details_before_308(cv.owner)["name"],
        },
    )
    assert rendering.cv_text(cv) == text


def test_with_no_cv_the_block_is_what_it_always_was(person):
    """What a letter's sender block asks for, and what every existing caller got."""
    before = contact_details_before_308(person)
    now = rendering.contact_details(person)
    assert {key: now[key] for key in before} == before


def test_a_letter_prints_what_it_printed(person, monkeypatch):
    """A letter shares the function and none of the choice: no CV is handed to it, so it
    is drawn from the defaults, and it prints neither a form of address nor pronouns."""
    letter = CoverLetter.objects.create(owner=person, name="Cover", body="Dear {{ company }},")
    html = rendering.render_letter_html(letter)
    monkeypatch.setattr(
        rendering, "contact_details", lambda owner, cv=None: contact_details_before_308(owner)
    )
    assert rendering.render_letter_html(letter) == html
    assert MOBILE_PRINTED in html and "Lisboa, Portugal" in html
    assert "they/them" not in html and ">Dr " not in html


# --------------------------------------------- two CVs, two sets of details, and a third


def test_two_cvs_print_different_numbers_and_links_and_a_third_prints_none(person, monkeypatch):
    """The issue's own sentence, checked in what the renderer is handed."""
    academic = pin(
        a_cv(person, "Academic"),
        phone=number(person, DESK),
        email=address(person, "alex@work.example"),
        social=link(person, MASTODON),
        repository=link(person, OTHER_FORGE),
        identifiers=[identifier(person, ORCID)],
    )
    industry = a_cv(person, "Industry")
    bare = pin(
        a_cv(person, "Bare"),
        phone=None,
        email=None,
        social=None,
        repository=None,
        website=None,
    )
    printing.choose_identifiers(bare, Prints.NONE)
    bare.show_location = False
    bare.save()

    first = handed(monkeypatch, academic)
    assert first["phone"] == DESK_PRINTED
    assert first["email"] == "alex@work.example"
    assert first["linkedin_url"] == MASTODON
    assert first["source_repo_url"] == OTHER_FORGE
    assert first["website"] == SITE, "left alone, so it follows the profile"
    assert [row.value for row in first["identifiers"]] == [ORCID]
    assert first["details"] == [
        "alex@work.example",
        DESK_PRINTED,
        f"ORCID {ORCID}",
        "Lisboa, Portugal",
        SITE,
        MASTODON,
        OTHER_FORGE,
    ]

    second = handed(monkeypatch, industry)
    assert second["phone"] == MOBILE_PRINTED and second["email"] == person.email
    assert second["linkedin_url"] == LINKEDIN and second["source_repo_url"] == FORGE
    assert [row.value for row in second["identifiers"]] == [ORCID, "R-1234"]
    assert first["phone"] != second["phone"] and first["linkedin_url"] != second["linkedin_url"]

    third = handed(monkeypatch, bare)
    assert third["name"] == "Alex Morgan" and third["headline"] == "Backend engineer"
    for key in ("phone", "email", "linkedin_url", "source_repo_url", "website", "location"):
        assert third[key] == "", key
    assert third["identifiers"] == [] and third["details"] == [] == third["brief_details"]

    # And on the page each is drawn from what it was handed, and nothing else.
    html = rendering.render_cv_html(academic)
    assert DESK_PRINTED in html and MOBILE_PRINTED not in html
    assert MASTODON in html and LINKEDIN not in html
    assert "R-1234" not in html
    html = rendering.render_cv_html(bare)
    for printed in (
        MOBILE_PRINTED,
        DESK_PRINTED,
        MOBILE,
        DESK,
        person.email,
        LINKEDIN,
        FORGE,
        SITE,
        ORCID,
        "Lisboa",
    ):
        assert printed not in html, printed
    assert "Alex Morgan" in html


@pytest.mark.parametrize("kind", [CVKind.CV, CVKind.PORTFOLIO])
def test_every_renderer_is_handed_the_same_chosen_details(person, client, kind):
    """The theme, the preview, the plain text and the Word file: one block, read once."""
    cv = pin(
        a_cv(person, kind=kind),
        phone=number(person, DESK),
        social=link(person, MASTODON),
        website=None,
    )
    assert DESK_PRINTED in rendering.render_cv_html(cv)

    client.force_login(person)
    preview = client.get(reverse("documents:cv_preview", args=[cv.pk])).content.decode()
    assert DESK_PRINTED in preview and MOBILE_PRINTED not in preview
    assert MASTODON in preview and SITE not in preview

    text = rendering.cv_text(cv)
    assert (
        DESK_PRINTED in text
        and MOBILE_PRINTED not in text
        and MASTODON in text
        and SITE not in text
    )

    outline = rendering.cv_outline(cv)
    written = formats.get("txt").write(outline).decode()
    assert DESK_PRINTED in written and MOBILE_PRINTED not in written
    # A Word file is a zip of markup: the same words, read out of its one document.
    with zipfile.ZipFile(io.BytesIO(formats.get("docx").write(outline))) as word:
        body = word.read("word/document.xml").decode()
    assert (
        DESK_PRINTED in body
        and MOBILE_PRINTED not in body
        and MASTODON in body
        and SITE not in body
    )


def test_the_default_follows_the_profile_and_a_choice_pins_a_row(person):
    following = a_cv(person, "Following")
    pinned = pin(a_cv(person, "Pinned"), phone=number(person, MOBILE))
    assert rendering.contact_details(person, following)["phone"] == MOBILE_PRINTED
    assert rendering.contact_details(person, pinned)["phone"] == MOBILE_PRINTED

    # A new primary number, and neither CV is opened.
    phone_numbers.set_primary(number(person, DESK))
    web_links.set_primary(link(person, MASTODON))

    assert rendering.contact_details(person, following)["phone"] == DESK_PRINTED
    assert rendering.contact_details(person, following)["linkedin_url"] == MASTODON
    assert rendering.contact_details(person, pinned)["phone"] == MOBILE_PRINTED, "the pin stays"


def test_an_identifier_added_later_is_printed_by_default_and_not_by_a_chosen_set(person):
    following = a_cv(person, "Following")
    chosen = pin(a_cv(person, "Chosen"), identifiers=[identifier(person, ORCID)])
    PersonIdentifier.objects.create(profile=person.profile, scheme="isni", value="0000000121032683")

    assert len(rendering.contact_details(person, following)["identifiers"]) == 3
    assert [row.value for row in rendering.contact_details(person, chosen)["identifiers"]] == [
        ORCID
    ]


def test_the_location_is_one_line_or_nothing_and_never_the_street(person):
    cv = a_cv(person)
    assert rendering.contact_details(person, cv)["location"] == "Lisboa, Portugal"
    cv.show_location = False
    cv.save()
    details = rendering.contact_details(person, cv)
    assert details["location"] == "" and "Lisboa, Portugal" not in details["details"]
    for shown in (True, False):
        cv.show_location = shown
        cv.save()
        assert "Rua do Exemplo" not in rendering.render_cv_html(cv)


# ----------------------------------------------------- the master switch stays the master


def test_the_one_switch_still_prints_none_of_it(person, monkeypatch):
    cv = pin(a_cv(person, show_contact_details=False), phone=number(person, DESK))
    cv.show_form_of_address = cv.show_pronouns = True
    cv.save()
    assert handed(monkeypatch, cv) is None
    # The page itself: the file's title names its holder whatever this says, as it did.
    page = rendering.render_cv_html(cv).split("</head>")[1]
    for printed in (
        "Alex Morgan",
        DESK_PRINTED,
        MOBILE_PRINTED,
        DESK,
        MOBILE,
        person.email,
        "they/them",
        "Dr",
    ):
        assert printed not in page
    assert "Alex Morgan" not in rendering.cv_text(cv)


# ------------------------------------------------ the form of address and the pronouns


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", [CVKind.CV, CVKind.PORTFOLIO])
def test_a_cv_that_says_so_prints_them_beside_the_name(person, theme, kind):
    """The form of address before the name, in the name's own type; the pronouns after it,
    in brackets, in the small print the contact line is set in."""
    cv = a_cv(person, theme=theme, kind=kind, show_form_of_address=True, show_pronouns=True)
    html = rendering.render_cv_html(cv)
    # Each part in a `<bdi>` of its own, so that a form of address in one script beside a
    # name in another keeps its full stop on its own side (the browser walk draws it).
    assert (
        '<h1><bdi class="form-of-address">Dr</bdi> <bdi>Alex Morgan</bdi>'
        ' <span class="pronouns">(<bdi>they/them</bdi>)</span></h1>'
    ) in html
    rule = re.search(r"\.pronouns \{([^}]*)\}", html).group(1)
    for said in ("font-size: 8.5pt", "color: #4a4f5a", "font-weight: normal"):
        assert said in rule
    assert "font-variant: normal" in rule, "the name's small capitals stay on the name"
    # The file's properties name the person: how they are addressed is not who wrote it.
    assert '<meta name="author" content="Alex Morgan">' in html
    assert "<title>Alex Morgan — " in html

    outline = rendering.cv_outline(cv)
    assert outline.author == "Alex Morgan"
    assert rendering.cv_text(cv).splitlines()[0] == "Dr Alex Morgan (they/them)"


def test_each_is_printed_on_its_own_and_only_where_the_profile_gives_one(person):
    cv = a_cv(person, show_pronouns=True)
    details = rendering.contact_details(person, cv)
    assert (details["form_of_address"], details["pronouns"]) == ("", "they/them")
    assert details["name_line"] == "Alex Morgan (they/them)"
    html = rendering.render_cv_html(cv)
    assert (
        '<h1><bdi>Alex Morgan</bdi> <span class="pronouns">(<bdi>they/them</bdi>)</span></h1>'
    ) in html
    assert "form-of-address" not in html

    person.profile.pronouns = ""
    person.profile.save()
    cv.show_form_of_address = True
    cv.save()
    details = rendering.contact_details(person, cv)
    assert details["name_line"] == "Dr Alex Morgan"
    html = rendering.render_cv_html(cv)
    assert '<h1><bdi class="form-of-address">Dr</bdi> <bdi>Alex Morgan</bdi></h1>' in html
    assert "pronouns" not in html


def test_what_was_typed_beside_the_name_is_text_on_the_page(person):
    person.profile.pronouns = "<b>x</b>"
    person.profile.form_of_address = '"><script>'
    person.profile.save()
    cv = a_cv(person, show_form_of_address=True, show_pronouns=True)
    html = rendering.render_cv_html(cv)
    assert "<b>x</b>" not in html and "<script>" not in html
    assert "(<bdi>&lt;b&gt;x&lt;/b&gt;</bdi>)" in html


# ---------------------------------------- a pinned row that is deleted prints nothing


GONE = {
    "phone": lambda person: number(person, DESK),
    "email": lambda person: address(person, "alex@work.example"),
    "social": lambda person: link(person, MASTODON),
    "repository": lambda person: link(person, OTHER_FORGE),
    "website": lambda person: link(person, SITE),
}
PRINTED_AS = {
    "phone": "phone",
    "email": "email",
    "social": "linkedin_url",
    "repository": "source_repo_url",
    "website": "website",
}


@pytest.mark.parametrize("key", sorted(GONE))
def test_a_chosen_row_deleted_from_the_profile_prints_nothing_of_its_kind(person, client, key):
    """The same for every kind: none, never the primary in its place. A CV prints only
    what somebody chose for it, and whoever pinned a work number may have done so to keep
    the other one off the page."""
    row = GONE[key](person)
    cv = pin(a_cv(person), **{key: row})
    printed = printing.value_of(row, key)
    if key == "phone":
        printed = phones.readable(printed)  # a document groups a number (#304)
    assert rendering.contact_details(person, cv)[PRINTED_AS[key]] == printed

    row.delete()
    cv.refresh_from_db()

    detail = printing.BY_KEY[key]
    assert getattr(cv, detail.choice_field) == Prints.CHOSEN, "it still says it chose"
    assert getattr(cv, f"{detail.pin_field}_id") is None
    assert rendering.contact_details(person, cv)[PRINTED_AS[key]] == ""
    assert printing.gone(cv) == (detail,)

    # And both pages say so: the CV's own, beside the preview, and its settings.
    client.force_login(person)
    page = client.get(reverse("documents:cv_detail", args=[cv.pk])).content.decode()
    assert "data-cv-gone" in page and str(detail.gone) in page
    settings = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    assert "data-cv-gone" in settings and str(detail.gone) in settings
    form = CVForm(user=person, instance=cv)
    assert form.initial[f"prints_{key}"] == "none", "the menu opens on what it prints"
    assert form.choice_is_open


def test_a_deleted_identifier_leaves_the_chosen_set_and_the_rest_stay(person):
    cv = pin(
        a_cv(person),
        identifiers=[identifier(person, ORCID), identifier(person, "R-1234")],
    )
    identifier(person, "R-1234").delete()
    assert [row.value for row in rendering.contact_details(person, cv)["identifiers"]] == [ORCID]
    identifier(person, ORCID).delete()
    assert rendering.contact_details(person, cv)["identifiers"] == []
    PersonIdentifier.objects.create(profile=person.profile, scheme="isni", value="0000000121032683")
    assert rendering.contact_details(person, cv)["identifiers"] == [], "none, not all of them"


def test_a_row_that_is_no_longer_offered_is_a_row_that_has_gone(person):
    """*Several telephone numbers* switched off: the primary is the only number there is,
    as far as a document is concerned. A pin on another prints nothing and deletes nothing,
    and it is printed again the day the feature is back."""
    from postulo.plugins import policy
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    cv = pin(a_cv(person), phone=number(person, DESK))
    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=person, state=PluginPolicy.State.FORCED_OFF
    )
    policy.forget_decisions()
    assert rendering.contact_details(person, cv)["phone"] == ""
    assert [detail.key for detail in printing.gone(cv)] == ["phone"]

    PluginPolicy.objects.all().delete()
    policy.forget_decisions()
    assert rendering.contact_details(person, cv)["phone"] == DESK_PRINTED


def test_an_address_nobody_confirmed_is_not_offered_and_one_unconfirmed_later_is_not_printed(
    person,
):
    offered = [row.email for row in printing.offered(person, "email")]
    assert offered == [person.email, "alex@work.example"], "the primary first; no typo"
    cv = pin(a_cv(person), email=address(person, "alex@work.example"))
    EmailAddress.objects.filter(email="alex@work.example").update(verified=False)
    assert rendering.contact_details(person, cv)["email"] == ""


# -------------------------------------------------------------- what was sent is frozen


class Drawing:
    """A renderer whose PDF depends on the page it was given, as a real one's does."""

    name = "drawing"

    def is_available(self) -> bool:
        return True

    def render(self, html: str) -> bytes:
        return b"%PDF-1.7 " + hashlib.sha256(html.encode()).hexdigest().encode()


def test_changing_a_choice_after_sending_leaves_the_sent_version_alone(person):
    """The choices are read when the document is drawn and nowhere else. An unchanged CV
    is handed the version already filed (#236); a changed choice is a new version; and the
    one that was sent is the file, the page and the words it was."""
    cv = a_cv(person)
    backend = Drawing()
    sent = rendering.snapshot_cv(cv, backend=backend)
    kept = (sent.source_text, sent.plain_text, sent.checksum, sent.file.read())
    sent.file.close()
    assert MOBILE_PRINTED in sent.source_text and MOBILE_PRINTED in sent.plain_text

    again = rendering.snapshot_cv(cv, backend=backend)
    assert again.pk == sent.pk and again.already_filed

    pin(cv, phone=number(person, DESK), social=None)
    cv.show_pronouns = True
    cv.save()
    number(person, MOBILE).delete()

    sent.refresh_from_db()
    assert (sent.source_text, sent.plain_text, sent.checksum) == kept[:3]
    with sent.file.open("rb") as handle:
        assert handle.read() == kept[3]
    assert sent.file_is_as_rendered()
    assert DESK_PRINTED not in sent.source_text and "they/them" not in sent.plain_text

    changed = rendering.snapshot_cv(cv, backend=backend)
    assert changed.pk != sent.pk and not getattr(changed, "already_filed", False)
    assert DESK_PRINTED in changed.source_text and MOBILE_PRINTED not in changed.source_text
    assert LINKEDIN not in changed.source_text and "(they/them)" in changed.plain_text
    assert cv.renders.count() == 2


# ------------------------------------------------------------------------- the page


def posted(cv=None, **changes) -> dict:
    data = {
        "name": cv.name if cv else "New",
        "kind": "cv",
        "theme": "plain",
        "language": "",
        "show_contact_details": "on",
        "show_location": "on",
        "prints_phone": "default",
        "prints_email": "default",
        "prints_social": "default",
        "prints_repository": "default",
        "prints_website": "default",
        "prints_identifiers": "default",
    }
    data.update(changes)
    return {name: value for name, value in data.items() if value is not None}


def test_the_card_names_each_row_the_way_your_details_does(person, client):
    cv = a_cv(person)
    client.force_login(person)
    html = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    card = html[html.index("data-cv-prints") :]
    assert "What this CV prints about you" in html
    for label in (
        f"{MOBILE} (Mobile)",
        f"{DESK} (Work)",
        f"{LINKEDIN} (LinkedIn)",
        f">{MASTODON}<",
        f"{OTHER_FORGE} (GitLab)",
        "alex@work.example",
        f"ORCID: {ORCID}",
        "ResearcherID: R-1234",
    ):
        assert label in card, label
    assert "typo@nowhere.example" not in card, "an address nobody confirmed is not offered"
    # And its absence is said, to the account that has one and to no other.
    waits = "An address of yours is offered here once it has been confirmed."
    assert waits in card
    EmailAddress.objects.filter(user=person, verified=False).delete()
    assert waits not in client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    # The first choice of each follows the profile, and the sentence under says what that
    # comes to today.
    assert "Your primary number" in card and "Your account&#x27;s address" in card
    assert f"Printed as things stand: <bdi>{MOBILE}</bdi>." in card
    # Shut while nothing was chosen, and the master switch outside it.
    assert re.search(r"<details data-cv-choices>", card)
    assert card.index('name="show_contact_details"') < card.index("<details")


def test_the_card_reads_with_nothing_recorded_at_all(user, client):
    """Zero rows of every kind: two choices each, and a sentence saying why nothing prints."""
    cv = CV.objects.create(owner=user, name="Empty")
    client.force_login(user)
    response = client.get(reverse("documents:cv_update", args=[cv.pk]))
    card = response.content.decode()
    card = card[card.index("data-cv-prints") :]
    form = response.context["form"]
    for key in ("phone", "social", "repository", "website"):
        assert [value for value, _label in form.fields[f"prints_{key}"].choices] == [
            "default",
            "none",
        ]
        assert "Your details give none yet" in str(form.fields[f"prints_{key}"].help_text)
    assert [value for value, _label in form.fields["prints_identifiers"].choices] == [
        "default",
        "none",
    ], "nothing to tick, so no choice that leads to a list"
    assert "data-shown-if-chosen" not in card
    assert f"Printed as things stand: <bdi>{user.email}</bdi>." in card
    assert "Your details give none yet" in card


def test_one_row_of_a_kind_is_a_choice_between_following_it_and_pinning_it(user):
    PhoneNumber.objects.create(owner=user, holder=user.profile, number=MOBILE, is_primary=True)
    form = CVForm(user=user)
    labels = [str(label) for _value, label in form.fields["prints_phone"].choices]
    assert labels == ["Your primary number", MOBILE, "No telephone number"]


def test_saving_the_page_stores_each_choice_and_the_preview_follows(person, client):
    cv = a_cv(person)
    client.force_login(person)
    orcid = identifier(person, ORCID)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]),
        posted(
            cv,
            prints_phone=str(number(person, DESK).pk),
            prints_email="none",
            prints_social=str(link(person, MASTODON).pk),
            prints_identifiers="chosen",
            identifier_rows=[str(orcid.pk)],
            show_location=None,
            show_pronouns="on",
        ),
    )
    assert response.status_code == 302, response.context["form"].errors
    cv.refresh_from_db()
    assert (cv.phone_choice, cv.pinned_phone.number) == ("chosen", DESK)
    assert (cv.email_choice, cv.pinned_email) == ("none", None)
    assert cv.pinned_social.url == MASTODON
    assert cv.repository_choice == cv.website_choice == "default"
    assert cv.identifiers_choice == "chosen" and list(cv.pinned_identifiers.all()) == [orcid]
    assert (cv.show_location, cv.show_form_of_address, cv.show_pronouns) == (False, False, True)

    preview = client.get(reverse("documents:cv_preview", args=[cv.pk])).content.decode()
    assert DESK_PRINTED in preview and MOBILE_PRINTED not in preview and person.email not in preview
    assert "(<bdi>they/them</bdi>)" in preview
    assert "Lisboa" not in preview and "R-1234" not in preview

    # Opened again, the page shows what was chosen, with the choosers open.
    form = client.get(reverse("documents:cv_update", args=[cv.pk])).context["form"]
    assert form.initial["prints_phone"] == str(number(person, DESK).pk)
    assert form.initial["identifier_rows"] == [str(orcid.pk)]
    assert form.choice_is_open


def test_ticks_count_only_while_the_menu_says_only_the_ones_ticked(person, client):
    cv = a_cv(person)
    client.force_login(person)
    client.post(
        reverse("documents:cv_update", args=[cv.pk]),
        posted(cv, prints_identifiers="none", identifier_rows=[str(identifier(person, ORCID).pk)]),
    )
    cv.refresh_from_db()
    assert cv.identifiers_choice == "none" and not cv.pinned_identifiers.exists()
    assert rendering.contact_details(person, cv)["identifiers"] == []


def test_a_form_posted_without_the_choosers_leaves_every_choice_alone(person, client):
    """An older client, a script, a test written before this: it has never heard of them."""
    cv = pin(a_cv(person), phone=number(person, DESK), social=None)
    client.force_login(person)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]),
        {"name": "Renamed", "kind": "cv", "theme": "plain", "language": ""},
    )
    assert response.status_code == 302
    cv.refresh_from_db()
    assert cv.name == "Renamed"
    assert (cv.phone_choice, cv.pinned_phone.number, cv.social_choice) == ("chosen", DESK, "none")


def as_drawn(client, cv, **changes) -> dict:
    """What the settings page posts when it is saved as it was drawn: every control at the
    value it opened on, which is what a person who changes nothing sends back."""
    form = client.get(reverse("documents:cv_update", args=[cv.pk])).context["form"]
    data = {}
    for bound in form:
        value = bound.value()
        if isinstance(bound.field, forms.BooleanField):
            if value:
                data[bound.name] = "on"
        elif value is not None:
            data[bound.name] = value
    data.update(changes)
    return data


def test_a_save_that_touches_nothing_keeps_a_pin_on_a_number_that_is_kept_back(person, client):
    """*Several telephone numbers* is switched off, so the pinned number is not offered and
    its menu opens on *none*. Saving the page for any other reason -- a new name -- must not
    turn that into the answer: the pin deletes nothing and prints again the day the number
    is offered again, which is what was promised."""
    from postulo.plugins import policy
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    desk = number(person, DESK)
    cv = pin(a_cv(person), phone=desk, social=link(person, MASTODON))
    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=person, state=PluginPolicy.State.FORCED_OFF
    )
    policy.forget_decisions()
    client.force_login(person)
    address_of_page = reverse("documents:cv_update", args=[cv.pk])

    data = as_drawn(client, cv, name="Renamed")
    assert data["prints_phone"] == "none", "the menu opens on what the CV prints"
    assert client.post(address_of_page, data).status_code == 302
    cv.refresh_from_db()
    assert cv.name == "Renamed"
    assert (cv.phone_choice, cv.pinned_phone_id) == ("chosen", desk.pk), "left alone"
    assert cv.pinned_social.url == MASTODON, "and so is a pin that is offered"
    assert rendering.contact_details(person, cv)["phone"] == "", "nothing while it is kept back"

    PluginPolicy.objects.all().delete()
    policy.forget_decisions()
    assert rendering.contact_details(person, CV.objects.get(pk=cv.pk))["phone"] == DESK_PRINTED

    # A deliberate change of the menu is a change, kept back or not.
    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=person, state=PluginPolicy.State.FORCED_OFF
    )
    policy.forget_decisions()
    assert (
        client.post(address_of_page, as_drawn(client, cv, prints_phone="default")).status_code
        == 302
    )
    cv.refresh_from_db()
    assert (cv.phone_choice, cv.pinned_phone_id) == ("default", None)


def test_a_save_that_touches_nothing_keeps_a_pin_on_an_address_no_longer_confirmed(person, client):
    work = address(person, "alex@work.example")
    cv = pin(a_cv(person), email=work)
    EmailAddress.objects.filter(pk=work.pk).update(verified=False)
    client.force_login(person)

    data = as_drawn(client, cv, name="Renamed")
    assert data["prints_email"] == "none"
    assert client.post(reverse("documents:cv_update", args=[cv.pk]), data).status_code == 302
    cv.refresh_from_db()
    assert (cv.name, cv.email_choice, cv.pinned_email_id) == ("Renamed", "chosen", work.pk)
    assert rendering.contact_details(person, cv)["email"] == ""

    EmailAddress.objects.filter(pk=work.pk).update(verified=True)
    assert rendering.contact_details(person, cv)["email"] == "alex@work.example"


def test_saving_the_page_over_a_row_that_was_deleted_makes_none_the_answer(person, client):
    """The other case, and the stated design: the row is gone for good, the menu opens on
    *none*, and saving stores what the menu says."""
    cv = pin(a_cv(person), phone=number(person, DESK))
    number(person, DESK).delete()
    client.force_login(person)
    data = as_drawn(client, cv)
    assert data["prints_phone"] == "none"
    assert client.post(reverse("documents:cv_update", args=[cv.pk]), data).status_code == 302
    cv.refresh_from_db()
    assert (cv.phone_choice, cv.pinned_phone_id) == ("none", None)
    assert printing.gone(cv) == ()


def test_a_new_cv_starts_with_what_was_always_printed(person, client):
    client.force_login(person)
    response = client.post(reverse("documents:cv_create"), posted(name="Fresh"))
    assert response.status_code == 302
    cv = CV.objects.get(name="Fresh")
    assert printing.is_default(cv)
    assert rendering.contact_details(person, cv) == rendering.contact_details(person)


def test_a_cv_can_be_made_with_a_choice_already_in_it(person, client):
    client.force_login(person)
    orcid = identifier(person, ORCID)
    client.post(
        reverse("documents:cv_create"),
        posted(
            name="Chosen at birth",
            prints_phone=str(number(person, DESK).pk),
            prints_identifiers="chosen",
            identifier_rows=[str(orcid.pk)],
        ),
    )
    cv = CV.objects.get(name="Chosen at birth")
    assert cv.pinned_phone.number == DESK and list(cv.pinned_identifiers.all()) == [orcid]


# ---------------------------------------------------------------------- the archive


def exported(person) -> dict:
    return export_module.build_document(person)["documents"]["cvs"]


def test_the_archive_carries_the_choice_with_the_cv_by_what_each_row_says(person):
    pin(
        a_cv(person, "Academic"),
        phone=number(person, DESK),
        email=address(person, "alex@work.example"),
        social=link(person, MASTODON),
        repository=None,
        identifiers=[identifier(person, ORCID)],
    )
    document = export_module.build_document(person)
    # The number is read, never written down here: another change may take the next one.
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION > 26
    (entry,) = document["documents"]["cvs"]
    assert entry["prints"] == {
        "phone": {"choice": "chosen", "number": DESK},
        "email": {"choice": "chosen", "email": "alex@work.example"},
        "social": {"choice": "chosen", "url": MASTODON},
        "repository": {"choice": "none"},
        "website": {"choice": "default"},
        "messaging": {"choice": "default"},
        "identifiers": {"choice": "chosen", "rows": [{"scheme": "orcid", "value": ORCID}]},
        "location": True,
        "form_of_address": False,
        "pronouns": False,
        "birth_date": False,
        "birth_place": False,
        "nationality": False,
        "gender": False,
        "eqf_level": False,
    }
    assert "pinned_phone" not in json.dumps(entry), "no row is named by an id"


def test_a_pin_on_a_row_that_has_gone_is_archived_as_what_it_prints(person):
    cv = pin(a_cv(person), phone=number(person, DESK))
    number(person, DESK).delete()
    (entry,) = exported(person)
    assert entry["prints"]["phone"] == {"choice": "none"}
    assert rendering.contact_details(person, CV.objects.get(pk=cv.pk))["phone"] == ""


def round_trip(person, other_user, *, edit=None):
    buffer = export_module.write_archive(person)
    if edit is not None:
        with zipfile.ZipFile(buffer) as archive:
            document = json.loads(archive.read(export_module.MANIFEST_NAME))
        edit(document)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
        buffer.seek(0)
    # The numbers are unique across the instance, so the account they came from lets go
    # of them first, as moving to another instance would.
    PhoneNumber.objects.filter(owner=person).delete()
    return importer.load(other_user, zipfile.ZipFile(buffer))


def test_an_import_points_each_choice_at_the_rows_it_has_just_made(person, other_user):
    pin(
        a_cv(person, "Academic", show_pronouns=True, show_location=False),
        phone=number(person, DESK),
        social=link(person, MASTODON),
        website=None,
        identifiers=[identifier(person, ORCID)],
    )
    report = round_trip(person, other_user)
    assert not [line for line in report.skipped if "chosen to print" in line], report.skipped

    cv = CV.objects.get(owner=other_user, name="Academic")
    assert cv.pinned_phone.owner == other_user and cv.pinned_phone.number == DESK
    assert cv.pinned_phone.pk != 0 and cv.pinned_phone.holder == other_user.profile
    assert cv.pinned_social.holder == other_user.profile and cv.pinned_social.url == MASTODON
    assert cv.website_choice == "none" and cv.repository_choice == "default"
    assert [row.profile for row in cv.pinned_identifiers.all()] == [other_user.profile]
    assert (cv.show_location, cv.show_pronouns, cv.show_form_of_address) == (False, True, False)

    details = rendering.contact_details(other_user, cv)
    assert details["phone"] == DESK_PRINTED and details["linkedin_url"] == MASTODON
    assert details["website"] == "" and details["source_repo_url"] == FORGE
    assert [row.value for row in details["identifiers"]] == [ORCID]
    assert details["pronouns"] == "they/them" and details["location"] == ""


def test_an_older_archive_imports_with_every_choice_at_its_default(person, other_user):
    pin(a_cv(person, "Academic"), phone=number(person, DESK), social=None)

    def as_the_format_before(document):
        document["postulo"]["format"] = export_module.FORMAT_VERSION - 1
        for entry in document["documents"]["cvs"]:
            del entry["prints"]

    round_trip(person, other_user, edit=as_the_format_before)
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert printing.is_default(cv)
    assert rendering.contact_details(other_user, cv)["phone"] == MOBILE_PRINTED


def test_a_reference_that_cannot_be_resolved_falls_back_to_the_default(person, other_user):
    """A number the file names and the account does not have, an address that is not one
    of the importing account's confirmed ones, an identifier the file never carried: each
    kind goes back to following the profile, and the report says so."""
    pin(
        a_cv(person, "Academic"),
        phone=number(person, DESK),
        email=address(person, "alex@work.example"),
        social=link(person, MASTODON),
        identifiers=[identifier(person, ORCID)],
    )

    def break_references(document):
        (entry,) = document["documents"]["cvs"]
        entry["prints"]["phone"]["number"] = "+351999999999"
        entry["prints"]["identifiers"]["rows"].append({"scheme": "isni", "value": "nobody"})
        entry["prints"]["website"] = {"choice": "sideways"}
        entry["prints"]["pronouns"] = "yes"

    report = round_trip(person, other_user, edit=break_references)
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert (cv.phone_choice, cv.pinned_phone) == ("default", None)
    assert (cv.email_choice, cv.pinned_email) == ("default", None), "not this account's"
    assert cv.identifiers_choice == "default" and not cv.pinned_identifiers.exists()
    assert cv.website_choice == "default" and cv.show_pronouns is False
    assert cv.pinned_social.url == MASTODON, "what could be found was"
    said = [line for line in report.skipped if "chosen to print" in line]
    # The number, the address, the identifiers, and the answer that is not one of the three.
    assert len(said) == 4 and all("Academic" in line for line in said)
    assert [line for line in report.skipped if "(pronouns)" in line and "yes or no" in line]
    assert rendering.contact_details(other_user, cv)["phone"] == MOBILE_PRINTED


#: Left out of the entry altogether, which no JSON value can say.
MISSING = object()

#: What `prints.identifiers.rows` may be found holding in a file somebody edited. The
#: first two used to stop the whole import with a `TypeError`; the others were read as a
#: chosen set with nothing in it, or with half of what was named, and nothing was said.
MALFORMED_ROWS = {
    "a number": 5,
    "a boolean": True,
    "a string": "abc",
    "null": None,
    "left out": MISSING,
    "one row, not in a list": {"scheme": "orcid", "value": ORCID},
    "a list of anything but rows": [5, None, "x"],
    "a row and something that is not one": [{"scheme": "orcid", "value": ORCID}, "junk"],
    "a row whose value is a number": [{"scheme": "other", "value": 1234}],
}


@pytest.mark.parametrize("shape", sorted(MALFORMED_ROWS))
def test_a_set_of_identifiers_that_cannot_be_read_takes_the_answer_back_to_the_default(
    person, other_user, shape
):
    """Half a chosen set is a set nobody chose, and so is one that is not a list of rows:
    the answer goes back to every identifier, the report says so, and the rest of the
    archive is imported."""
    PersonIdentifier.objects.create(
        profile=person.profile, scheme="other", value="1234", label="Staff number"
    )
    pin(a_cv(person, "Academic"), identifiers=[identifier(person, ORCID)], social=None)

    def malform(document):
        (entry,) = document["documents"]["cvs"]
        if MALFORMED_ROWS[shape] is MISSING:
            del entry["prints"]["identifiers"]["rows"]
        else:
            entry["prints"]["identifiers"]["rows"] = MALFORMED_ROWS[shape]

    report = round_trip(person, other_user, edit=malform)
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert cv.identifiers_choice == "default" and not cv.pinned_identifiers.exists()
    assert len([line for line in report.skipped if "chosen to print (identifiers)" in line]) == 1
    assert len(rendering.contact_details(other_user, cv)["identifiers"]) == 3
    assert cv.social_choice == "none", "the answers beside it are read as they were"


def test_a_chosen_set_with_nothing_in_it_is_still_a_set(person, other_user):
    """An empty list is not malformed: every identifier it had chosen was deleted, and it
    prints none."""
    cv = pin(a_cv(person, "Academic"), identifiers=[identifier(person, ORCID)])
    identifier(person, ORCID).delete()
    assert exported(person)[0]["prints"]["identifiers"] == {"choice": "chosen", "rows": []}
    report = round_trip(person, other_user)
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert cv.identifiers_choice == "chosen" and not cv.pinned_identifiers.exists()
    assert not [line for line in report.skipped if "chosen to print" in line]


#: The same for a kind that prints one row. The first six were left at the default without
#: a word; the last two were already said.
MALFORMED_ANSWERS = {
    "a number": 5,
    "null": None,
    "a list": ["chosen"],
    "a choice that is not one of the three": {"choice": "sideways"},
    "no choice": {"number": DESK},
    "a choice that is not text": {"choice": ["chosen"]},
    "chosen, and nothing named": {"choice": "chosen"},
    "chosen, and what is named is not text": {"choice": "chosen", "number": [DESK]},
}


@pytest.mark.parametrize("shape", sorted(MALFORMED_ANSWERS))
@pytest.mark.parametrize("key", ["phone", "website"])
def test_an_answer_that_cannot_be_read_goes_back_to_the_default_and_is_said(
    person, other_user, key, shape
):
    pin(a_cv(person, "Academic"), phone=number(person, DESK), website=None, social=None)

    def malform(document):
        (entry,) = document["documents"]["cvs"]
        entry["prints"][key] = MALFORMED_ANSWERS[shape]

    report = round_trip(person, other_user, edit=malform)
    cv = CV.objects.get(owner=other_user, name="Academic")
    detail = printing.BY_KEY[key]
    assert getattr(cv, detail.choice_field) == "default"
    assert getattr(cv, f"{detail.pin_field}_id") is None
    said = [line for line in report.skipped if "chosen to print" in line]
    assert len(said) == 1 and f"({key})" in said[0] and "Academic" in said[0]
    assert cv.social_choice == "none", "the answers beside it are read as they were"


def test_a_kind_an_archive_leaves_out_is_at_its_default_and_nothing_is_said(person, other_user):
    """Not malformed, only absent: what an archive from before the kind existed looks like."""
    pin(a_cv(person, "Academic"), phone=number(person, DESK), social=None)

    def leave_out(document):
        (entry,) = document["documents"]["cvs"]
        del entry["prints"]["phone"], entry["prints"]["identifiers"], entry["prints"]["location"]

    report = round_trip(person, other_user, edit=leave_out)
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert (cv.phone_choice, cv.identifiers_choice, cv.show_location) == (
        "default",
        "default",
        True,
    )
    assert cv.social_choice == "none"
    assert not [line for line in report.skipped if "Academic" in line], report.skipped


@pytest.mark.parametrize("block", [5, "everything", ["phone"], True])
def test_a_block_that_is_not_one_leaves_every_answer_at_its_default_and_is_said(
    person, other_user, block
):
    pin(a_cv(person, "Academic"), phone=number(person, DESK), social=None)

    def malform(document):
        (entry,) = document["documents"]["cvs"]
        entry["prints"] = block

    report = round_trip(person, other_user, edit=malform)
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert printing.is_default(cv)
    said = [line for line in report.skipped if "Academic" in line]
    for name in (*printing.BY_KEY, "identifiers", *printing.SWITCHES):
        assert len([line for line in said if f"({name})" in line]) == 1, name


@pytest.mark.parametrize("name", sorted(printing.SWITCHES))
@pytest.mark.parametrize("answer", ["yes", 1, None, {"choice": "default"}])
def test_a_yes_or_no_that_is_neither_is_left_as_a_new_cv_has_it_and_is_said(
    person, other_user, name, answer
):
    cv = a_cv(person, "Academic", show_location=False, show_form_of_address=True)
    cv.show_pronouns = True
    cv.save()

    def malform(document):
        (entry,) = document["documents"]["cvs"]
        entry["prints"][name] = answer

    report = round_trip(person, other_user, edit=malform)
    cv = CV.objects.get(owner=other_user, name="Academic")
    column = printing.SWITCHES[name]
    assert getattr(cv, column) is CV._meta.get_field(column).default
    said = [line for line in report.skipped if "Academic" in line]
    assert len(said) == 1 and f"({name})" in said[0] and "yes or no" in said[0]


# Two identifiers of no known scheme may hold one value under two names -- a staff number
# and a library card that happen to match -- and scheme and value alone cannot tell them
# apart.


def twins(owner) -> list:
    return [row for row in printing.offered(owner, "identifiers") if row.value == "R-1234"]


@pytest.mark.parametrize("which", [0, 1])
def test_two_other_identifiers_with_one_value_are_told_apart_by_what_they_are_called(
    person, other_user, which
):
    PersonIdentifier.objects.create(
        profile=person.profile, scheme="other", value="R-1234", label="Scopus"
    )
    chosen = twins(person)[which]
    pin(a_cv(person, "Academic"), identifiers=[chosen])
    (entry,) = exported(person)
    assert entry["prints"]["identifiers"]["rows"] == [
        {"scheme": "other", "value": "R-1234", "label": chosen.label}
    ]

    report = round_trip(person, other_user)
    assert not [line for line in report.skipped if "chosen to print" in line], report.skipped
    assert sorted(row.label for row in twins(other_user)) == ["ResearcherID", "Scopus"]
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert [row.label for row in cv.pinned_identifiers.all()] == [chosen.label]
    assert [
        row.display_label for row in rendering.contact_details(other_user, cv)["identifiers"]
    ] == [chosen.label]


def test_an_archive_from_before_the_name_was_carried_matches_as_it_did(person, other_user):
    """Scheme and value, and the first row that has them."""
    pin(a_cv(person, "Academic"), identifiers=[identifier(person, "R-1234")])

    def as_it_was_written(document):
        (entry,) = document["documents"]["cvs"]
        for row in entry["prints"]["identifiers"]["rows"]:
            del row["label"]

    report = round_trip(person, other_user, edit=as_it_was_written)
    assert not [line for line in report.skipped if "chosen to print" in line], report.skipped
    cv = CV.objects.get(owner=other_user, name="Academic")
    assert [row.value for row in cv.pinned_identifiers.all()] == ["R-1234"]


def test_the_candidate_file_carries_no_cv_and_so_no_choice(person):
    """#181's file is the person's own record: their details and their career. A CV is a
    document made from it, and is not in it."""
    pin(a_cv(person), phone=number(person, DESK))
    document = export_module.build_candidate_document(person)
    assert "documents" not in document
    assert "prints" not in json.dumps(document) and "pinned" not in json.dumps(document)


# ---------------------------------------------------------------------------- the API


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, cv, payload, **headers):
    return client.patch(
        f"/api/v1/cvs/{cv.pk}", data=json.dumps(payload), content_type="application/json", **headers
    )


def test_the_api_reads_the_choice_with_what_it_comes_to_and_what_may_be_chosen(person, client):
    cv = pin(a_cv(person), phone=number(person, DESK), website=None)
    body = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(person)).json()
    assert body["show_contact_details"] is True
    prints = body["prints"]
    assert prints["phone"]["choice"] == "chosen"
    assert prints["phone"]["id"] == number(person, DESK).pk and prints["phone"]["printed"] == DESK
    assert [row["value"] for row in prints["phone"]["offered"]] == [MOBILE, DESK]
    assert prints["phone"]["offered"][1]["label"] == f"{DESK} (Work)"
    assert prints["website"] == {
        "choice": "none",
        "id": None,
        "printed": "",
        "offered": [{"id": link(person, SITE).pk, "label": SITE, "value": SITE}],
    }
    assert prints["email"]["choice"] == "default" and prints["email"]["printed"] == person.email
    assert [row["value"] for row in prints["email"]["offered"]] == [
        person.email,
        "alex@work.example",
    ]
    assert prints["identifiers"]["choice"] == "default" and prints["identifiers"]["ids"] == []
    assert prints["identifiers"]["printed"] == [f"ORCID {ORCID}", "ResearcherID R-1234"]
    assert (prints["location"], prints["form_of_address"], prints["pronouns"]) == (
        True,
        False,
        False,
    )
    assert (prints["birth_date"], prints["birth_place"]) == (False, False), "off, as a new CV is"
    assert prints["nationality"] is False and prints["gender"] is False
    assert prints["eqf_level"] is False
    # The list of CVs is the list it was: the choice is on the one CV asked for.
    assert "prints" not in client.get("/api/v1/cvs", **bearer(person)).json()["items"][0]


def test_the_api_writes_each_choice_and_answers_with_the_cv_as_it_stands(person, client):
    cv = a_cv(person)
    before = cv.updated_at
    orcid = identifier(person, ORCID)
    response = patch(
        client,
        cv,
        {
            "prints": {
                "phone": {"choice": "chosen", "id": number(person, DESK).pk},
                "social": {"choice": "none"},
                "identifiers": {"choice": "chosen", "ids": [orcid.pk]},
                "pronouns": True,
                "location": False,
            }
        },
        **bearer(person, "write"),
    )
    assert response.status_code == 200, response.content
    body = response.json()["prints"]
    assert body["phone"]["printed"] == DESK and body["social"]["printed"] == ""
    assert body["identifiers"]["ids"] == [orcid.pk] and body["pronouns"] is True
    cv.refresh_from_db()
    assert cv.pinned_phone.number == DESK and cv.social_choice == "none"
    assert cv.repository_choice == "default", "a kind left out is left alone"
    assert list(cv.pinned_identifiers.all()) == [orcid]
    assert (cv.show_location, cv.show_pronouns) == (False, True)
    assert cv.updated_at > before

    # Back to following the profile, and the master switch.
    response = patch(
        client,
        cv,
        {"show_contact_details": False, "prints": {"phone": {"choice": "default"}}},
        **bearer(person, "write"),
    )
    cv.refresh_from_db()
    assert response.status_code == 200 and response.json()["show_contact_details"] is False
    assert (cv.phone_choice, cv.pinned_phone, cv.show_contact_details) == ("default", None, False)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ({"phone": {"choice": "sideways"}}, "prints.phone.choice"),
        ({"phone": {"choice": "chosen"}}, "prints.phone.id"),
        ({"phone": {"choice": "chosen", "id": 987654}}, "prints.phone.id"),
        ({"phone": {"choice": "default", "id": 1}}, "prints.phone.id"),
        ({"identifiers": {"choice": "chosen", "ids": [987654]}}, "prints.identifiers.ids"),
        ({"identifiers": {"choice": "none", "ids": [1]}}, "prints.identifiers.ids"),
        ({"email": {"choice": "chosen", "id": "x"}}, "id"),
        ({"location": "perhaps"}, "location"),
    ],
)
def test_the_api_refuses_what_is_not_a_choice_and_stores_none_of_the_call(
    person, client, answer, said
):
    cv = a_cv(person)
    answer = {"website": {"choice": "none"}, **answer}
    response = patch(client, cv, {"prints": answer}, **bearer(person, "write"))
    assert response.status_code == 422, response.content
    assert response["Content-Type"].startswith("application/problem+json")
    assert said in json.dumps(response.json())
    cv.refresh_from_db()
    assert printing.is_default(cv), "the answer beside the refused one was not stored either"


def test_the_api_needs_write_to_change_and_read_to_look(person, client):
    cv = a_cv(person)
    assert (
        patch(client, cv, {"prints": {"location": False}}, **bearer(person, "read")).status_code
        == 403
    )
    assert client.get(f"/api/v1/cvs/{cv.pk}", **bearer(person, "write")).status_code == 403
    assert (
        patch(client, cv, {"prints": {"location": False}}, **bearer(person, "captures")).status_code
        == 403
    )
    cv.refresh_from_db()
    assert cv.show_location is True


def test_the_schema_describes_the_choice(person, client):
    schema = client.get("/api/v1/openapi.json", **bearer(person)).json()
    components = schema["components"]["schemas"]
    assert set(components["CVPrintsOut"]["properties"]) == {
        "phone",
        "email",
        "social",
        "repository",
        "website",
        "messaging",
        "identifiers",
        "location",
        "form_of_address",
        "pronouns",
        "birth_date",
        "birth_place",
        "nationality",
        "gender",
        "eqf_level",
    }
    assert "prints" in components["CVDetailOut"]["properties"]
    assert "patch" in schema["paths"]["/api/v1/cvs/{pk}"]


@pytest.mark.parametrize("theme", ["classic", "plain"])
def test_a_portfolio_says_a_current_role_is_still_current(person, theme):
    """An entry with a start and no end prints "2021 – present", not 2021 alone (#515).

    The page and the plain text are the same line, as an employer reads one or the other.
    """
    cv = a_cv(person, kind=CVKind.PORTFOLIO, theme=theme)
    html = " ".join(rendering.render_cv_html(cv).split())
    assert re.search(r"2021\s*–\s*(<[^>]+>\s*)*present", html)
    assert "2021 – present" in rendering.cv_text(cv)


# ------------------------------------------------------------ a messaging handle (#682)
#
# A handle is not printed because it was added: the default prints nothing, so the choice
# is *none* or *chosen*, and a CV nobody touched is the document it was.


@pytest.fixture
def with_handles(person):
    from postulo.core.models import MessagingHandle

    for service, handle, primary in (
        ("matrix", "@alex:example.org", True),
        ("signal", "alex.42", False),
    ):
        MessagingHandle.objects.create(
            owner=person,
            holder=person.profile,
            service=service,
            handle=handle,
            is_primary=primary,
        )
    return person


def test_a_handle_is_not_printed_because_it_was_added(with_handles):
    """Even the primary one: a CV nobody opened the choice on is byte for byte the same."""
    cv = a_cv(with_handles)

    now = rendering.contact_details(with_handles, cv)
    before = contact_details_before_308(with_handles)

    assert now["messaging"] == ""
    assert {key: now[key] for key in before} == before
    html = rendering.render_cv_html(cv)
    assert "@alex:example.org" not in html and "alex.42" not in html
    assert printing.is_default(cv)


def test_a_chosen_handle_prints_beside_the_number_in_the_details_list(with_handles):
    cv = a_cv(with_handles)
    row = with_handles.profile.messaging_handles.get(service="signal")

    pin(cv, messaging=row)
    cv = CV.objects.get(pk=cv.pk)
    details = rendering.contact_details(with_handles, cv)

    assert details["messaging"] == "Signal alex.42"
    assert details["details"].index(MOBILE_PRINTED) + 1 == details["details"].index(
        "Signal alex.42"
    )
    assert "Signal alex.42" in rendering.render_cv_html(cv)
    assert "Signal alex.42" in rendering.cv_text(cv)
    assert not printing.is_default(cv)


def test_one_at_most_can_be_chosen_per_cv(with_handles):
    cv = a_cv(with_handles)
    first, second = with_handles.profile.messaging_handles.order_by("-is_primary")

    pin(cv, messaging=first)
    pin(cv, messaging=second)

    assert CV.objects.get(pk=cv.pk).pinned_messaging == second


def test_none_of_a_kind_that_prints_nothing_is_the_default_on_the_page(with_handles):
    cv = a_cv(with_handles)
    row = with_handles.profile.messaging_handles.get(service="matrix")
    pin(cv, messaging=row)

    form = CVForm(user=with_handles, instance=CV.objects.get(pk=cv.pk))
    assert form.initial["prints_messaging"] == str(row.pk)
    offered = [value for value, _label in form.fields["prints_messaging"].choices]
    assert offered[-1] == "none" and "default" not in offered

    saved = CVForm(
        user=with_handles,
        instance=cv,
        data={
            "name": cv.name,
            "kind": cv.kind,
            "theme": cv.theme,
            "language": "",
            "show_contact_details": "on",
            "show_location": "on",
            "prints_messaging": "none",
        },
    )
    assert saved.is_valid(), saved.errors
    saved.save()
    cv.refresh_from_db()
    assert cv.messaging_choice == Prints.DEFAULT and cv.pinned_messaging_id is None
    assert printing.is_default(cv)


def test_a_chosen_handle_that_has_gone_prints_none_and_says_so(with_handles):
    cv = a_cv(with_handles)
    row = with_handles.profile.messaging_handles.get(service="signal")
    pin(cv, messaging=row)
    row.delete()
    cv = CV.objects.get(pk=cv.pk)

    assert rendering.contact_details(with_handles, cv)["messaging"] == ""
    assert [detail.key for detail in printing.gone(cv)] == ["messaging"]


def test_a_handle_chosen_while_the_feature_is_off_prints_nothing_and_comes_back(with_handles):
    from postulo.plugins.messaging_contacts import MESSAGING_CONTACTS
    from postulo.plugins.models import PluginPolicy

    cv = a_cv(with_handles)
    row = with_handles.profile.messaging_handles.get(service="matrix")
    pin(cv, messaging=row)
    policy = PluginPolicy.objects.create(
        plugin=MESSAGING_CONTACTS, person=with_handles, state=PluginPolicy.State.FORCED_OFF
    )

    assert rendering.contact_details(with_handles, CV.objects.get(pk=cv.pk))["messaging"] == ""
    assert CV.objects.get(pk=cv.pk).pinned_messaging_id == row.pk, "kept, not deleted"
    policy.delete()
    assert (
        rendering.contact_details(with_handles, CV.objects.get(pk=cv.pk))["messaging"]
        == "Matrix @alex:example.org"
    )


def test_the_archive_names_a_chosen_handle_by_what_it_says(with_handles):
    cv = a_cv(with_handles)
    pin(cv, messaging=with_handles.profile.messaging_handles.get(service="signal"))

    block = printing.as_archived(CV.objects.get(pk=cv.pk))

    assert block["messaging"] == {
        "choice": "chosen",
        "service": "signal",
        "label": "",
        "handle": "alex.42",
    }
    assert "pinned_messaging" not in json.dumps(block), "no row is named by an id"
    other = CV.objects.create(owner=with_handles, name="Restored")
    assert printing.restore(other, block) == []
    assert other.pinned_messaging == with_handles.profile.messaging_handles.get(service="signal")


def test_the_api_reads_and_writes_the_choice(client, with_handles):
    from postulo.api.models import ApiToken

    cv = a_cv(with_handles)
    row = with_handles.profile.messaging_handles.get(service="matrix")
    _record, raw = ApiToken.issue(with_handles, "Agent", scopes=("read", "write"))
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    answer = client.patch(
        f"/api/v1/cvs/{cv.pk}",
        data=json.dumps({"prints": {"messaging": {"choice": "chosen", "id": row.pk}}}),
        content_type="application/json",
        **headers,
    )

    assert answer.status_code == 200, answer.content
    prints = answer.json()["prints"]["messaging"]
    assert prints["choice"] == "chosen" and prints["id"] == row.pk
    assert prints["printed"] == "Matrix @alex:example.org"
    assert {one["id"] for one in prints["offered"]} == {
        one.pk for one in with_handles.profile.messaging_handles.all()
    }
