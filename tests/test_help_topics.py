"""Help where the question is asked: the topics, their pages, and *Your details* (#302).

A card's question mark shows one sentence and opens the card's help in a drawer; with
scripts off it is a link to a page holding the same help. A sentence that says what a field
is for is that field's tooltip; a format a field requires stays under it. What needs a
browser -- the tooltip showing and going, the drawer opening and closing, where either is
drawn -- is in `tests/e2e/test_help.py`. What is checked here is what the server draws:

- every topic has a page of its own, for somebody signed in, drawn from the same template
  as its drawer, so the two cannot say different things;
- the help of *Your picture* states the formats and the sizes **the code enforces**, read
  from the code, and so does the sentence under the upload box;
- every card of *Your details* has its question mark, its sentence and its drawer, each
  named by the other;
- on that page the help that says what a field is for is marked as a tooltip, and the help
  that says what may be typed is not; a page with anything on it refused has no tooltip at
  all, and every card's sentence is under its title;
- the help says what the code does: each sentence a reviewer found untrue is held here;
- the same rows on a contact's form are as they were, byte for byte at every switch.
"""

from __future__ import annotations

import io
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.urls import reverse
from django.utils import translation
from PIL import Image

from postulo.accounts import avatars
from postulo.accounts.forms import ProfileForm
from postulo.accounts.models import PersonIdentifier
from postulo.core import help as help_topics
from postulo.core.models import PhoneNumber
from postulo.jobs.models import Company, Contact
from tests.test_components import render

pytestmark = pytest.mark.django_db

#: The cards of *Your details*, each with the topic its question mark opens.
CARDS = {
    "section-picture": "your-picture",
    "section-name": "your-name",
    "section-contact": "contact-block",
    "section-phones": "telephone-numbers",
    "section-links-social": "links",
    "section-links-repository": "links",
    "section-links-website": "links",
    "section-addresses": "postal-addresses",
    "section-identifiers": "identifiers",
}


def details(client, user) -> str:
    client.force_login(user)
    response = client.get(reverse("accounts:profile"))
    assert response.status_code == 200
    return response.content.decode()


def help_text_of(html: str) -> str:
    """What a page or a drawer says: the inside of its `data-help-text`, spaces evened."""
    found = re.search(r"<div[^>]*data-help-text[^>]*>(.*?)</div>", html, re.S)
    assert found, "nothing on the page is marked as a help topic's text"
    return " ".join(found.group(1).split())


def card(html: str, anchor: str) -> str:
    """One card of the page: from its id to the next card's."""
    start = html.index(f'id="{anchor}"')
    following = [html.find(f'id="{other}"', start + 1) for other in CARDS if other != anchor]
    following = [at for at in following if at > start]
    return html[start : min(following)] if following else html[start:]


