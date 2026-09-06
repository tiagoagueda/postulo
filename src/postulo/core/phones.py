"""Telephone numbers: the country in front, and something that can actually be dialled.

A recruiter's number written down as ``06 12 34 56 78`` cannot be dialled from anywhere
else, and the person writing it down is not thinking about that at the time. They are in
France, so is the recruiter, and the leading zero is simply how a telephone number looks.
Six months later that number is unreachable from a Portuguese phone and nothing in the
record says which country it belonged to.

So a field offers a country, puts its dialling code in front, and keeps the result in the
international form. The moment to fix a number is the moment somebody types it, because
that is the only moment the missing context is in the room.

**What this deliberately does not do.** It does not decide whether a number is real. That
needs the whole numbering plan of every country, which is a multi-megabyte library and a
constant stream of updates, and Postulo has no use for the answer: it is not going to dial
anything. A number nobody can parse is still worth keeping — refusing to save it would be
the worst possible outcome — so an unparseable number is stored exactly as it was typed.

The flags are derived from the country code rather than listed, because unlike a language
a country **is** a country: ``PT`` becomes the two regional indicators for P and T. The
same Windows caveat as the language picker applies, and the same answer: two letters is a
legible fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    "en-gb": "GB",
    "bg": "BG",
    "cs": "CZ",
    "da": "DK",
    "de": "DE",
    "el": "GR",
    "es": "ES",
    "et": "EE",
    "fi": "FI",
    "fr-fr": "FR",
    "ga": "IE",
    "hr": "HR",
    "hu": "HU",
    "it": "IT",
    "lt": "LT",
    "lv": "LV",
    "mt": "MT",
    "nl": "NL",
    "pl": "PL",
    "pt-pt": "PT",
    "ro": "RO",
    "sk": "SK",
    "sl": "SI",
    "sv": "SE",
}

_DIGITS = re.compile(r"\D+")


def flag(country: str) -> str:
    """The flag for an ISO country code, built from the code itself.

    Unlike a language, a country is a country, so nothing has to be decided by hand: the
    two letters map straight onto the two regional indicator characters.
    """
    country = (country or "").strip().upper()
    if len(country) != 2 or not country.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in country)


@dataclass(frozen=True)
class Country:
    code: str
    dialling: str
    name: str

    @property
    def flag(self) -> str:
        return flag(self.code)

    @property
    def label(self) -> str:
        return f"{self.name} +{self.dialling}"


def countries() -> list[Country]:
    return [Country(code, dialling, name) for code, dialling, name in COUNTRIES]


def default_country(language: str = "") -> str:
    """Which country to offer first, given what somebody reads Postulo in."""
    return FROM_LANGUAGE.get((language or "").lower(), "")


def combine(number: str, country: str) -> str:
    """One field's worth of typing, plus the country beside it, as it should be stored.

    A number that already begins with ``+`` says which country it is for, so the choice
    beside the field is ignored — somebody pasting an international number should not have
    it mangled by a dropdown they did not look at.

    A national number gets the chosen country's code in front, with the trunk prefix
    removed. Almost everywhere that is a leading zero; the exceptions are countries that
    have no trunk prefix at all, where there is no zero to remove and nothing happens.

    Anything that cannot be made sense of comes back exactly as it was typed. Refusing to
    save a number nobody can parse would be the worst outcome available.
    """
    number = (number or "").strip()
    if not number:
        return ""
    if number.startswith("+"):
        digits = _DIGITS.sub("", number)
        return f"+{digits}" if digits else number
    if number.startswith("00"):
        digits = _DIGITS.sub("", number)[2:]
        return f"+{digits}" if digits else number

    row = BY_CODE.get((country or "").strip().upper())
    if row is None:
        return number

    digits = _DIGITS.sub("", number)
    if not digits:
        return number
    return f"+{row[1]}{digits.lstrip('0')}"


def country_of(number: str) -> Country | None:
    """Which country a stored number belongs to, read back from its dialling code."""
    number = (number or "").strip()
    if not number.startswith("+"):
        return None
    digits = _DIGITS.sub("", number)
    for code, dialling, name in _BY_DIALLING:
        if digits.startswith(dialling):
            return Country(code, dialling, name)
    return None


def as_dialled(number: str) -> str:
    """The ``tel:`` form, which is the number with everything but digits and ``+`` gone."""
    number = (number or "").strip()
    if not number:
        return ""
    digits = _DIGITS.sub("", number)
    return f"+{digits}" if number.startswith("+") else digits


def readable(number: str) -> str:
    """A stored number, spaced so a person can read it back to somebody.

    Grouped from the right in twos and threes rather than by each country's own
    convention: that convention is part of the numbering plan this deliberately does not
    carry, and evenly spaced digits are readable everywhere while being wrong nowhere.
    """
    number = (number or "").strip()
    if not number.startswith("+"):
        return number
    found = country_of(number)
    if found is None:
        return number
    rest = _DIGITS.sub("", number)[len(found.dialling) :]
    if not rest:
        return number
    groups = []
    while len(rest) > 3:
        groups.append(rest[:3])
        rest = rest[3:]
    groups.append(rest)
    return f"+{found.dialling} " + " ".join(groups)
