"""Which countries' citizens are citizens of the EU, the EEA or Switzerland (#680).

One constant, in a module of its own, so that the question "is somebody a citizen of one of
these" has one answer wherever it is asked. The meaning is the narrow, checkable one that an
employer or a right to work turns on, not the geographic one: it excludes the United Kingdom
since 2020, and Ukraine, the Western Balkans and Turkey, which are in Europe and in none of
these.

**Where each part comes from**

- The 27 member states of the European Union: the Treaty on European Union and the
  accession treaties; Croatia (2013) is the latest, and the United Kingdom left on
  31 January 2020.
- The three countries of the European Free Trade Association that are in the European
  Economic Area, and not in the Union: Iceland, Liechtenstein and Norway (the Agreement on
  the European Economic Area, 1994).
- Switzerland, which is in neither but whose citizens have free movement with the Union
  under the Agreement on the Free Movement of Persons (1999).

`tests/test_nationalities.py` holds that every code here is a country `core/phones.py`
knows, which is the list a nationality is chosen from.
"""

from __future__ import annotations

#: The European Union's member states, by ISO 3166-1 alpha-2 code.
EU: frozenset[str] = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
    }
)

#: The EEA's members that are not in the Union.
EEA_NOT_EU: frozenset[str] = frozenset({"IS", "LI", "NO"})

#: Not in the Union or the EEA, with free movement under a bilateral treaty.
SWITZERLAND: frozenset[str] = frozenset({"CH"})

#: Citizens of any of these: "an EU or EEA country, or Switzerland".
EU_EEA_CH: frozenset[str] = EU | EEA_NOT_EU | SWITZERLAND
