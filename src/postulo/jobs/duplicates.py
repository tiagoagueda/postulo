"""Is this company, or this person, already in the record under another spelling? (#239)

Companies were matched on a name that was exactly the same, case aside, so *Acme*, *Acme
Ltd* and *ACME GmbH* became three employers, and nothing compared contacts at all. This
answers a question, when a company's or a contact's page is drawn: which of this person's
other records look like the same one, and why.

**It tells and never refuses**, the stance `jobs.known` takes about a posting captured twice
(#178). *Acme Ltd* and *Acme GmbH* may well be two companies -- a British arm and a German
one -- and only the person recording them knows. So nothing here stops a record being
saved, nothing is matched on the way in, and nothing is merged by itself: the page says
what it noticed and offers *Merge*, which shows what would move before it moves anything.

**Computed when the page is drawn, never in the background.** There is no table of
suspected duplicates to keep true, no job that compares everybody with everybody, and
nothing to go stale: a record renamed a moment ago is compared as it now is. It costs a
read of one person's companies or contacts, which a job search counts in dozens.

Four signals, and each is said with the record it matched:

* the name, once its legal form is set aside -- `LEGAL_FORMS` below, the one list;
* the same website domain;
* the same identifier;
* for a contact, the same email address -- and the same name, which for a person has no
  legal form to set aside.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.utils.translation import gettext as _

from . import identifiers
from .models import Company, CompanyIdentifier, Contact

#: The legal forms a company's name is written with, as people write them. **The one
#: list**: every comparison of two names goes through `bare_name`, and `bare_name` reads
#: only this.
#:
#: Written the way they are typed and folded by the same function the names are, so *S.A.*
#: here matches *SA*, *s.a.* and *S. A.* there, and a form of several words -- *Sp. z o.o.*,
#: *S.A. de C.V.* -- is one entry. Not a register of every legal form there is: the ones
#: that turn up beside an employer's name across Europe, and the commonest from beyond it.
#: A form that is missing costs a notice that was not shown, never a record.
LEGAL_FORMS: tuple[str, ...] = (
    # English-speaking
    "Ltd", "Limited", "Co", "Company", "Corp", "Corporation", "Inc", "Incorporated",
    "LLC", "LLP", "LP", "PLC", "Pty", "Pte", "Pvt", "Private Limited", "CIC",
    # German-speaking
    "GmbH", "gGmbH", "mbH", "AG", "KG", "KGaA", "OHG", "GbR", "UG", "UG haftungsbeschränkt",
    "e.V.", "eG", "SE",
    # French-speaking
    "S.A.", "SAS", "SASU", "SARL", "S.à r.l.", "EURL", "SNC", "SCA", "SCS", "SCOP",
    # Portuguese and Brazilian
    "Lda", "Limitada", "Ltda", "Unipessoal", "SGPS", "EIRELI", "S/A",
    # Spanish-speaking
    "S.L.", "S.L.U.", "S.A.U.", "S.L.L.", "S.A. de C.V.", "S. de R.L.", "S. Coop.",
    # Italian
    "S.p.A.", "S.r.l.", "S.r.l.s.", "S.a.s.", "S.n.c.",
    # Dutch and Belgian
    "B.V.", "N.V.", "VOF", "BVBA", "SPRL", "CVBA",
    # Nordic
    "AB", "Aktiebolag", "AS", "A/S", "ASA", "ApS", "Oy", "Oyj", "Abp", "hf", "ehf",
    # Central and eastern Europe
    "Sp. z o.o.", "sp.k.", "sp.j.", "s.r.o.", "a.s.", "spol. s r.o.", "k.s.", "v.o.s.",
    "Kft", "Zrt", "Nyrt", "Bt", "SRL", "d.o.o.", "d.d.", "j.d.o.o.", "a.d.",
    "OÜ", "UAB", "SIA",
    "ООД", "ЕООД", "АД", "ЕАД", "ООО", "ОАО", "ЗАО", "ПАО", "АО", "ТОВ",
    # Greek and Turkish
    "Α.Ε.", "Ε.Π.Ε.", "Ι.Κ.Ε.", "A.Ş.", "Ltd. Şti.",
    # Further afield
    "K.K.", "G.K.", "Bhd", "Sdn Bhd", "Tbk", "JSC", "PJSC", "OJSC", "CJSC", "FZE",
)  # fmt: skip

#: The forms that are conventionally written *before* the name rather than after it --
#: *AB Volvo*, *Oy Nokia Ab*, *UAB Example*, *ООО Пример*. Kept beside the list above and
#: far shorter than it, because a word taken off the front of a name is a bigger claim than
#: one taken off the end: *AG Insurance* is a name, not a form followed by one.
LEADING_FORMS: tuple[str, ...] = (
    "AB", "AS", "Oy", "OÜ", "UAB", "SIA", "SC", "PT",
    "ООО", "ОАО", "ЗАО", "ПАО", "АО", "ТОВ",
)  # fmt: skip

#: Hosts a "website" can be on without being the company's own: a profile on a network, a
#: page on somebody's platform. Two companies whose website is a LinkedIn page share
#: `linkedin.com` with every other company that has one, and saying so would be noise.
SHARED_HOSTS = frozenset(
    {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "youtube.com",
        "xing.com",
        "github.com",
        "gitlab.com",
        "codeberg.org",
        "indeed.com",
        "glassdoor.com",
        "wikipedia.org",
        "google.com",
        "wordpress.com",
        "medium.com",
        "linktr.ee",
    }
)


# ------------------------------------------------------------------ comparable forms


def fold(text: str) -> str:
    """``text`` as something two typings of it agree on: no case, no accents, no dots.

    Accents go because they are what people leave out -- *Nestlé* and *Nestle* are one
    company to whoever typed both. Dots go without leaving a space, so *S.A.* is *sa*;
    every other mark becomes a space, so *Coca-Cola* is two words and *A/S* is two letters.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = plain.casefold().replace(".", "")
    return " ".join("".join(char if char.isalnum() else " " for char in plain).split())


