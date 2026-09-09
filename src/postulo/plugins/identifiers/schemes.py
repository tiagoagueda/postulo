"""Every identifier Postulo knows, and which of people and organisations each identifies.

One table rather than two, because the two were not disjoint and pretending they were lost
real identifiers (#109). Three schemes here say *both*, and each has a reason:

* **ISNI** identifies *public identities of contributors and organisations* — that is its
  own definition. It was offered to people only, so a university could not record the ISNI
  an EU application form asks it for.
* **Wikidata** has items for anything, which includes a great many people. It was offered
  to companies only, so a researcher could not record theirs.
* **LinkedIn** has company pages and personal profiles alike, and was offered to companies
  only.

None of those was a bug anybody filed. All three followed from the sets living in two files
that never had to agree.

**Nothing here is looked up anywhere.** A scheme says what a value should look like, folds
what somebody pasted into the canonical spelling, checks the digits where it has some, and
knows where it links. That is the whole of what a scheme may do — a checksum catches the
typos a lookup would, which is the entire reason ORCID and the LEI have one.

**``register`` is one generic scheme for every national company register** — SIRET, NIF,
Companies House, KvK, Handelsregister — and Postulo validates none of them beyond a country
prefix. That is the itch this being a plugin scratches: a plugin per country could do
better, without waiting for a patch here.
"""

from __future__ import annotations

import re

from django.utils.translation import gettext_lazy as _

from postulo.core.identifiers import COMPANY, PERSON, Scheme

BOTH = frozenset({PERSON, COMPANY})
ONLY_PERSON = frozenset({PERSON})
ONLY_COMPANY = frozenset({COMPANY})

# ------------------------------------------------------------------------- the keys
#
# Unchanged from the two registries these replace. Every stored row carries one of them, so
# a key that moved would be a data migration -- and there is no reason for one to move: they
# were already distinct across the two files except `other`, which stays `other` and was
# always the hint that one table was the right shape.

ORCID = "orcid"
RESEARCHERID = "researcherid"
SCOPUS = "scopus"
ISNI = "isni"
WIKIDATA = "wikidata"
LEI = "lei"
REGISTER = "register"
LINKEDIN = "linkedin"
CRUNCHBASE = "crunchbase"
OPENCORPORATES = "opencorporates"
OTHER = "other"


# ----------------------------------------------------- what each one does to a value


def _regroup(size: int, separator: str):
    """Put sixteen characters back into groups of four, however they were typed.

    An ORCID and an ISNI are the same sixteen characters written two ways -- hyphens for
    one, spaces for the other -- and people type them with neither.
    """

    def tidy(value: str) -> str:
        digits = re.sub(r"[^0-9X]", "", value)
        if len(digits) == size:
            return separator.join(digits[i : i + 4] for i in range(0, size, 4))
        return value

    return tidy


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def _no_spaces(value: str) -> str:
    return value.replace(" ", "")


def _country_then_number(value: str) -> str:
    """``PT501234567`` and ``PT 501 234 567`` read the same afterwards."""
    value = re.sub(r"\s+", " ", value)
    if len(value) > 2 and value[:2].isalpha() and value[2] != " ":
        value = value[:2] + " " + value[2:].lstrip(" -:")
    return value


def orcid_checks_out(value: str) -> bool:
    """ISO 7064 MOD 11-2, which is what the last character of an ORCID is.

    An ORCID that fails its own checksum is a typo, every time. Checking it here is why
    Postulo never has to ask orcid.org whether an identifier is real.
    """
    digits = value.replace("-", "")
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    remainder = total % 11
    expected = (12 - remainder) % 11
    return ("X" if expected == 10 else str(expected)) == digits[-1]


def lei_checks_out(value: str) -> bool:
    """ISO 7064 MOD 97-10: letters become two digits, and the whole thing is 1 mod 97."""
    digits = "".join(str(int(char, 36)) for char in value)
    return int(digits) % 97 == 1


# --------------------------------------------------------------------------- the table

