"""Country flags: an image, because the emoji was not one on Windows.

A flag emoji is not a character. It is two *regional indicator* code points -- U+1F1F5 and
U+1F1F9 for Portugal -- and a font is invited, never required, to draw the pair as a flag.
Segoe UI Emoji has never accepted the invitation, so Windows draws exactly what the code
points say: `PT`. Both places Postulo used flags carried a comment predicting this and
calling it a legible fallback; it was reported as a bug by the maintainer, on Windows,
which is the answer to whether it reads as a fallback (#88).

What these check is the contract the rest of the interface leans on: that the file exists
for every country the telephone field can offer, that the tag refuses anything that is not a
country, and that a language with no flag renders as nothing rather than as a broken image.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template
from django.urls import reverse

from postulo.core import languages, phones
from postulo.core.templatetags import postulo as tags

REPO = Path(__file__).resolve().parents[1]
LIST = REPO / "assets" / "flags.txt"


def listed() -> list[str]:
    return [
        line.replace("#", " ").split()[0]
        for line in LIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def render(template: str, **context) -> str:
    return Template("{% load postulo %}" + template).render(Context(context))


# ------------------------------------------------------------------- the set


def test_the_list_holds_every_country_the_telephone_field_offers():
    """Any country in the list can be chosen, and the chosen one's flag is shown.

    A country added to `phones.COUNTRIES` without running `npm run sync:flags` would be a
    country whose flag silently does not appear, which is the sort of thing nobody notices
    until it is somebody's own.

    The list may hold more than the telephone field offers: a subdivision like `es-ct` is
    there for a language, never for a dialling code.
    """
    assert {code.lower() for code, _, _ in phones.COUNTRIES} <= set(listed())


def test_nothing_extra_is_listed_that_no_language_asked_for():
    """The subdivisions earn their place one at a time, each because a language needs it."""
    countries = {code.lower() for code, _, _ in phones.COUNTRIES}
    wanted = {country.lower() for country in languages.FLAG_COUNTRIES.values()}
    assert set(listed()) - countries <= wanted


def test_every_listed_flag_is_actually_there():
    missing = [code for code in listed() if not (tags.FLAG_DIR / f"{code}.svg").is_file()]
    assert not missing, f"run `npm run sync:flags`: {missing}"


def test_nothing_is_there_that_is_not_listed():
    """The committed set is the list and nothing else, exactly as with the icons."""
    keep = {f"{code}.svg" for code in listed()} | {"LICENSE.txt"}
    extra = sorted(path.name for path in tags.FLAG_DIR.iterdir() if path.name not in keep)
    assert not extra, f"not in {LIST.name}: {extra}"


def test_the_licence_travels_with_the_artwork():
    """flag-icons is MIT, which asks that the notice go wherever the files go."""
    notice = (tags.FLAG_DIR / "LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in notice
    assert "Panayiotis Lipiridis" in notice


def test_every_language_with_a_flag_has_one_to_show():
    """`FLAG_COUNTRIES` names a country; that country has to be one Postulo ships."""
    for code, country in languages.FLAG_COUNTRIES.items():
        assert country.lower() in listed(), f"{code} points at {country}"


def test_the_two_maps_never_contradict_each_other():
    """The flag beside a language and the country its telephone field starts on are two
    different questions, and both are answered by hand.

    They may legitimately differ -- a language with no uncontested home gets no flag while
    the telephone field still has to start somewhere -- but where both have an opinion they
    must agree, or the same page says two things about one language. This is what the emoji
    map used to duplicate, in a codebase whose own telephone field refuses to store a
    country column for exactly that reason.
    """
    for code, country in languages.FLAG_COUNTRIES.items():
        other = phones.FROM_LANGUAGE.get(code)
        # A subdivision agrees with the country it is part of: Catalan's flag is Catalonia
        # and its dialling code is Spain's, and both of those are right.
        assert other in (None, country, country.split("-")[0]), (
            f"{code}: flag says {country}, telephone says {other}"
        )


# ------------------------------------------------------------------- the tag


def test_the_tag_draws_a_flag():
    html = render('{% flag "pt" %}')
    assert 'class="flag"' in html
    assert 'data-flag="pt"' in html
    assert "/flags/pt" in html and html.endswith(">")
    # Stated, so the row does not jump when the image lands.
    assert 'width="20"' in html and 'height="15"' in html


def test_the_tag_takes_a_country_however_it_is_written():
    assert render('{% flag "PT" %}') == render('{% flag " pt " %}') == render('{% flag "pt" %}')


def test_the_tag_draws_a_subdivision_too():
    """Catalan is at home in Catalonia, whose flag is `es-ct` and not Spain's."""
    html = render('{% flag "ES-CT" %}')
    assert 'data-flag="es-ct"' in html
    assert "/flags/es-ct" in html