def paragraph(html: str, element_id: str) -> str:
    found = re.search(rf'<p[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    assert found, f"no paragraph carries the id {element_id}"
    return found.group(0)


# ---------------------------------------------------------------------- the topics


def test_a_card_has_a_topic_and_every_topic_a_card():
    assert set(CARDS.values()) == set(help_topics.TOPICS)


@pytest.mark.parametrize("slug", sorted(help_topics.TOPICS))
def test_a_topic_is_a_page_of_its_own(client, user, slug):
    """Where the question mark leads with scripts off: the help, readable on its own, with
    its name as the page's heading and a way back to the page its card is on."""
    topic = help_topics.topic(slug)
    client.force_login(user)

    response = client.get(reverse("core:help_topic", args=[slug]))

    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(rf"<h1[^>]*>\s*Help: {re.escape(str(topic.title))}\s*</h1>", html)
    assert help_text_of(html).startswith("<p>")
    assert f'href="{reverse(topic.home)}"' in html
    assert "Back to Your details" in html


def test_a_topic_is_for_somebody_signed_in(client):
    response = client.get(reverse("core:help_topic", args=["your-picture"]))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_a_topic_nobody_wrote_is_not_found(client, user):
    client.force_login(user)

    assert client.get("/help/no-such-thing/").status_code == 404


def test_the_tag_refuses_a_topic_nobody_wrote():
    """A question mark that leads nowhere is a mistake in the template, said where it is
    drawn."""
    with pytest.raises(TemplateSyntaxError, match="No help topic named"):
        render('{% load postulo %}{% help_topic "no-such-thing" as about %}')


@pytest.mark.parametrize("slug", sorted(help_topics.TOPICS))
@pytest.mark.parametrize("language", ["en-GB", "fr-FR", "pt-PT", "pt-BR", "de"])
def test_a_topic_draws_in_every_language(slug, language):
    """Each is a template of translated paragraphs, and a translation that drops a
    placeholder or breaks a tag fails where it is drawn."""
    topic = help_topics.topic(slug)
    with translation.override(language):
        html = get_template(topic.template).render(topic.variables())
        assert str(topic.title) and str(topic.summary) and str(topic.back)

    assert html.count("<p") == html.count("</p>") >= 3


def test_the_way_back_is_the_card_the_question_was_asked_from(client, user):
    """The question mark sends where it is along as `next`, so the way back lands on the
    card. It is a value from the request, so it is followed only to the page the topic's
    cards are on, with a card's anchor: a link that says "Back to Your details" leads
    nowhere else -- not off this host, and not to signing out or deleting the account."""
    client.force_login(user)
    address = reverse("core:help_topic", args=["telephone-numbers"])
    home = reverse("accounts:profile")

    def way_back(next_address: str) -> tuple[str, str]:
        html = client.get(address, {"next": next_address}).content.decode()
        link = re.search(r'<a href="([^"]*)"[^>]*data-help-back[^>]*>(.*?)</a>', html, re.S)
        return link.group(1), " ".join(re.sub(r"<svg.*?</svg>", "", link.group(2)).split())

    assert way_back(f"{home}#section-phones") == (f"{home}#section-phones", "Back to Your details")
    assert way_back(home) == (home, "Back to Your details")
    for elsewhere in (
        "/jobs/contacts/",
        reverse("account_logout"),
        reverse("accounts:delete"),
        "/export/download/",
        f"{home}?then=/accounts/logout/#section-phones",
        "https://evil.example/accounts/profile/",
        "//evil.example/accounts/profile/",
    ):
        assert way_back(elsewhere) == (home, "Back to Your details"), elsewhere


# ------------------------------------------------- what a picture may be, from the code


def picture(kind: str, **saved) -> bytes:
    """A small picture of one kind, as the bytes a file of that kind holds."""
    out = io.BytesIO()
    image = Image.new("RGB", (40, 30), "teal")
    image.save(out, format=kind, **saved)
    return out.getvalue()


def refusal_of(user, upload) -> str:
    """What the form says about one upload, or nothing where it is accepted."""
    form = ProfileForm(
        {"first_name": "Alex", "last_name": "Morgan"}, {"picture": upload}, instance=user.profile
    )
    return "" if form.is_valid() else " ".join(form.errors.get("picture", ["(another field)"]))


def test_the_pictures_help_states_what_the_code_enforces(client, user, monkeypatch):
    """The formats and the two sizes are read from `accounts.avatars`, where the form and
    the decoder refuse by them. Changed there, they change in the card's help, under the
    upload box and in both refusals, and nowhere do they have to be changed by hand."""
    limits = avatars.limits()
    assert limits == {"formats": "PNG, JPEG, WebP or GIF", "megabytes": "5", "megapixels": "40"}

    client.force_login(user)
    address = reverse("core:help_topic", args=["your-picture"])
    said = help_text_of(client.get(address).content.decode())
    assert "has to be PNG, JPEG, WebP or GIF, of no more than 5 MB" in said
    assert "no more than 40 megapixels" in said
    assert ProfileForm(instance=user.profile).fields["picture"].help_text == (
        "PNG, JPEG, WebP or GIF, up to 5 MB."
    )

    monkeypatch.setattr(avatars, "MAX_UPLOAD_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(avatars, "MAX_PIXELS", 12_000_000)
    monkeypatch.setattr(avatars, "ALLOWED_TYPES", {"image/png": "PNG", "image/avif": "AVIF"})

    said = help_text_of(client.get(address).content.decode())
    assert "has to be PNG or AVIF, of no more than 2 MB" in said
    assert "no more than 12 megapixels" in said
    assert "5 MB" not in said and "JPEG" not in said
    assert ProfileForm(instance=user.profile).fields["picture"].help_text == (
        "PNG or AVIF, up to 2 MB."
    )
    # And the drawer on the card says what the page says.
    assert help_text_of(card(details(client, user), "section-picture")) == said


def test_a_limit_that_is_not_a_whole_number_is_written_as_it_is(client, user, monkeypatch):
    """Two and a half megabytes is "2.5 MB" in the help, under the box and in the
    refusal of a file over it, and never "2 MB" -- a floor that told somebody a 2.4 MB
    picture was too big. In French the decimal is written the French way."""
    monkeypatch.setattr(avatars, "MAX_UPLOAD_BYTES", int(2.5 * 1024 * 1024))
    monkeypatch.setattr(avatars, "MAX_PIXELS", 12_500_000)
    client.force_login(user)

    said = help_text_of(
        client.get(reverse("core:help_topic", args=["your-picture"])).content.decode()
    )
    assert "of no more than 2.5 MB and no more than 12.5 megapixels" in said
    assert ProfileForm(instance=user.profile).fields["picture"].help_text.endswith("up to 2.5 MB.")
    three = SimpleUploadedFile("me.png", b"0" * (3 * 1024 * 1024), content_type="image/png")
    assert refusal_of(user, three) == "That picture is over 2.5 MB. A smaller one, please."

    with translation.override("fr-FR"):
        assert avatars.limits()["megabytes"] == "2,5"


def test_what_a_picture_is_decides_and_not_what_the_upload_says(user):
    """The kind of file is what the image library reads from its bytes. A BMP or a TIFF
    sent as a PNG is still a BMP or a TIFF, and an SVG is no picture the library reads at
    all: each is refused with the formats' sentence. A PNG sent with no useful type is a
    PNG, and a phone's multi-picture JPEG -- which the library calls MPO -- is a JPEG."""
    refused = "That is not a kind of picture Postulo keeps. Use PNG, JPEG, WebP or GIF."
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'
    for name, data in (
        ("me.png", picture("BMP")),
        ("me.png", picture("TIFF")),
        ("me.png", svg),
        ("me.svg", svg),
        ("me.png", b"not really png bytes"),
    ):
        upload = SimpleUploadedFile(name, data, content_type="image/png")
        assert refusal_of(user, upload) == refused, name

    for name, data, said in (
        ("me.png", picture("PNG"), "application/octet-stream"),
        (
            "me.jpg",
            picture("MPO", save_all=True, append_images=[Image.new("RGB", (40, 30))]),
            "image/jpeg",
        ),
        ("me.webp", picture("WEBP"), "image/webp"),
    ):
        upload = SimpleUploadedFile(name, data, content_type=said)
        assert refusal_of(user, upload) == "", name


def test_what_the_help_promises_is_what_the_form_accepts(user):
    """The other half: the limit the help states is the one an upload is refused by. A file
    a byte over is turned away, and the refusal names the same size the help does."""
    megabytes = avatars.limits()["megabytes"]
    too_big = SimpleUploadedFile(
        "me.png", b"\x89PNG" + b"0" * avatars.MAX_UPLOAD_BYTES, content_type="image/png"
    )
    assert f"{megabytes} MB" in refusal_of(user, too_big)

    wrong_kind = SimpleUploadedFile("me.bmp", b"BM", content_type="image/bmp")
    said = refusal_of(user, wrong_kind)
    for name in avatars.ALLOWED_TYPES.values():
        assert name in said, name


# ------------------------------------------------------------- the cards of Your details


def test_every_card_has_its_question_mark_its_sentence_and_its_drawer(client, user):
    """A link to the topic's page, named by the card; the one sentence, which the link is
    described by; and the drawer the link opens where a script runs. The three cards of
    links share a topic, so they share one drawer, drawn once."""
    html = details(client, user)

    for anchor, slug in CARDS.items():
        part = card(html, anchor)
        mark = re.search(r"<a\b[^>]*data-help-mark[^>]*>", part)
        assert mark, f"{anchor} has no question mark"
        mark = mark.group(0)
        address = reverse("core:help_topic", args=[slug])
        assert f'href="{address}?next=%2Faccounts%2Fprofile%2F%23{anchor}"' in mark, anchor
        assert f'data-opens-dialog="help-{slug}"' in mark, anchor
        assert 'target="_blank"' in mark, "with scripts off it opens a new tab"

        assert "aria-haspopup" not in mark, "without a script it is a link, and says so"

        sentence = re.search(r'aria-describedby="([^"]+)"', mark).group(1)
        described_by = paragraph(part, sentence)
        assert "data-tooltip" in described_by and "data-help-summary" in described_by
        assert html.count(f'id="{sentence}"') == 1, anchor
        assert 'data-icon="circle-question-mark"' in part, anchor

    for slug in set(CARDS.values()):
        assert html.count(f'<dialog id="help-{slug}"') == 1, slug
    assert html.count("data-help-mark") == len(CARDS)


def test_a_card_is_named_by_its_own_name_and_shows_its_own_sentence(client, user):
    """The cards of links share their help, and each still says which card it is: the
    question mark is "Help: Code repositories", with that card's sentence."""
    html = details(client, user)

    for anchor, name, opening in (
        ("section-links-social", "Social profiles", "A LinkedIn, a Mastodon, a Bluesky"),
        ("section-links-repository", "Code repositories", "A profile on a forge"),
        ("section-links-website", "Websites", "A personal site, a blog, a portfolio"),
    ):
        part = card(html, anchor)
        assert f'<span class="sr-only">Help: {name}</span>' in part
        sentence = re.search(r"<p[^>]*data-help-summary[^>]*>(.*?)</p>", part, re.S).group(1)
        assert sentence.strip().startswith(opening)

    drawer = html[html.index('<dialog id="help-links"') :]
    assert "Help: Social profiles, code repositories and websites" in drawer[:600]


def test_the_drawer_says_what_the_page_says(client, user):
    """One template draws both, through one tag, so they cannot say different things."""
    html = details(client, user)

    for slug in help_topics.TOPICS:
        drawer = html[html.index(f'<dialog id="help-{slug}"') :]
        drawer = drawer[: drawer.index("</dialog>")]
        page = client.get(reverse("core:help_topic", args=[slug])).content.decode()
        assert help_text_of(drawer) == help_text_of(page), slug


def test_the_sentence_of_your_name_is_what_its_two_menus_are_described_by(client, user):
    """The card's one sentence is the description of both menus (#309) and of the question
    mark: one paragraph, named three times."""
    html = details(client, user)
    part = card(html, "section-name")

    assert html.count('id="name-addressing-help"') == 1
    assert "data-tooltip" in paragraph(part, "name-addressing-help")
    assert part.count('aria-describedby="name-addressing-help"') == 3
    assert "Neither is printed anywhere unless a CV says so" in help_text_of(
        html[html.index('<dialog id="help-your-name"') :]
    )


# ----------------------------------------------------- which help is a tooltip, and which stays


def numbered(user) -> None:
    PhoneNumber.objects.create(
        owner=user, holder=user.profile, number="+351912345678", is_primary=True
    )
    PersonIdentifier.objects.create(profile=user.profile, scheme="wikidata", value="Q95")


def test_what_a_field_is_for_is_its_tooltip_and_what_may_be_typed_stays(client, user):
    """The sentence is the same paragraph with the same id, named by the control as before:
    a tooltip only where a script makes it one. A format stays under its box, and so does
    what a tick box does when it is ticked."""
    numbered(user)
    html = details(client, user)

    tooltips = (
        "id_headline_helptext",
        "id_record_language_helptext",
        "id_phone_numbers-0-label_helptext",
        "id_phone_numbers-0-number_helptext",
        "id_addresses-0-label_helptext",
        "id_identifiers-0-scheme_helptext",
        "id_identifiers-0-value_helptext",
        "id_identifiers-0-label_helptext",
    )
    for helped in tooltips:
        assert "data-tooltip" in paragraph(html, helped), helped
        control = helped.removesuffix("_helptext")
        assert re.search(rf'aria-describedby="[^"]*\b{re.escape(helped)}\b', html), control

    assert "data-tooltip" not in paragraph(html, "id_picture_helptext")
    assert "PNG, JPEG, WebP or GIF, up to 5 MB." in html
    gravatar = re.search(r'<span[^>]*id="id_use_gravatar_helptext"[^>]*>', html).group(0)
    assert "data-tooltip" not in gravatar
    # What may be typed into an identifier, and what a country's rules refuse: in sight.
    assert "An address on ORCID, Wikidata or LinkedIn can be pasted whole" in html
    assert "data-rules-help" in html and "data-tooltip" not in re.search(
        r"<p[^>]*data-rules-help[^>]*>", html
    ).group(0)


def test_the_language_of_the_record_says_what_it_is_for(client, user):
    """Its sentence was given to the field after the form had already made the field's
    bound half, and was never drawn."""
    html = details(client, user)

    assert "Which language your experience, education and projects are typed in." in html
    select = re.search(r'<select name="record_language"[^>]*>', html).group(0)
    assert 'aria-describedby="id_record_language_helptext"' in select


def rows(prefix: str, *forms: dict) -> dict:
    """A block of new rows as a page posts it: the management form and each row."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(forms)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, form in enumerate(forms):
        data.update({f"{prefix}-{index}-{name}": value for name, value in form.items()})
    return data


#: One refusal on each part of the page, and the words that say it was refused.
REFUSALS = {
    "the name": ({"last_name": ""}, "This field is required."),
    "a telephone row": (
        rows("phone_numbers", {"kind": "", "label": "", "number_0": "PT", "number_1": "12"}),
        "That number is too short for Portugal.",
    ),
    "a link row": (
        rows(
            "social_profiles", {"service": "linkedin", "label": "", "url": "https://example.com/me"}
        ),
        "That does not look like an address on LinkedIn.",
    ),
    "an address row": (
        rows(
            "addresses", {"kind": "other", "label": "", "municipality": "Lisboa", "country": "PT"}
        ),
        "Say what this address is.",
    ),
    "an identifier row": (
        rows("identifiers", {"scheme": "wikidata", "value": "not an item", "label": ""}),
        "Wikidata: that is not the usual form",
    ),
}


@pytest.mark.parametrize("refused", sorted(REFUSALS))
def test_a_page_with_anything_refused_has_no_tooltip_at_all(client, user, refused):
    """Refused is decided for the page, not for one form (#302): a refusal on one row lies
    under the next row's help, and a card's sentence is the tooltip of fields far from
    where the refusal is. So while anything on the page is refused, no help on it is a
    tooltip, and every card's one sentence -- the question mark's, *Your name*'s that its
    two menus are described by -- is under its title, where it is with scripts off."""
    numbered(user)
    client.force_login(user)
    data, said = REFUSALS[refused]

    html = client.post(
        reverse("accounts:profile"), {"first_name": "Alex", "last_name": "Morgan", **data}
    ).content.decode()

    assert said in html, f"{refused} was not refused"
    assert "data-tooltip" not in html
    for summary in re.findall(r"<p[^>]*data-help-summary[^>]*>", html):
        assert "data-tooltip" not in summary
    assert "data-tooltip" not in paragraph(html, "name-addressing-help")
    assert 'aria-describedby="name-addressing-help"' in html


def test_a_page_with_nothing_refused_has_its_tooltips(client, user):
    """The other side of the switch: saved without a fault, the page is drawn with its
    tooltips again."""
    numbered(user)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-tooltip" in paragraph(html, "id_headline_helptext")
    assert "data-tooltip" in paragraph(html, "name-addressing-help")


def test_what_to_type_stays_in_sight(client, user):
    """An instruction is read before the mistake, so it is no tooltip (#302): *Location*
    while there is no address to derive a line from, the telephone row a new number is
    typed into and the single box that stands in for the rows, and on each card of links
    what to do with an address. *Location* with a line in grey only explains it, and a
    stored number's sentence only says how it is kept: those are tooltips."""
    from postulo.core.models import PostalAddress

    numbered(user)
    html = details(client, user)
    assert "data-tooltip" not in paragraph(html, "id_location_helptext")
    assert "City and country, as it should appear on a CV." in html
    assert "data-tooltip" in paragraph(html, "id_phone_numbers-0-number_helptext")
    assert "data-tooltip" not in paragraph(html, "id_phone_numbers-1-number_helptext")

    said = {
        kind: " ".join(text.split())
        for kind, text in re.findall(
            r'data-web-links="(\w+)".*?<p[^>]*data-links-how[^>]*>(.*?)</p>', html, re.S
        )
    }
    paste = (
        "Paste an address without choosing a service, and saving chooses it where the "
        "address is on one Postulo knows."
    )
    assert said == {
        "social": paste,
        "repository": paste,
        "website": "Left blank, the name is the address's host.",
    }

    PostalAddress.objects.create(
        owner=user, holder=user.profile, municipality="Porto", country="PT", is_primary=True
    )
    assert "data-tooltip" in paragraph(details(client, user), "id_location_helptext")


def test_the_single_telephone_box_keeps_its_sentence_in_sight(client, user):
    """While *Several telephone numbers* is off, the one box is where a new number is
    typed, so what a + does is in sight under it."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    html = details(client, user)

    assert "data-tooltip" not in paragraph(html, "id_phone_helptext")
    assert "data-tooltip" in paragraph(html, "id_headline_helptext")


# ----------------------------------------------------------- the help is true (M4)


def test_the_help_says_what_the_code_does(client, user):
    """Each sentence the review found untrue, as the code now has it said."""
    from postulo.accounts.forms import PersonIdentifierForm

    client.force_login(user)

    def topic_text(slug: str) -> str:
        page = client.get(reverse("core:help_topic", args=[slug])).content.decode()
        return help_text_of(page)

    # Documents print a town and a country and never a street: the card's sentence said
    # "a form or a formal letter carries that one", the address.
    postal = str(help_topics.topic("postal-addresses").summary)
    assert "never print a street" in postal and "formal letter" not in postal
    # Nine refusals, not three: the help says a refusal says why, and lists none.
    phones = topic_text("telephone-numbers")
    assert "the refusal says why" in phones and "too short" not in phones
    # Editing the number that gets you back in makes it another number (core.models).
    assert "Changing its digits makes it another number" in phones
    # The fold is under the rows on Your details: the Kind help names no direction.
    kind = str(PersonIdentifierForm.base_fields["scheme"].help_text)
    assert "above" not in kind and "below" not in kind and "What goes where" in kind
    # Only some registers lift an identifier out of an address, and the code says which.
    identifiers = topic_text("identifiers")
    assert "An address on ORCID, Wikidata or LinkedIn can be pasted whole" in identifiers
    assert "ResearcherID" not in identifiers
    # https:// is put in front, and a named service drops the name.
    links = topic_text("links")
    assert "with https:// put in front where you left it out" in links
    assert "Letters print none." in links
    # The single box stands in for the primary, and edits it.
    contact = topic_text("contact-block")
    assert "what you type there replaces it, and emptying the box removes it" in contact


def test_the_cards_help_is_no_longer_under_the_rows(client, user):
    """What the paragraphs under the rows said is the card's help now, and is said once."""
    numbered(user)
    html = details(client, user)
    page = html[: html.index("<dialog")]

    for moved in (
        "is kept once on this instance.",
        "Postulo never opens a link on its own",
        "Unlike a telephone number, an address is not kept once",
        "A name is not an identity",
        "The lists are the ones in use in the language of your career record",
    ):
        assert moved not in page, moved
    assert "A number in its international form is kept once on this instance." in html
    assert "Postulo never opens a link on its own" in html


def test_a_contacts_form_keeps_its_help_in_sight(client, user):
    """The same rows on another page: no question mark, no tooltip, and the sentences
    where they were. A page takes the new shape when it is given it, not by accident --
    and is drawn as it was before #302 at every place a switch was added: each switch is
    written on the line of what it follows, so it leaves no blank line behind. The sentence
    under the telephone legend is the topic's own summary, one string in one place."""
    company = Company.objects.create(owner=user, name="Aperture Science")
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    PhoneNumber.objects.create(owner=user, holder=contact, number="+351912345679", is_primary=True)
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()

    assert "data-help-mark" not in html and "data-tooltip" not in html
    assert "<dialog" not in html
    assert 'id="id_phone_numbers-0-number_helptext"' in html
    summary = str(help_topics.topic("telephone-numbers").summary)
    for seam in (
        # Under each legend: the sentence, as it was.
        '<legend class="label px-1">Telephone numbers</legend>\n'
        '  <p class="mb-3 text-sm text-ink-500 dark:text-ink-400">\n'
        f"    {summary}\n  </p>\n"
        '  <input type="hidden" name="phone_numbers-TOTAL_FORMS"',
        '<legend class="label px-1">Social profiles</legend>\n'
        '    <p class="mb-3 text-sm text-ink-500 dark:text-ink-400">A LinkedIn, a Mastodon',
        # Under the rows: the paragraphs, as they were.
        '  </ol>\n  \n  <p class="mt-3 text-xs text-ink-500 dark:text-ink-400">\n'
        "    A number is kept once on this instance.",
        "  </p>\n  \n</fieldset>",
        '    </ol>\n    \n    <p class="mt-3 text-xs text-ink-500 dark:text-ink-400">',
        "    </p>\n  </fieldset>\n",
    ):
        assert seam in html, seam


def test_the_gallerys_question_mark_is_named_for_its_own_card(client):
    """It borrows the help of *Your picture*, and is named for the card it is on."""
    from django.contrib.auth import get_user_model

    staff = get_user_model().objects.create_user(
        email="admin@example.org", password="a-fairly-long-password-42", is_staff=True
    )
    client.force_login(staff)

    html = client.get(reverse("server:design")).content.decode()

    mark = re.search(r'id="design-help-card".*?<a\b[^>]*data-help-mark[^>]*>(.*?)</a>', html, re.S)
    assert '<span class="sr-only">Help: A card with a question mark</span>' in mark.group(1)
