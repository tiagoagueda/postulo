"""A gendered occupation label is found by either of its forms (#533).

ESCO writes both forms of an occupation in one label, ``développeur de logiciels/
développeuse de logiciels``, and a title is typed as one of them. These run on a small
classification of their own, so they do not wait for the real file to be downloaded.
"""

from __future__ import annotations

import pytest

from postulo.jobs import esco


@pytest.fixture
def classification(monkeypatch):
    document = {
        "revision": "test",
        "languages": ["en", "fr"],
        "unit_groups": {
            "2512": {"names": {"en": "Software developers", "fr": "Développeurs de logiciels"}},
            "5131": {"names": {"en": "Waiters", "fr": "Serveurs"}},
            "5132": {"names": {"en": "Bartenders", "fr": "Barmans"}},
        },
        "occupations": {
            "a": {
                "isco": "2512",
                "names": {
                    "en": "software developer",
                    "fr": "développeur de logiciels/développeuse de logiciels",
                },
            },
            "b": {"isco": "5131", "names": {"en": "waiter/waitress", "fr": "serveur/serveuse"}},
            "c": {"isco": "5131", "names": {"en": "head waiter", "fr": "maître d'hôtel"}},
            # One French name in two unit groups: no answer is better than an arbitrary one.
            "d": {"isco": "5131", "names": {"en": "server", "fr": "serveur de salle"}},
            "e": {"isco": "5132", "names": {"en": "bar server", "fr": "serveur de salle"}},
        },
    }
    monkeypatch.setattr(esco, "classification", lambda: document)
    esco._occupations_by_name.cache_clear()
    esco._unit_groups_by_name.cache_clear()
    yield
    esco._occupations_by_name.cache_clear()
    esco._unit_groups_by_name.cache_clear()


def test_either_form_of_a_gendered_label_finds_its_code(classification):
    assert esco.code_for("Développeur de logiciels", "fr") == "2512"
    assert esco.code_for("développeuse de logiciels", "fr") == "2512"
    assert esco.code_for("développeur de logiciels/développeuse de logiciels", "fr") == "2512"
    assert esco.code_for("waiter", "en") == "5131"


def test_a_double_space_or_a_decomposed_accent_still_matches(classification):
    assert esco.code_for("Développeur  de   logiciels", "fr") == "2512"
    assert esco.code_for("De\u0301veloppeur de logiciels", "fr") == "2512"


def test_a_name_in_two_unit_groups_matches_nothing(classification):
    assert esco.code_for("serveur de salle", "fr") == ""