def test_a_flag_is_decoration_unless_it_is_given_words():
    """Beside a name somebody can already read, a flag adds nothing a screen reader should
    repeat -- "Portugal flag, portugues (Portugal)" is worse than silence."""
    assert 'alt="" aria-hidden="true"' in render('{% flag "pt" %}')

    alone = render('{% flag "pt" label="Portugal" %}')
    assert 'alt="Portugal"' in alone
    assert "aria-hidden" not in alone


@pytest.mark.parametrize(
    "country", ["", "   ", "x", "xyz", "zz", "../icons/mail", "p t", "1a", None]
)
def test_nothing_is_drawn_for_anything_that_is_not_a_country(country):
    """Empty is a real answer: a language with no uncontested home gets no flag, and the
    row simply closes up. Nothing here may become a path, either -- an unrecognised code
    can arrive from a form field, and under the manifest storage production uses, asking
    for a file that was never collected raises rather than returning a dead link."""
    assert render("{% flag country %}", country=country) == ""
    assert tags.flag_url(country) == ""


def test_the_url_is_the_one_the_static_machinery_gives():
    """Never a string built by hand: files are served under a content hash, and a URL
    assembled from a pattern would be right in development and wrong in production."""
    from django.templatetags.static import static

    assert tags.flag_url("pt") == static("flags/pt.svg")


# ------------------------------------------------------- where they are shown


@pytest.mark.django_db
def test_the_telephone_field_shows_the_chosen_country_without_a_script(client, user):
    """An `<option>` holds text and nothing else, so the flag sits over the closed select.

    The server draws it, and the script only keeps it in step: with JavaScript blocked the
    field still shows the flag of the country it loaded with, which is the true answer
    until the form is saved.
    """
    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    client.force_login(user)

    html = client.get(reverse("jobs:contact_create")).content.decode()

    holder = re.search(r"<span[^>]*data-phone-flag[^>]*>(.*?)</span>", html, re.S)
    assert holder, "no flag beside the country chooser"
    assert 'data-flag="pt"' in holder.group(1)


@pytest.mark.django_db
def test_every_country_option_carries_its_own_flag_for_the_script(client, user):
    """There is no pattern a script could build a URL from -- each file has its own content
    hash -- so each option carries the answer."""
    client.force_login(user)
    html = client.get(reverse("jobs:contact_create")).content.decode()

    options = re.findall(r'<option value="([A-Z]{2})" data-flag="([^"]*)"', html)
    assert len(options) == len(phones.COUNTRIES)
    for code, url in options:
        assert url.endswith(".svg"), f"{code} has no flag"
        assert f"/flags/{code.lower()}" in url


@pytest.mark.django_db
def test_no_option_label_carries_a_flag_any_more(client, user):
    """It could never have been an image, and on Windows it was two letters."""
    client.force_login(user)
    html = client.get(reverse("jobs:contact_create")).content.decode()

    labels = re.findall(r"<option [^>]*>([^<]*)</option>", html)
    assert labels, "no options at all"
    indicators = [label for label in labels if any(ch in label for ch in "\U0001f1e6\U0001f1ff")]
    assert not indicators, indicators