def _forms(written: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """The forms as the words they fold to, longest first so *Sdn Bhd* is tried before *Bhd*."""
    folded = {tuple(fold(form).split()) for form in written}
    return tuple(sorted((form for form in folded if form), key=len, reverse=True))


_TRAILING = _forms(LEGAL_FORMS)
_LEADING = _forms(LEADING_FORMS)


def bare_name(name: str) -> str:
    """A company's name with its legal form set aside, for comparing and for nothing else.

    *Acme*, *Acme Ltd*, *ACME GmbH* and *Acme, S.A.* are all ``acme``. Forms come off the
    end for as long as there are any -- *Acme GmbH & Co. KG* has three -- and then off the
    front, for the few written there. **Never down to nothing**: a company that is called
    *Limited* keeps its name. The words are joined without a space, so *Coca-Cola*,
    *Coca Cola* and *CocaCola* agree.

    Never shown and never stored: what a person typed is what the record says.
    """
    words = fold(name).split()

    def strip(forms, *, front: bool) -> bool:
        """Take one form off one end, if one is there and something would be left."""
        for form in forms:
            size = len(form)
            if len(words) <= size:
                continue
            if front and tuple(words[:size]) == form:
                del words[:size]
                return True
            if not front and tuple(words[-size:]) == form:
                del words[-size:]
                return True
        return False

    while strip(_TRAILING, front=False):
        pass
    while strip(_LEADING, front=True):
        pass
    return "".join(words)


def person_name(name: str) -> str:
    """A person's name for comparing: case, accents and spacing aside, and nothing more."""
    return fold(name)


def domain_of(url: str) -> str:
    """The host a website is on, without a leading ``www.``; empty where it says nothing.

    Empty for an address with no host, and for a host that is somebody else's platform
    (`SHARED_HOSTS`), where sharing it is not a sign of anything.
    """
    address = (url or "").strip()
    if not address:
        return ""
    try:
        parts = urlsplit(address if "://" in address else f"//{address}")
        host = (parts.hostname or "").lower()
    except ValueError:
        # A bracket with nothing in it, a port that is not a number: not an address.
        return ""
    host = host.removeprefix("www.")
    # A host has a dot in it and no space. The field is validated on the way in, but a row
    # written by an import or through the API was not typed into that form.
    if "." not in host or any(char.isspace() for char in host):
        return ""
    if any(host == shared or host.endswith(f".{shared}") for shared in SHARED_HOSTS):
        return ""
    return host


def _identifier_key(scheme: str, value: str, label: str) -> tuple[str, str, str]:
    """What makes two identifiers the same one. Case aside, as the constraints are (#211);
    and for *Other*, which is a free slot, the name given to it as well as the value."""
    named = label.casefold() if scheme == identifiers.OTHER else ""
    return (scheme, value.casefold(), named)


# ------------------------------------------------------------------------ the answer


@dataclass(frozen=True)
class Candidate:
    """One record that looks like the same one, and what made it look so."""

    record: object
    #: Sentences, in the language being read, one per signal that matched.
    reasons: tuple[str, ...]


def for_company(company) -> list[Candidate]:
    """This person's other companies that look like ``company``, each with why.

    Three reads at most: the names and websites of the person's other companies, the
    identifiers of theirs that match one of this company's, and the rows of the ones that
    matched. The comparing is done here, because a name with its legal form set aside is
    not something a database can be asked for.
    """
    if company.pk is None:
        return []
    name = bare_name(company.name)
    domain = domain_of(company.website)
    reasons: dict[int, list[str]] = {}

    others = (
        Company.objects.filter(owner_id=company.owner_id)
        .exclude(pk=company.pk)
        .values_list("pk", "name", "website")
    )
    for pk, other_name, other_website in others:
        if name and bare_name(other_name) == name:
            reasons.setdefault(pk, []).append(_("The same name, once the legal form is set aside"))
        if domain and domain_of(other_website) == domain:
            reasons.setdefault(pk, []).append(
                _("The same website: %(domain)s") % {"domain": domain}
            )

    mine = {
        _identifier_key(row.scheme, row.value, row.label): row for row in company.identifiers.all()
    }
    if mine:
        theirs = (
            CompanyIdentifier.objects.filter(
                owner_id=company.owner_id, scheme__in={key[0] for key in mine}
            )
            .exclude(company_id=company.pk)
            .values_list("company_id", "scheme", "value", "label")
        )
        for pk, scheme, value, label in theirs:
            matched = mine.get(_identifier_key(scheme, value, label))
            if matched is not None:
                reasons.setdefault(pk, []).append(
                    _("The same identifier: %(identifier)s") % {"identifier": str(matched)}
                )

    return _candidates(Company.objects.filter(owner_id=company.owner_id), reasons)


def for_contact(contact) -> list[Candidate]:
    """This person's other contacts that look like ``contact``: the same name, or the same
    email address. At whatever company -- somebody who changed employer is recorded twice
    exactly because they did."""
    if contact.pk is None:
        return []
    found = _contacts_alike(contact.owner_id, [contact])
    return _candidates(
        Contact.objects.filter(owner_id=contact.owner_id).select_related("company"),
        found.get(contact.pk, {}),
    )


def contacts_with_any(owner, contacts) -> set[int]:
    """Which of ``contacts`` have a possible duplicate among the owner's others.

    For a page that lists several people -- a company's -- and wants to say beside each
    whether it is worth a look, in one read for all of them rather than one each.
    """
    contacts = [contact for contact in contacts if contact.pk is not None]
    if not contacts:
        return set()
    owner_id = getattr(owner, "pk", owner)
    return {pk for pk, matches in _contacts_alike(owner_id, contacts).items() if matches}


def _contacts_alike(owner_id, contacts) -> dict[int, dict[int, list[str]]]:
    """For each of ``contacts``: the owner's other contacts like it, each with why."""
    everybody = list(Contact.objects.filter(owner_id=owner_id).values_list("pk", "name", "email"))
    by_name: dict[str, list[int]] = {}
    by_email: dict[str, list[int]] = {}
    for pk, name, email in everybody:
        if person_name(name):
            by_name.setdefault(person_name(name), []).append(pk)
        if email.strip():
            by_email.setdefault(email.strip().casefold(), []).append(pk)

    found: dict[int, dict[int, list[str]]] = {}
    for contact in contacts:
        matches: dict[int, list[str]] = {}
        for pk in by_name.get(person_name(contact.name), []):
            if pk != contact.pk:
                matches.setdefault(pk, []).append(_("The same name"))
        address = (contact.email or "").strip()
        for pk in by_email.get(address.casefold(), []) if address else []:
            if pk != contact.pk:
                matches.setdefault(pk, []).append(
                    _("The same email address: %(email)s") % {"email": address}
                )
        found[contact.pk] = matches
    return found


def _candidates(queryset, reasons: dict[int, list[str]]) -> list[Candidate]:
    """The records that matched, in the order a list of them reads in, each with why."""
    if not reasons:
        return []
    return [
        Candidate(record=record, reasons=tuple(reasons[record.pk]))
        for record in queryset.filter(pk__in=reasons).order_by("name", "pk")
    ]