SCHEMES: dict[str, Scheme] = {
    scheme.key: scheme
    for scheme in (
        # -------------------------------------------------------------------- people
        Scheme(
            ORCID,
            "ORCID",
            pattern=re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"),
            subjects=ONLY_PERSON,
            link="https://orcid.org/{value}",
            example="0000-0002-1825-0097",
            url_paths=("/",),
            hosts=("orcid.org",),
            upper=True,
            tidy=_regroup(16, "-"),
            checksum=orcid_checks_out,
            checksum_message=_(
                "That ORCID's last digit does not match the rest, so one of them is a typo."
            ),
        ),
        Scheme(
            RESEARCHERID,
            "ResearcherID",
            pattern=re.compile(r"^[A-Z]-\d{4}-\d{4}$"),
            subjects=ONLY_PERSON,
            link="https://www.webofscience.com/wos/author/record/{value}",
            example="A-1234-2020",
            upper=True,
        ),
        Scheme(
            SCOPUS,
            "Scopus Author ID",
            pattern=re.compile(r"^\d{10,11}$"),
            subjects=ONLY_PERSON,
            link="https://www.scopus.com/authid/detail.uri?authorId={value}",
            example="7004212771",
            tidy=_digits_only,
        ),
        # ---------------------------------------------------------------------- both
        Scheme(
            ISNI,
            "ISNI",
            pattern=re.compile(r"^\d{4} \d{4} \d{4} \d{3}[\dX]$"),
            subjects=BOTH,
            link="https://isni.org/isni/{value}",
            example="0000 0001 2281 955X",
            upper=True,
            tidy=_regroup(16, " "),
        ),
        Scheme(
            WIKIDATA,
            "Wikidata",
            re.compile(r"^Q[1-9]\d{0,11}$"),
            subjects=BOTH,
            link="https://www.wikidata.org/wiki/{value}",
            example="Q95",
            url_paths=("/wiki/", "/entity/"),
            hosts=("wikidata.org",),
            upper=True,
        ),
        Scheme(
            LINKEDIN,
            "LinkedIn",
            re.compile(r"^[a-z0-9][a-z0-9._\-]{0,99}$"),
            subjects=BOTH,
            link="https://www.linkedin.com/company/{value}/",
            person_link="https://www.linkedin.com/in/{value}/",
            example="aperture-science",
            url_paths=("/company/", "/school/", "/showcase/", "/in/"),
            hosts=("linkedin.com",),
            lower=True,
        ),
        # ----------------------------------------------------------------- companies
        Scheme(
            LEI,
            _("Legal Entity Identifier (LEI)"),
            re.compile(r"^[A-Z0-9]{18}\d{2}$"),
            subjects=ONLY_COMPANY,
            link="https://search.gleif.org/#/record/{value}",
            example="HWUPKR0MPOU8FGXBT394",
            url_paths=("/record/",),
            hosts=("gleif.org",),
            upper=True,
            tidy=_no_spaces,
            checksum=lei_checks_out,
            checksum_message=_("The LEI's check digits do not match."),
        ),
        Scheme(
            REGISTER,
            _("Company register number"),
            # A two-letter country, then the number as the register writes it.
            re.compile(r"^[A-Z]{2} [A-Z0-9][A-Z0-9 .\-/]{1,38}$"),
            subjects=ONLY_COMPANY,
            example=_("PT 501234567, FR 552081317, DE HRB 12345"),
            upper=True,
            tidy=_country_then_number,
        ),
        Scheme(
            CRUNCHBASE,
            "Crunchbase",
            re.compile(r"^[a-z0-9][a-z0-9._\-]{0,99}$"),
            subjects=ONLY_COMPANY,
            link="https://www.crunchbase.com/organization/{value}",
            example="aperture-science",
            url_paths=("/organization/",),
            hosts=("crunchbase.com",),
            lower=True,
        ),
        Scheme(
            OPENCORPORATES,
            "OpenCorporates",
            # jurisdiction code, a slash, the number: gb/01234567 or us_de/2345678
            re.compile(r"^[a-z]{2}(_[a-z]{2,3})?/[A-Za-z0-9.\-]{1,40}$"),
            subjects=ONLY_COMPANY,
            link="https://opencorporates.com/companies/{value}",
            example="gb/01234567",
            url_paths=("/companies/",),
            hosts=("opencorporates.com",),
            segments=2,
            lower=True,
        ),
        # --------------------------------------------------------------------- both
        # `other` was already the one key in both registries, and that overlap is the hint
        # the matrix was the right model all along.
        Scheme(
            OTHER,
            _("Other"),
            re.compile(r"^\S(.{0,98}\S)?$"),
            subjects=BOTH,
            example=_("a staff number, a national registration"),
        ),
    )
}
