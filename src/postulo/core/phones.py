"""Telephone numbers: the country in front, and something that can actually be dialled.

A recruiter's number written down as ``06 12 34 56 78`` cannot be dialled from anywhere
else, and the person writing it down is not thinking about that at the time. They are in
France, so is the recruiter, and the leading zero is simply how a telephone number looks.
Six months later that number is unreachable from a Portuguese phone and nothing in the
record says which country it belonged to.

So a field offers a country, puts its dialling code in front, and keeps the result in the
international form. The moment to fix a number is the moment somebody types it, because
that is the only moment the missing context is in the room.

**What it does with a number, since #304.** It reads it against the numbering plan of the
country it is for, and three things follow.

- *Putting the code in front* (`combine`) is the plan's work and no longer a rule of thumb.
  France's ``06 12 34 56 78`` and ``6 12 34 56 78`` are both ``+33612345678``; a country
  with no trunk prefix has nothing removed; Italy's leading zero is part of the number and
  stays, where stripping zeros made ``+39 06 6982 1234`` a number nobody has.
- *Saying what a number is* (`check`, and `kept` for one already stored): **fine**,
  **impossible** for its country — the wrong length, or a beginning no range there has —
  or **not placeable**, where the plan has no opinion to give. A field refuses the first
  kind with the reason and keeps the second with a warning beside it. A stored number
  may also be **miswritten**: readable, and not spelt the way it is dialled.
- *Showing it* (`readable`, `split`) groups the digits the way that country writes them,
  always from the stored international form, so the number typed and the number kept are
  the same number.

**Why the decision changed.** This docstring used to say that Postulo does not decide
whether a number is real: that needs every country's numbering plan, "a multi-megabyte
library and a constant stream of updates", for an answer Postulo had no use for, since it
dials nothing. And that a number nobody can parse is still worth keeping, so refusing to
save one "would be the worst possible outcome". Two things turned out to be wrong with
that. The rule of thumb it left in place was itself producing numbers nobody can dial —
the Italian zero above, a French number kept with its trunk zero after the code (#644) —
and it could not tell a number that is merely unusual from one a digit short, so a
mistyped number was stored without a word. The plan
tells those apart, which is what the old argument lacked: what is refused now is what
cannot exist, and what nobody can read is still kept, as it was typed, with a line saying
so. The cost is a dependency (`phonenumberslite`, Google's libphonenumber metadata without
the geocoding and carrier tables, Apache-2.0) that the monthly dependency run keeps
current.

**What is still not done.** Nothing here says a number is *somebody's*: the plan knows
which ranges a country has handed out, not who answers. That is proved by sending a code
to it, which is `channels.py`'s business (#142).

**Five rules that hold the rest together.**

- A number already stored is never rewritten by being looked at. A field hands back the
  stored value untouched unless somebody changed it, and checks it when they do.
- What somebody types is tidied before it is read (`tidy`): the spaces of every
  typography, the marks a contact app pastes round a number, a ``tel:`` in front. What is
  stored is read as it is stored.
- `check` and `combine` are one reading (`_settle`), so what a field says about a number
  and what it keeps for it cannot disagree; and nothing kept has a digit that was not
  typed as one, so words beside a number are never read as more of it.
- The plans are loaded when a number is first read and not before: `phonenumbers` is
  imported inside the functions that need it, here and nowhere else in Postulo, so starting
  Django costs nothing for it (`tests/test_numbering_plans.py` holds both halves).
- `normalise` asks the table below and not the plan. It decides what two numbers are
  compared by, a stored column and a unique index rest on it, and a rule that moved with a
  metadata release would move what the index holds.

This module has no opinion about flags any more. It used to build one from the country
code -- ``PT`` into the two regional indicators for P and T -- and note that Windows drew
the letters instead, calling that a legible fallback. It is not one (#88). The flag is an
SVG now, drawn by the ``{% flag %}`` tag from the country code this module already
supplies, and the builder was deleted rather than left lying about: a function whose only
purpose is to produce the broken thing is an invitation to produce it again.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from . import languages

#: (ISO 3166-1 alpha-2, dialling code, English name).
#:
#: The names are English because this is a list somebody scans for their own country, and
#: it is the one place in Postulo where a translated name would make the list harder to
#: use rather than easier: everyone recognises their country in English, and a person
#: reading Postulo in Greek does not want "Πορτογαλία" filed under Π.
COUNTRIES: tuple[tuple[str, str, str], ...] = (
    ("AD", "376", "Andorra"),
    ("AE", "971", "United Arab Emirates"),
    ("AF", "93", "Afghanistan"),
    ("AG", "1", "Antigua and Barbuda"),
    ("AI", "1", "Anguilla"),
    ("AL", "355", "Albania"),
    ("AM", "374", "Armenia"),
    ("AO", "244", "Angola"),
    ("AR", "54", "Argentina"),
    ("AS", "1", "American Samoa"),
    ("AT", "43", "Austria"),
    ("AU", "61", "Australia"),
    ("AW", "297", "Aruba"),
    ("AX", "358", "Åland Islands"),
    ("AZ", "994", "Azerbaijan"),
    ("BA", "387", "Bosnia and Herzegovina"),
    ("BB", "1", "Barbados"),
    ("BD", "880", "Bangladesh"),
    ("BE", "32", "Belgium"),
    ("BF", "226", "Burkina Faso"),
    ("BG", "359", "Bulgaria"),
    ("BH", "973", "Bahrain"),
    ("BI", "257", "Burundi"),
    ("BJ", "229", "Benin"),
    ("BL", "590", "Saint Barthélemy"),
    ("BM", "1", "Bermuda"),
    ("BN", "673", "Brunei"),
    ("BO", "591", "Bolivia"),
    ("BQ", "599", "Caribbean Netherlands"),
    ("BR", "55", "Brazil"),
    ("BS", "1", "Bahamas"),
    ("BT", "975", "Bhutan"),
    ("BW", "267", "Botswana"),
    ("BY", "375", "Belarus"),
    ("BZ", "501", "Belize"),
    ("CA", "1", "Canada"),
    ("CD", "243", "Congo (Kinshasa)"),
    ("CF", "236", "Central African Republic"),
    ("CG", "242", "Congo (Brazzaville)"),
    ("CH", "41", "Switzerland"),
    ("CI", "225", "Côte d’Ivoire"),
    ("CK", "682", "Cook Islands"),
    ("CL", "56", "Chile"),
    ("CM", "237", "Cameroon"),
    ("CN", "86", "China"),
    ("CO", "57", "Colombia"),
    ("CR", "506", "Costa Rica"),
    ("CU", "53", "Cuba"),
    ("CV", "238", "Cabo Verde"),
    ("CW", "599", "Curaçao"),
    ("CY", "357", "Cyprus"),
    ("CZ", "420", "Czechia"),
    ("DE", "49", "Germany"),
    ("DJ", "253", "Djibouti"),
    ("DK", "45", "Denmark"),
    ("DM", "1", "Dominica"),
    ("DO", "1", "Dominican Republic"),
    ("DZ", "213", "Algeria"),
    ("EC", "593", "Ecuador"),
    ("EE", "372", "Estonia"),
    ("EG", "20", "Egypt"),
    ("EH", "212", "Western Sahara"),
    ("ER", "291", "Eritrea"),
    ("ES", "34", "Spain"),
    ("ET", "251", "Ethiopia"),
    ("FI", "358", "Finland"),
    ("FJ", "679", "Fiji"),
    ("FK", "500", "Falkland Islands"),
    ("FM", "691", "Micronesia"),
    ("FO", "298", "Faroe Islands"),
    ("FR", "33", "France"),
    ("GA", "241", "Gabon"),
    ("GB", "44", "United Kingdom"),
    ("GD", "1", "Grenada"),
    ("GE", "995", "Georgia"),
    ("GF", "594", "French Guiana"),
    ("GG", "44", "Guernsey"),
    ("GH", "233", "Ghana"),
    ("GI", "350", "Gibraltar"),
    ("GL", "299", "Greenland"),
    ("GM", "220", "Gambia"),
    ("GN", "224", "Guinea"),
    ("GP", "590", "Guadeloupe"),
    ("GQ", "240", "Equatorial Guinea"),
    ("GR", "30", "Greece"),
    ("GT", "502", "Guatemala"),
    ("GU", "1", "Guam"),
    ("GW", "245", "Guinea-Bissau"),
    ("GY", "592", "Guyana"),
    ("HK", "852", "Hong Kong"),
    ("HN", "504", "Honduras"),
    ("HR", "385", "Croatia"),
    ("HT", "509", "Haiti"),
    ("HU", "36", "Hungary"),
    ("ID", "62", "Indonesia"),
    ("IE", "353", "Ireland"),
    ("IL", "972", "Israel"),
    ("IM", "44", "Isle of Man"),
    ("IN", "91", "India"),
    ("IO", "246", "British Indian Ocean Territory"),
    ("IQ", "964", "Iraq"),
    ("IR", "98", "Iran"),
    ("IS", "354", "Iceland"),
    ("IT", "39", "Italy"),
    ("JE", "44", "Jersey"),
    ("JM", "1", "Jamaica"),
    ("JO", "962", "Jordan"),
    ("JP", "81", "Japan"),
    ("KE", "254", "Kenya"),
    ("KG", "996", "Kyrgyzstan"),
    ("KH", "855", "Cambodia"),
    ("KI", "686", "Kiribati"),
    ("KM", "269", "Comoros"),
    ("KN", "1", "Saint Kitts and Nevis"),
    ("KP", "850", "North Korea"),
    ("KR", "82", "South Korea"),
    ("KW", "965", "Kuwait"),
    ("KY", "1", "Cayman Islands"),
    ("KZ", "7", "Kazakhstan"),
    ("LA", "856", "Laos"),
    ("LB", "961", "Lebanon"),
    ("LC", "1", "Saint Lucia"),
    ("LI", "423", "Liechtenstein"),
    ("LK", "94", "Sri Lanka"),
    ("LR", "231", "Liberia"),
    ("LS", "266", "Lesotho"),
    ("LT", "370", "Lithuania"),
    ("LU", "352", "Luxembourg"),
    ("LV", "371", "Latvia"),
    ("LY", "218", "Libya"),
    ("MA", "212", "Morocco"),
    ("MC", "377", "Monaco"),
    ("MD", "373", "Moldova"),
    ("ME", "382", "Montenegro"),
    ("MF", "590", "Saint Martin"),
    ("MG", "261", "Madagascar"),
    ("MH", "692", "Marshall Islands"),
    ("MK", "389", "North Macedonia"),
    ("ML", "223", "Mali"),
    ("MM", "95", "Myanmar"),
    ("MN", "976", "Mongolia"),
    ("MO", "853", "Macao"),
    ("MP", "1", "Northern Mariana Islands"),
    ("MQ", "596", "Martinique"),
    ("MR", "222", "Mauritania"),
    ("MS", "1", "Montserrat"),
    ("MT", "356", "Malta"),
    ("MU", "230", "Mauritius"),
    ("MV", "960", "Maldives"),
    ("MW", "265", "Malawi"),
    ("MX", "52", "Mexico"),
    ("MY", "60", "Malaysia"),
    ("MZ", "258", "Mozambique"),
    ("NA", "264", "Namibia"),
    ("NC", "687", "New Caledonia"),
    ("NE", "227", "Niger"),
    ("NF", "672", "Norfolk Island"),
    ("NG", "234", "Nigeria"),
    ("NI", "505", "Nicaragua"),
    ("NL", "31", "Netherlands"),
    ("NO", "47", "Norway"),
    ("NP", "977", "Nepal"),
    ("NR", "674", "Nauru"),
    ("NU", "683", "Niue"),
    ("NZ", "64", "New Zealand"),
    ("OM", "968", "Oman"),
    ("PA", "507", "Panama"),
    ("PE", "51", "Peru"),
    ("PF", "689", "French Polynesia"),
    ("PG", "675", "Papua New Guinea"),
    ("PH", "63", "Philippines"),
    ("PK", "92", "Pakistan"),
    ("PL", "48", "Poland"),
    ("PM", "508", "Saint Pierre and Miquelon"),
    ("PR", "1", "Puerto Rico"),
    ("PS", "970", "Palestine"),
    ("PT", "351", "Portugal"),
    ("PW", "680", "Palau"),
    ("PY", "595", "Paraguay"),
    ("QA", "974", "Qatar"),
    ("RE", "262", "Réunion"),
    ("RO", "40", "Romania"),
    ("RS", "381", "Serbia"),
    ("RU", "7", "Russia"),
    ("RW", "250", "Rwanda"),
    ("SA", "966", "Saudi Arabia"),
    ("SB", "677", "Solomon Islands"),
    ("SC", "248", "Seychelles"),
    ("SD", "249", "Sudan"),
    ("SE", "46", "Sweden"),
    ("SG", "65", "Singapore"),
    ("SH", "290", "Saint Helena"),
    ("SI", "386", "Slovenia"),
    ("SJ", "47", "Svalbard and Jan Mayen"),
    ("SK", "421", "Slovakia"),
    ("SL", "232", "Sierra Leone"),
    ("SM", "378", "San Marino"),
    ("SN", "221", "Senegal"),
    ("SO", "252", "Somalia"),
    ("SR", "597", "Suriname"),
    ("SS", "211", "South Sudan"),
    ("ST", "239", "São Tomé and Príncipe"),
    ("SV", "503", "El Salvador"),
    ("SX", "1", "Sint Maarten"),
    ("SY", "963", "Syria"),
    ("SZ", "268", "Eswatini"),
    ("TC", "1", "Turks and Caicos Islands"),
    ("TD", "235", "Chad"),
    ("TG", "228", "Togo"),
    ("TH", "66", "Thailand"),
    ("TJ", "992", "Tajikistan"),
    ("TK", "690", "Tokelau"),
    ("TL", "670", "Timor-Leste"),
    ("TM", "993", "Turkmenistan"),
    ("TN", "216", "Tunisia"),
    ("TO", "676", "Tonga"),
    ("TR", "90", "Türkiye"),
    ("TT", "1", "Trinidad and Tobago"),
    ("TV", "688", "Tuvalu"),
    ("TW", "886", "Taiwan"),
    ("TZ", "255", "Tanzania"),
    ("UA", "380", "Ukraine"),
    ("UG", "256", "Uganda"),
    ("US", "1", "United States"),
    ("UY", "598", "Uruguay"),
    ("UZ", "998", "Uzbekistan"),
    ("VA", "39", "Vatican City"),
    ("VC", "1", "Saint Vincent and the Grenadines"),
    ("VE", "58", "Venezuela"),
    ("VG", "1", "British Virgin Islands"),
    ("VI", "1", "U.S. Virgin Islands"),
    ("VN", "84", "Vietnam"),
    ("VU", "678", "Vanuatu"),
    ("WF", "681", "Wallis and Futuna"),
    ("WS", "685", "Samoa"),
    ("XK", "383", "Kosovo"),
    ("YE", "967", "Yemen"),
    ("YT", "262", "Mayotte"),
    ("ZA", "27", "South Africa"),
    ("ZM", "260", "Zambia"),
    ("ZW", "263", "Zimbabwe"),
)

BY_CODE: dict[str, tuple[str, str, str]] = {row[0]: row for row in COUNTRIES}

#: Longest dialling code first, so "1" never wins over "1" being part of nothing and
#: "35" never shadows "351".
_BY_DIALLING = sorted(COUNTRIES, key=lambda row: (-len(row[1]), row[0]))

#: A language code (as Postulo writes them) → the country whose dialling code to offer
#: somebody reading Postulo in it. A guess, and only a starting value: the field is a
#: choice, and being wrong costs one click.
FROM_LANGUAGE: dict[str, str] = {
    "en-GB": "GB",
    "bg": "BG",
    "cs": "CZ",
    "da": "DK",
    "de": "DE",
    "el": "GR",
    "es": "ES",
    "et": "EE",
    "fi": "FI",
    "fr-FR": "FR",
    "ga": "IE",
    "hr": "HR",
    "hu": "HU",
    "it": "IT",
    "lt": "LT",
    "lv": "LV",
    "mt": "MT",
    "nl": "NL",
    "pl": "PL",
    "pt-PT": "PT",
    "pt-BR": "BR",
    "ro": "RO",
    "sk": "SK",
    "sl": "SI",
    "sv": "SE",
}

_DIGITS = re.compile(r"\D+")

_SPACES = re.compile(r"\s+")

#: The way a link to a number begins, which is what a number copied out of a page or a
#: contact card arrives wearing.
_AS_A_LINK = re.compile(r"tel:\s*", re.IGNORECASE)

#: A bracket opened before the plus or the two zeros: "(+33 6 12 34 56 78)".
_BRACKET_BEFORE_THE_CODE = re.compile(r"\(\s*(?=\+|00)")

#: What people put between the digits besides the punctuation the plans know (a full stop,
#: a hyphen, a slash, brackets): a middle dot, an apostrophe -- Switzerland writes
#: 079 123'45'67 -- a comma, an underscore. Between two digits, and one of them: two commas
#: are a pause before an extension, and are left for the plan to say so.
_ALSO_SEPARATES = re.compile(r"(?<=\d) *[·'’,_] *(?=\d)")


@dataclass(frozen=True)
class Country:
    code: str
    dialling: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.name} +{self.dialling}"


def countries() -> list[Country]:
    return [Country(code, dialling, name) for code, dialling, name in COUNTRIES]


def country_name(code: str) -> str:
    """The English name of a country by its alpha-2 code, or the code if it is unknown.

    The table is a triple -- code, dialling prefix, name -- because it was built for
    telephone numbers. Anything that only wants the name asks here rather than unpacking
    it, which is what stops the third element becoming a magic index in four places (#92).
    """
    wanted = (code or "").strip().upper()
    for alpha2, _prefix, name in COUNTRIES:
        if alpha2 == wanted:
            return name
    return wanted


def country_choices() -> list[tuple[str, str]]:
    """Every country as a form choice, by name."""
    return sorted(((alpha2, name) for alpha2, _prefix, name in COUNTRIES), key=lambda row: row[1])


def default_country(language: str = "") -> str:
    """Which country to offer first, given what somebody reads Postulo in."""
    return FROM_LANGUAGE.get(languages.find(language, FROM_LANGUAGE), "")


# ------------------------------------------------------------- the numbering plans


def _plan():
    """Every country's numbering plan, loaded the first time a number is read.

    The one place Postulo imports `phonenumbers`. Inside a function on purpose: the
    package reads its tables as it is imported, a quarter of a second on a cold start, and
    a process that never meets a telephone number -- a migration, the worker sending a
    reminder, most page views -- should not pay for it.
    """
    import phonenumbers

    return phonenumbers


def tidy(typed: str) -> str:
    """What somebody typed or pasted, with what is not part of the number taken off.

    One step, at the top of everything that reads typing -- `check`, `combine`, and so an
    importer -- because a number is pasted far more often than it is typed, and what it is
    pasted from leaves marks nobody can see:

    - every kind of space becomes a plain one. French typography sets a number with narrow
      no-break spaces, the plans know only the ordinary space, and `06 12 34 56 78` copied
      from a letterhead was refused as too short;
    - characters that draw nothing are dropped: the direction marks a contact app wraps a
      number in, a zero-width space, a soft hyphen;
    - a leading ``tel:`` goes, a full-width ``＋`` is a plus, and a bracket opened before
      the plus is not a reason to say the number has no country in front;
    - a middle dot, an apostrophe, a comma or an underscore between two digits is a
      separator, as a full stop or a hyphen already was.

    **Never asked of a stored value that is only being looked at.** `kept`, `readable` and
    `normalise` read what the table holds, as it holds it: a value stored as
    ``tel:+33612345678`` is compared with nothing and dialled without its plus, and a mark
    worked out from the tidied text would say it was fine.
    """
    text = "".join(ch for ch in (typed or "") if unicodedata.category(ch) != "Cf")
    text = _SPACES.sub(" ", text).strip()
    text = _AS_A_LINK.sub("", text, count=1) if _AS_A_LINK.match(text) else text
    text = text.replace("＋", "+")
    opened = _BRACKET_BEFORE_THE_CODE.match(text)
    if opened:
        text = text[opened.end() :]
        if text.endswith(")") and text.count(")") > text.count("("):
            text = text[:-1].rstrip()
    return _ALSO_SEPARATES.sub(" ", text)


def _international(number: str) -> str:
    """The number beginning with ``+``, where it says which country it is for; or nothing.

    ``00`` is the international prefix most of the world dials, and a number written that
    way has said its country as plainly as one written with the plus.
    """
    if number.startswith("+"):
        return number
    if number.startswith("00"):
        return "+" + number[2:]
    return ""


def _digits(text: str) -> str:
    """The digits of a text and nothing else, as the ten ASCII ones whatever they were
    written in: a number typed in Arabic-Indic digits is the same number."""
    return "".join(str(unicodedata.decimal(ch)) for ch in text if ch.isdecimal())


def _region(country: str) -> str:
    """The country beside the field, as the table and the plans both key it; or nothing."""
    code = (country or "").strip().upper()
    return code if code in BY_CODE else ""


def _read(number: str, region: str):
    """The number as the plan reads it, with what stopped it being read when it was not.

    Returns the plan's own object and no error, or nothing and the plan's error.
    """
    plan = _plan()
    international = _international(number)
    try:
        return plan.parse(international or number, None if international else region), None
    except plan.NumberParseException as error:
        return None, error


def _is_a_short_number(parsed, region: str) -> bool:
    """Whether this is one of the country's short numbers: 3949, 112, 116 000.

    They exist, people are told to ring them, and they have no international form: nothing
    outside the country can dial one, so a country code in front would make a number that
    reaches nobody.
    """
    return bool(region) and _plan().is_valid_short_number_for_region(parsed, region)


def _spelt_on_a_keypad(number: str, region: str):
    """The number its letters spell, where that is what the letters are for; or nothing.

    ``1-800-FLOWERS`` is a number, and the plan reads it by putting each letter on the key
    that carries it. It does the same to any three letters it meets, which made
    ``+33 6 12 34 56 78 (mobile)`` the number ``+33612345678662453`` and ``+49 30 123456
    mob`` a valid number in Berlin that nobody wrote. So the letters are digits only when
    the whole value is a number the plan calls valid that way **and** is not one already
    without them: a number that is complete before its letters has words beside it, not
    more digits. Three letters at least, as the plan itself asks, and only the twenty-six
    a keypad carries.
    """
    letters = [ch for ch in number if ch.isalpha()]
    if len(letters) < 3 or not all(ch.isascii() for ch in letters):
        return None
    plan = _plan()
    without, _error = _read("".join(" " if ch.isalpha() else ch for ch in number), region)
    if without is not None and plan.is_valid_number(without):
        return None
    spelt, _error = _read(plan.convert_alpha_characters_in_number(number), region)
    if spelt is None or spelt.extension or not plan.is_valid_number(spelt):
        return None
    return spelt


def _lacks_its_area_code(parsed) -> bool:
    """Whether the number is the right length for a call within one town and no other.

    The plan says so of the number as a whole where no kind of number has that length.
    Where one has -- Brazil's ``4004-1234`` is a whole number at eight digits -- it says
    only that the length is possible, and the same question has to be asked of a landline
    and of a mobile: ``91234-5678`` is a Brazilian mobile without its two-digit area code,
    and "no number there begins that way" told nobody what to add.
    """
    plan = _plan()
    kinds = plan.PhoneNumberType
    local = plan.ValidationResult.IS_POSSIBLE_LOCAL_ONLY
    return any(
        plan.is_possible_number_for_type_with_reason(parsed, kind) == local
        for kind in (kinds.FIXED_LINE, kinds.MOBILE)
    )


#: The countries whose territories have a dialling code of their own, with the territories
#: in the order they are tried. Somebody in Saint-Denis writes ``06 92 12 34 56`` as
#: somebody in Lyon writes ``06 12 34 56 78``, chooses France, and is told France does not
#: use that number -- which is true, and no help: the cure is choosing Réunion. Three
#: countries, because these are the ones whose people write a territory's number the
#: national way and think of the country as theirs.
TERRITORIES: dict[str, tuple[str, ...]] = {
    "FR": ("RE", "GP", "MQ", "GF", "YT", "PM", "BL", "MF", "NC", "PF", "WF"),
    "NL": ("AW", "CW", "SX", "BQ"),
    "DK": ("GL", "FO"),
}


def _territory_that_uses(number: str, region: str) -> str:
    """The one territory of this country the number is valid in, by its code; or nothing.

    Asked only once the number has been refused for the country itself, and only of that
    country's territories, so nobody's plan is loaded for a number that was fine. Where
    several territories share a dialling code -- Guadeloupe, Saint Barthélemy and Saint
    Martin are all ``+590`` -- they give one number between them and the first the plan
    places it in is named. Where two give *different* numbers -- ``41 12 34`` is a number
    in Saint Pierre and Miquelon and another in New Caledonia -- nothing is named: that
    would be a guess.
    """
    plan = _plan()
    found: dict[str, str] = {}
    for code in TERRITORIES.get(region, ()):
        try:
            parsed = plan.parse(number, code)
        except plan.NumberParseException:
            continue
        if not parsed.extension and plan.is_valid_number_for_region(parsed, code):
            found.setdefault(plan.format_number(parsed, plan.PhoneNumberFormat.E164), code)
    return next(iter(found.values())) if len(found) == 1 else ""


# ----------------------------------------------------------- what a number is

#: The plan read the number and found nothing wrong with it.
FINE = "fine"
#: The plan has no opinion to give: kept, with a warning beside it.
UNPLACEABLE = "unplaceable"
#: It cannot exist, or cannot be placed without something the person can supply: refused
#: when it is typed, and marked where it is already stored.
IMPOSSIBLE = "impossible"
#: Only ever said of a number already stored: it can be read, and the plan would write it
#: with other digits. Kept as it is, with the spelling that can be dialled beside it.
MISWRITTEN = "miswritten"

TOO_SHORT = "too-short"
TOO_LONG = "too-long"
WRONG_LENGTH = "wrong-length"
NOT_IN_USE = "not-in-use"
NO_AREA_CODE = "no-area-code"
ELSEWHERE = "elsewhere"
NO_COUNTRY = "no-country"
EXTENSION = "extension"
WORDS = "words"
UNKNOWN_CODE = "unknown-code"
SHORT_NUMBER = "short-number"
NOT_A_NUMBER = "not-a-number"
AS_TYPED = "as-typed"
AS_WRITTEN = "as-written"

#: One sentence per reason, in the reader's language. The country's name is the one in the
#: table above, in English, for the reason the table gives.
_SENTENCES = {
    TOO_SHORT: _("That number is too short for %(country)s."),
    TOO_LONG: _("That number is too long for %(country)s."),
    WRONG_LENGTH: _("That number is not a length %(country)s uses."),
    NOT_IN_USE: _("That is not a number %(country)s uses: no number there begins that way."),
    NO_AREA_CODE: _("That number has no area code."),
    ELSEWHERE: _(
        "That is not a number %(country)s uses. It is one %(territory)s uses: choose "
        "%(choice)s beside the number."
    ),
    # The sentence `channels.py` has always said about the same number.
    NO_COUNTRY: _(
        "That number has no country in front of it, so nowhere else can dial "
        "it. Choose the country, or write it starting with +."
    ),
    EXTENSION: _("An extension cannot be kept as part of the number. Leave it out."),
    WORDS: _("That has words in it as well as a number. Leave them out."),
    UNKNOWN_CODE: _(
        "No country has the dialling code that number begins with, so it could not be "
        "checked. It is kept as it was typed."
    ),
    SHORT_NUMBER: _(
        "That is a short number in %(country)s, which can only be dialled from inside the "
        "country. It is kept as it was typed, with no country in front of it."
    ),
    NOT_A_NUMBER: _(
        "That could not be read as a telephone number, so it could not be checked. It is "
        "kept as it was typed."
    ),
    AS_TYPED: _(
        "This is kept as it was typed, with no country in front of it, so it could not be "
        "checked and cannot be dialled from anywhere else."
    ),
    AS_WRITTEN: _("Kept as it was written. The number that can be dialled is %(number)s."),
}

#: The same two refusals where the form has somewhere to put what is left out. A contact
#: has notes; *Your details* has none, and a sentence sending somebody to a box that is
#: not on the page is worse than one that only says what to do here.
_SENTENCES_WHERE_THERE_ARE_NOTES = {
    EXTENSION: _(
        "An extension cannot be kept as part of the number. Leave it out here, and put it "
        "in the notes."
    ),
    WORDS: _(
        "That has words in it as well as a number. Leave them out here, and put them in the notes."
    ),
}

#: The two reasons that can be given of a number so short that nothing says where it is for.
_SENTENCES_NAMING_NOWHERE = {
    TOO_SHORT: _("That number is too short."),
    TOO_LONG: _("That number is too long."),
}


@dataclass(frozen=True)
class Verdict:
    """What the plan says about a number: fine, impossible for its country, or unplaceable."""

    state: str = FINE
    reason: str = ""
    #: The country the reason is about, by its code in the table; a dialling code with its
    #: plus where the table has no country for it; nothing where nothing says.
    country: str = ""
    #: The territory that does use a number its country does not, by its code.
    elsewhere: str = ""
    #: The spelling that can be dialled, of a stored number written another way.
    dialled: str = ""

    @property
    def fine(self) -> bool:
        return self.state == FINE

    @property
    def impossible(self) -> bool:
        return self.state == IMPOSSIBLE

    @property
    def unplaceable(self) -> bool:
        return self.state == UNPLACEABLE

    @property
    def miswritten(self) -> bool:
        return self.state == MISWRITTEN

    def sentence(self, *, notes: bool = False) -> str:
        """The reason as a sentence, or nothing for a number that is fine.

        `notes` is whether the form the number sits on has a notes box, which two of the
        refusals send what they leave out to.
        """
        if not self.reason:
            return ""
        if notes and self.reason in _SENTENCES_WHERE_THERE_ARE_NOTES:
            return str(_SENTENCES_WHERE_THERE_ARE_NOTES[self.reason])
        if not self.country and self.reason in _SENTENCES_NAMING_NOWHERE:
            return str(_SENTENCES_NAMING_NOWHERE[self.reason])
        return str(_SENTENCES[self.reason]) % {
            "country": _in_a_sentence(self.country),
            "territory": _in_a_sentence(self.elsewhere),
            # As the chooser lists it, with no article: it is what somebody looks for there.
            "choice": country_name(self.elsewhere),
            "number": readable(self.dialled) if self.dialled else "",
        }

    @property
    def message(self) -> str:
        """The sentence for a form with no notes box, which is most of the places it is said."""
        return self.sentence()


#: The countries English names with an article: "too short for the Netherlands".
_NAMED_WITH_THE = frozenset(
    "AE AX BQ BS CF CK DO FK FO GB GM IM IO KM KY MH MP MV NL PH SB SC TC US VG VI".split()
)


def _in_a_sentence(country: str) -> str:
    """A country's name as it sits in a sentence.

    The table's name, which is English in every language for the reason the table gives.
    English alone puts an article in front of a score of them, and the article is English
    grammar, not part of the name, so it is added only where the sentence is English: a
    French sentence says "pour United States" and not "pour the United States".
    """
    row = BY_CODE.get(country)
    if row is None:
        # A dialling code with no country in the table: the freephone numbers under +800.
        return country
    english = languages.primary(languages.current()) == "en"
    return f"the {row[2]}" if english and country in _NAMED_WITH_THE else row[2]


def _country_named(parsed, region: str) -> str:
    """Which country a sentence about this number should name, by its code.

    The one chosen beside the field, for a number typed the national way. Otherwise the one
    the plan places the number in, and failing that the country its dialling code is
    chiefly for: ``+1`` is a score of countries and "too short for the United States" is the
    sentence somebody expects of it. A code that belongs to no country in the table -- the
    international freephone numbers under ``+800`` -- is named by the code.
    """
    plan = _plan()
    code = (
        region
        or plan.region_code_for_number(parsed)
        or plan.region_code_for_country_code(parsed.country_code)
    )
    return code if code in BY_CODE else f"+{parsed.country_code}"


def _settle(number: str, region: str) -> tuple[Verdict, str]:
    """What a number is, and what is kept for it: one reading, so the two cannot disagree.

    `check` gives the first and `combine` the second. They were two functions that each
    read the number their own way, and they disagreed: ``+33 6 12 #`` was "kept as it was
    typed" to one and ``+33612`` to the other, which the page then marked as impossible.
    Two rules follow from deciding both here. Whatever is called *unplaceable* is kept as
    it was typed, and whatever is called *fine* is kept in a form that is fine when it is
    read back.

    The number arrives tidied (`tidy`) when it was typed, and as the table holds it when it
    is a stored value being looked at.
    """
    if not number:
        return Verdict(), ""
    international = _international(number)
    digits = _digits(number)
    words = any(ch.isalpha() for ch in number)
    if not digits:
        # Nothing to place. Words are the only thing anybody has, and are kept; punctuation
        # alone is a number with no digits in it.
        if words:
            return Verdict(UNPLACEABLE, NOT_A_NUMBER), number
        return Verdict(IMPOSSIBLE, TOO_SHORT, "" if international else region), number
    if not international and not region:
        # "Tel. +33 6 12 34 56 78" has a country in front of the number and a word in
        # front of that; telling it to start with a plus would be telling it what it did.
        return Verdict(IMPOSSIBLE, WORDS if words and "+" in number else NO_COUNTRY), number

    plan = _plan()
    read = number
    parsed, error = _read(read, region)
    if words:
        # An extension the plan knows the word for is an extension. Otherwise the letters
        # are a number spelt on a keypad, or they are words; never digits by default.
        if parsed is not None and parsed.extension:
            return Verdict(IMPOSSIBLE, EXTENSION), number
        parsed, error = _spelt_on_a_keypad(number, region), None
        if parsed is None:
            return Verdict(IMPOSSIBLE, WORDS), number
    elif error is not None and error.error_type == plan.NumberParseException.NOT_A_NUMBER:
        # Something in it the plan cannot read past -- a stray `#`, a character it has no
        # name for. A number that says its country, or has one beside it, is never
        # unplaceable for that: it is judged on its digits.
        read = f"+{_digits(international)}" if international else digits
        parsed, error = _read(read, region)

    if error is not None:
        kinds = plan.NumberParseException
        if error.error_type == kinds.INVALID_COUNTRY_CODE:
            return Verdict(UNPLACEABLE, UNKNOWN_CODE), number
        named = _chiefly(_digits(international)) if international else region
        # The digits are what was said, so with a plus they are kept in the one shape a
        # number with a plus has; a national number the plan could not begin on has no
        # other shape than the one it was typed in.
        stored = f"+{_digits(international)}" if international else number
        if error.error_type == kinds.TOO_LONG:
            return Verdict(IMPOSSIBLE, TOO_LONG, named), stored
        # Digits, and too few of them for the plan to begin on.
        return Verdict(IMPOSSIBLE, TOO_SHORT, named), stored

    if parsed.extension:
        return Verdict(IMPOSSIBLE, EXTENSION), number
    stored = plan.format_number(parsed, plan.PhoneNumberFormat.E164)
    if plan.is_valid_number(parsed):
        return Verdict(), stored
    # Typed the national way for the country beside it. Not so for a number dialled out
    # through that country's own international prefix -- `011 33 ...` beside the United
    # States is a French number, and a sentence about it names France.
    national = not international and parsed.country_code == plan.country_code_for_region(region)
    if national and _is_a_short_number(parsed, region):
        return Verdict(UNPLACEABLE, SHORT_NUMBER, region), number
    named = _country_named(parsed, region if national else "")
    length = plan.is_possible_number_with_reason(parsed)
    lengths = plan.ValidationResult
    if length == lengths.INVALID_COUNTRY_CODE:
        return Verdict(UNPLACEABLE, UNKNOWN_CODE), number
    if national:
        territory = _territory_that_uses(read, region)
        if territory:
            return Verdict(IMPOSSIBLE, ELSEWHERE, named, elsewhere=territory), stored
    if length == lengths.IS_POSSIBLE_LOCAL_ONLY:
        # The right length for a call within one town, and too short for anybody else.
        return Verdict(IMPOSSIBLE, NO_AREA_CODE, named), stored
    if length == lengths.TOO_SHORT:
        return Verdict(IMPOSSIBLE, TOO_SHORT, named), stored
    if length == lengths.TOO_LONG:
        return Verdict(IMPOSSIBLE, TOO_LONG, named), stored
    if length == lengths.INVALID_LENGTH:
        return Verdict(IMPOSSIBLE, WRONG_LENGTH, named), stored
    if _lacks_its_area_code(parsed):
        return Verdict(IMPOSSIBLE, NO_AREA_CODE, named), stored
    return Verdict(IMPOSSIBLE, NOT_IN_USE, named), stored


def check(number: str, country: str = "") -> Verdict:
    """What a number somebody typed is, with the country chosen beside it.

    **Impossible** is refused: too short or too long for its country, a length the country
    does not use, a beginning no range there has. So are four things the person can put
    right and nothing else can. A national number with no country beside it, which the
    plan cannot place and one click can: the alternative is guessing a country, and a
    guessed country in front is a wrong number. A number without its area code. An
    extension, which the international form has no room for and which used to be run into
    the digits. And words beside a number, which the plan would otherwise read as more
    digits.

    Where the country chosen is one whose territories have dialling codes of their own and
    the number is one of theirs, the refusal says which (`TERRITORIES`).

    **Unplaceable** is kept, with a warning: a dialling code no country has, one of a
    country's short numbers, and words with no number among them. Refusing those would
    lose the only thing anybody has, and the plan has not said they are wrong -- it has
    said nothing.

    Validity is asked of every country that shares the dialling code, not only of the one
    chosen: Canada chosen beside a number in New York is not a mistake worth refusing.
    """
    return _settle(tidy(number), _region(country))[0]


def combine(number: str, country: str) -> str:
    """One field's worth of typing, plus the country beside it, as it should be stored.

    A number that already begins with ``+`` or ``00`` says which country it is for, so the
    choice beside the field is ignored — somebody pasting an international number should
    not have it mangled by a dropdown they did not look at.

    A national number gets the chosen country's code in front, and what happens to its
    first digits is the country's own rule, read from its numbering plan: France's trunk
    zero goes, a country with no trunk prefix loses nothing, and Italy's zero is part of
    the number and stays.

    What comes back is the international form whenever the plan can read the number at
    all, including a number it will go on to call impossible: saying so is `check`'s work,
    and a field asks it before it stores anything. These come back as they were typed
    (after `tidy`), because no international form of them exists: a national number with
    no country beside it, one of a country's short numbers, a dialling code no country has,
    a number with an extension, a number with words beside it, and words alone.

    An importer stores what this returns without asking `check`, so nothing here may drop
    part of what was written or add to it: an extension lost on the way in is lost for
    good, and a word read as digits is a number nobody wrote. **What comes back never has
    a digit the text did not hold as a digit**, except through a number spelt on a keypad
    that the plan calls valid.
    """
    return _settle(tidy(number), _region(country))[1]


def kept(number: str) -> Verdict:
    """What a number already stored is, for the mark a page draws beside it.

    The same question as `check`, asked of the stored value and nothing else: there is no
    country beside it, because none was kept, and it is read as the table holds it, not
    tidied. A value with no ``+`` is therefore *as typed* -- a national number from before
    a country was asked for, a short number, words -- and that is said as a fact, not as
    an error: nothing is refused for being looked at.

    One answer is this function's own. A value the plan can read and would write with other
    digits -- a trunk zero left after the code by an import from before #304, Argentina's
    ``15`` -- is **miswritten**: the plan calls the number fine, and what is stored is not
    that number's spelling. It is neither rewritten nor called fine. It is marked, with the
    spelling that can be dialled, and checked the day somebody changes it.
    """
    number = (number or "").strip()
    if not number:
        return Verdict()
    international = _international(number)
    if not international:
        return Verdict(UNPLACEABLE, AS_TYPED)
    verdict, stored = _settle(number, "")
    if verdict.fine and stored != f"+{_digits(international)}":
        return Verdict(MISWRITTEN, AS_WRITTEN, dialled=stored)
    return verdict


# ------------------------------------------------------------- reading it back


def _by_dialling_code(digits: str) -> Country | None:
    """The first country in the table whose dialling code these digits begin with."""
    for code, dialling, name in _BY_DIALLING:
        if digits.startswith(dialling):
            return Country(code, dialling, name)
    return None


def _chiefly(digits: str) -> str:
    """The country the dialling code these digits begin with is chiefly for, by its code.

    The table finds the dialling code and the plan says whose it mainly is. The table's
    first country under a shared code is the first in the alphabet, which made ``+1`` too
    short "for Antigua and Barbuda", ``+7 9`` a matter for Kazakhstan and ``+212 6`` one
    for Western Sahara.
    """
    found = _by_dialling_code(digits)
    if found is None:
        return ""
    main = _plan().region_code_for_country_code(int(found.dialling))
    return main if main in BY_CODE else found.code


def country_of(number: str) -> Country | None:
    """Which country a stored number belongs to, read back from the number itself.

    From the plan where it can say: ``+1 415…`` is the United States and ``+1 604…`` is
    Canada, which the dialling code alone cannot tell apart -- by the code alone every
    number under ``+1`` was Antigua and Barbuda, the first of them in the alphabet. A
    number the plan calls impossible, or cannot begin on, is still placed, in the country
    its code is chiefly for.
    """
    number = (number or "").strip()
    if not number.startswith("+"):
        return None
    parsed, _error = _read(number, "")
    if parsed is not None:
        plan = _plan()
        code = plan.region_code_for_number(parsed) or plan.region_code_for_country_code(
            parsed.country_code
        )
        if code in BY_CODE:
            return Country(*BY_CODE[code])
    code = _chiefly(_digits(number))
    return Country(*BY_CODE[code]) if code else None


def split(number: str) -> tuple[str, str]:
    """A stored number as the two things a field shows: its country, and the rest.

    The rest is grouped the way the country writes it, and is what the international form
    reads after the code -- ``6 12 34 56 78`` beside France, ``06 6982 1234`` beside Italy
    -- so the chooser and the box together read as the number itself.

    **Typing the two back gives the stored number, exactly, or the rest is the stored
    value whole.** A field decides whether somebody changed a number by comparing what
    comes back with what this returned, so a split that did not round-trip would rewrite a
    number nobody touched. A value that was never in international form has no country to
    show: the chooser is left empty rather than set to a guess, because a chooser showing
    France beside a number nobody said was French reads as a fact.
    """
    number = (number or "").strip()
    if not number:
        return "", ""
    found = country_of(number)
    if found is None:
        return "", number
    parsed, _error = _read(number, "")
    if parsed is not None:
        plan = _plan()
        whole = plan.format_number(parsed, plan.PhoneNumberFormat.INTERNATIONAL)
        code = f"+{parsed.country_code} "
        if whole.startswith(code):
            rest = whole[len(code) :]
            if combine(rest, found.code) == number:
                return found.code, rest
    return found.code, number


def normalise(number: str) -> str:
    """The form two numbers are compared by, or nothing when they cannot be compared.

    ``+351 912 345 678``, ``+351912345678`` and ``00351912345678`` are one number written
    three ways, and only the international digits make that visible. A number that never
    reached that form has no such answer — it is a national number for a country nobody
    recorded — and this returns nothing for it, which keeps it out of every comparison
    rather than letting it collide with the first number that happens to share its digits.

    Asked of the table and not of the plan, for the reason the module gives: a unique index
    rests on what this returns.
    """
    number = (number or "").strip()
    # `00` is the international prefix most of the world dials, and a number written that
    # way is the same number: comparing only the `+` form would let one account hold
    # +351912345678 while another held 00351912345678, which is the rule failing quietly.
    if number.startswith("00"):
        number = "+" + _DIGITS.sub("", number)[2:]
    if not number.startswith("+"):
        return ""
    digits = _DIGITS.sub("", number)
    if not digits or _by_dialling_code(digits) is None:
        return ""
    return f"+{digits}"


def as_dialled(number: str) -> str:
    """The ``tel:`` form, which is the number with everything but digits and ``+`` gone."""
    number = (number or "").strip()
    if not number:
        return ""
    digits = _DIGITS.sub("", number)
    return f"+{digits}" if number.startswith("+") else digits


def readable(number: str) -> str:
    """A stored number, grouped the way its own country writes it.

    France in pairs, Portugal in threes, the United States with its hyphens: the
    international format of the plan, drawn from the stored value and never kept. What is
    shown has the digits that are stored and no others. A value the plan would write with
    different digits -- a trunk zero left after the code by an import from before #304 --
    is shown as it is, because a page that showed the corrected number over a link that
    dials the stored one would be showing two numbers.

    Anything with no international form is left alone: there is no country to group it by.
    """
    number = (number or "").strip()
    international = _international(number)
    if not international:
        return number
    parsed, _error = _read(number, "")
    if parsed is None:
        return number
    plan = _plan()
    shown = plan.format_number(parsed, plan.PhoneNumberFormat.INTERNATIONAL)
    if _DIGITS.sub("", shown) != _DIGITS.sub("", international):
        return number
    return shown
