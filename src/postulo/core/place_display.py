"""How a place is shown: the city alone while every place is in one country (#1249).

Where every company a person can see is in France, ``Paris, France`` on each row is the same
word to skip a hundred times. So a place is shown as ``Paris`` while the countries of the
person's places are one, and as ``Paris, France`` the moment a second one appears, so a row
is never ambiguous.

**Display only.** An address keeps its country; documents, exports, the CSV, the archive,
shares and the API's data fields never go through here and always carry it. What does go
through here is every page that prints a place for a person to read: the Companies list
and cards, a company's page, its map and the command bar.

**The set of countries** is the countries of the addresses on the companies the person can
see (their own, shared with them, in a group) plus their own address. An empty country, or
one the list does not know, is no country at all, so one company with none never forces the
country onto the others. Worked out once per request and kept on the user object, which is
the request's: the next request asks again, so a company added or removed is seen at once.

**Three settings** (``Profile.show_country``): *automatically* is the rule above, *always*
and *never* are the person's own word for it.
"""

from __future__ import annotations

from django.apps import apps

from postulo.core import languages, phones

AUTO = "auto"
ALWAYS = "always"
NEVER = "never"

_CACHE = "_postulo_shows_country"


def _own(user) -> str:
    profile = getattr(user, "profile", None)
    return getattr(profile, "show_country", AUTO) or AUTO


def countries_in_play(user) -> frozenset[str]:
    """The countries of the places this person deals with, as alpha-2 codes."""
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    from django.contrib.contenttypes.models import ContentType

    from .models import PostalAddress

    Company = apps.get_model("jobs", "Company")
    visible = Company.objects.visible_to(user).values("pk")
    codes = set(
        PostalAddress.objects.filter(
            content_type=ContentType.objects.get_for_model(Company), object_id__in=visible
        )
        .exclude(country="")
        .values_list("country", flat=True)
        .distinct()
    )
    profile = getattr(user, "profile", None)
    if profile is not None and getattr(profile, "pk", None):
        codes.update(
            PostalAddress.objects.filter(
                content_type=ContentType.objects.get_for_model(profile), object_id=profile.pk
            )
            .exclude(country="")
            .values_list("country", flat=True)
        )
    return frozenset(code for code in codes if code in phones.BY_CODE)


def shows_country(user) -> bool:
    """Whether a place is shown with its country for this person, in this request."""
    known = getattr(user, _CACHE, None)
    if known is not None:
        return known
    choice = _own(user)
    if choice == ALWAYS:
        answer = True
    elif choice == NEVER:
        answer = False
    else:
        answer = len(countries_in_play(user)) != 1
    try:
        setattr(user, _CACHE, answer)
    except AttributeError:  # pragma: no cover - a user that cannot carry an attribute
        pass
    return answer


def country_name(code: str) -> str:
    """A country's name in the reader's language, English where the language lacks it."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    try:
        from babel import Locale

        name = Locale.parse(languages.current(), sep="-").territories.get(code)
    except Exception:  # an unknown locale: the English name below
        name = None
    return name or phones.country_name(code)


def place(town: str, country: str, user) -> str:
    """A town and a country code as one line to read: with the country only where it is shown."""
    parts = [(town or "").strip()]
    if shows_country(user) or not parts[0]:
        parts.append(country_name(country))
    return ", ".join(part for part in parts if part)


def address_place(address, user) -> str:
    """A postal address's town and country, as `place` writes them."""
    return place(address.municipality, address.country, user)


def address_line(address, user, separator: str = ", ") -> str:
    """`PostalAddress.one_line`, with the country left out where it is not shown."""
    if shows_country(user):
        parts = [
            address.street,
            address.postcode,
            address.municipality,
            address.region,
            country_name(address.country),
        ]
    else:
        parts = [address.street, address.postcode, address.municipality, address.region]
    return separator.join(part for part in (p.strip() for p in parts if p) if part)


def company_place(company, user) -> str:
    """Where a company is: its primary address's town and country, as `place` writes them."""
    town, country = company.location_parts()
    return place(town, country, user)


def _country_words(user) -> set[str]:
    """The names a typed place may end in that this person need not be told."""
    if _own(user) == NEVER:
        codes = set(phones.BY_CODE)
    else:
        codes = countries_in_play(user)
    words = set()
    for code in codes:
        words.add(phones.BY_CODE[code][2].casefold())
        words.add(country_name(code).casefold())
    return words


def typed_place(text: str, user) -> str:
    """A place somebody typed -- a listing's ``Paris, France`` -- without its country, if shown so.

    Only a last part that is the name of a country this person need not be told, so that
    ``Berlin, Germany`` stays whole beside French companies, and ``Remote`` is left as it is.
    """
    text = (text or "").strip()
    if not text or shows_country(user):
        return text
    head, comma, tail = text.rpartition(",")
    if comma and head.strip() and tail.strip().casefold() in _country_words(user):
        return head.strip()
    return text