def test_the_stylesheet_keeps_a_white_edged_flag_from_disappearing():
    """Poland, Japan, Finland and half of Austria reach their own edge in white, and a white
    card behind them takes that edge away. An outline rather than an inset shadow, because a
    replaced element paints its image over an inset shadow and under nothing at all."""
    css = (REPO / "src" / "postulo" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    rule = _block(css, ".flag {")
    assert rule, "no .flag rule in the compiled stylesheet"
    assert "outline" in rule
    assert re.search(r'\[data-theme="dark"\][^}]*\{[^}]*outline-color', rule, re.S), (
        "the outline does not change in the dark theme"
    )


def _block(css: str, opener: str) -> str:
    """One rule and everything nested inside it, found by counting braces.

    Since #159 the committed stylesheet is written out rather than minified, and Tailwind
    writes the dark-theme branch *nested* inside the rule rather than flattened into a
    second selector. Both facts asserted above are unchanged; where to read them is not,
    and an expression that stops at the first `}` now stops in the middle of the rule.
    """
    start = css.find(opener)
    if start < 0:
        return ""
    depth = 0
    for index in range(start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start : index + 1]
    return ""


# --------------------------------------------------------- postal addresses (#214)


@pytest.mark.django_db
def test_an_address_row_shows_the_chosen_countrys_flag_without_a_script(client, user):
    """As beside a telephone number (#88): the server draws the flag of the country the row
    loaded with, and every option carries its own flag's URL for the script."""
    from postulo.core.models import PostalAddress

    PostalAddress.objects.create(
        owner=user, holder=user.profile, kind="home", street="Rua A", country="PT"
    )
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    holders = re.findall(r"<span[^>]*data-flag-holder[^>]*>(.*?)</span>", html, re.S)
    assert holders, "no flag beside the country chooser"
    assert any('data-flag="pt"' in holder for holder in holders), "the stored country's flag"
    select = re.search(r'<select[^>]*name="[^"]*-country"[^>]*>(.*?)</select>', html, re.S)
    assert select and "data-flag-select" in select.group(0)
    options = re.findall(r'<option value="([A-Z]{2})"([^>]*)>', select.group(1))
    assert len(options) == len(phones.COUNTRIES)
    for code, attrs in options:
        assert f"/flags/{code.lower()}" in attrs, code


@pytest.mark.django_db
def test_an_address_row_with_no_country_draws_no_flag(client, user):
    from postulo.core.models import PostalAddress

    PostalAddress.objects.create(
        owner=user, holder=user.profile, kind="home", street="Rua A", country=""
    )
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    holders = re.findall(r"<span[^>]*data-flag-holder[^>]*>(.*?)</span>", html, re.S)
    assert holders and all("<img" not in holder for holder in holders)


# ------------------------------------------------- Server settings -> Defaults (#208)


@pytest.fixture
def administrator(db, django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )


@pytest.mark.django_db
def test_every_offered_language_with_a_home_draws_its_flag(client, administrator):
    """The checkbox grid looks like the locale picker: flag beside every language that has
    one, outside the `lang` span, decorative."""
    from postulo.core import languages

    client.force_login(administrator)
    html = client.get(reverse("server:defaults")).content.decode()

    rows = re.findall(r"<label[^>]*>(.*?)</label>", html, re.S)
    by_code = {}
    for row in rows:
        code = re.search(r'name="offered_languages" value="([^"]+)"', row)
        if code:
            by_code[code.group(1)] = row
    assert len(by_code) > 30, "the grid"

    flagged = [code for code in by_code if languages.flag_country(code)]
    assert flagged, "no language with a flag?"
    for code in flagged:
        country = languages.flag_country(code).lower()
        assert f'data-flag="{country}"' in by_code[code], code
        assert 'aria-hidden="true"' in by_code[code], code
        # Outside the span marked as being in that language.
        assert re.search(rf'data-flag[^<]*<[^<]*<span lang="{code}"', by_code[code]), code

    homeless = [code for code in by_code if not languages.flag_country(code)]
    for code in homeless:
        assert "<img" not in by_code[code], f"{code} has no single home and should show no flag"


@pytest.mark.django_db
def test_the_default_language_shows_the_chosen_flag_without_a_script(client, administrator):
    """As the telephone field does (#88): the server draws the saved language's flag over
    the closed select, and each option carries its own flag's URL for the script."""
    from postulo.core.models import SiteSettings

    row = SiteSettings.get()
    row.default_language = "pt-pt"
    row.save()
    client.force_login(administrator)

    html = client.get(reverse("server:defaults")).content.decode()

    holder = re.search(r"<span[^>]*data-flag-holder[^>]*>(.*?)</span>", html, re.S)
    assert holder, "no flag beside the language chooser"
    assert 'data-flag="pt"' in holder.group(1)

    select = re.search(r'<select[^>]*name="default_language"[^>]*>(.*?)</select>', html, re.S)
    assert select and "data-flag-select" in select.group(0)
    options = re.findall(r'<option value="([^"]+)"([^>]*)>', select.group(1))
    assert len(options) > 30
    for code, attrs in options:
        assert f'lang="{code}"' in attrs, code
        assert "data-flag=" in attrs, code
    assert 'value="pt-pt"' in select.group(1) and "/flags/pt" in select.group(1)
    labels = re.findall(r"<option [^>]*>([^<]*)</option>", select.group(1))
    assert not any(ch in label for label in labels for ch in "\U0001f1e6\U0001f1ff")


# ------------------------------------------------- a document's language (#280)


def a_cv(user, **fields):
    from postulo.documents.models import CV

    return CV.objects.create(owner=user, name=fields.pop("name", "Backend"), **fields)


@pytest.mark.django_db
def test_a_cv_says_which_language_it_is_in(client, user):
    """A flag beside the kind, and the *language's* own name as its words -- never the
    country's, because the flag stands alone on a card with no name beside it."""
    a_cv(user, language="pt-pt")
    client.force_login(user)

    html = client.get(reverse("documents:cv_list")).content.decode()

    image = re.search(r"<img[^>]*data-flag=\"pt\"[^>]*>", html)
    assert image, "no flag on the card"
    assert 'alt="português (Portugal)"' in image.group(0), image.group(0)
    assert "aria-hidden" not in image.group(0), "it stands alone, so it is not decoration"


@pytest.mark.django_db
def test_a_language_with_no_flag_says_its_name_instead(client, user):
    """`flag_country` answers nothing for the dozen with no uncontested home, and no flag
    beats a wrong flag -- so the card reads either way."""
    from postulo.core import languages

    assert languages.flag_country("sw") == "", "Swahili is one of the flagless ones"
    a_cv(user, language="sw")
    client.force_login(user)

    html = client.get(reverse("documents:cv_list")).content.decode()

    assert '<span lang="sw"' in html and "Kiswahili" in html
    assert "data-flag=" not in html, "no flag at all, rather than a wrong one"


@pytest.mark.django_db
def test_a_blank_language_follows_the_profile_and_is_drawn_the_same(client, user):
    """Blank means *follow your profile*, and what reaches the employer is the resolved
    value either way, so it is not drawn differently."""
    user.profile.language = "de"
    user.profile.save(update_fields=["language"])
    cv = a_cv(user, language="")
    client.force_login(user)

    assert cv.effective_language == "de" and cv.language_name == "Deutsch"
    html = client.get(reverse("documents:cv_list")).content.decode()
    assert 'data-flag="de"' in html


@pytest.mark.django_db
def test_a_letter_says_it_too(client, user):
    from postulo.documents.models import CoverLetter

    CoverLetter.objects.create(owner=user, name="Speculative", body="Dear team", language="fr-fr")
    client.force_login(user)

    html = client.get(reverse("documents:letter_list")).content.decode()

    assert 'data-flag="fr"' in html and 'alt="français (France)"' in html


def _queries_for(client, url) -> int:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        client.get(url)
    return len(captured.captured_queries)


@pytest.mark.django_db
@pytest.mark.parametrize("declared,profile", [("pt-pt", ""), ("", "de")])
def test_asking_every_card_its_language_costs_no_query_of_its_own(client, user, declared, profile):
    """Whether the language is the document's own or inherited, the page asks the same
    number of questions of the database however many cards are on it: the resolution is
    cached per document, and the owner's profile is joined rather than fetched a row at a
    time (#280).

    The one case left out is a document with no language whose owner has chosen none
    either, where the last step of the fallback reads the instance's settings row. That
    row is re-read all over the application because nothing caches it, which is #231's
    subject rather than this one.
    """
    user.profile.language = profile
    user.profile.save(update_fields=["language"])
    for number in range(3):
        a_cv(user, name=f"CV {number}", language=declared)
    client.force_login(user)
    url = reverse("documents:cv_list")

    client.get(url)  # let anything cached per process warm up first
    before = _queries_for(client, url)
    for number in range(3, 9):
        a_cv(user, name=f"CV {number}", language=declared)
    after = _queries_for(client, url)

    assert after == before, f"{before} queries for three cards, {after} for nine"
