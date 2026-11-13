"""A register that publishes in capitals is written the way a person would write it (#1244)."""

from __future__ import annotations

import pytest

from postulo.plugins.register_lookup import casing


@pytest.mark.parametrize(
    ("code", "spelled"),
    [
        ("AV", "Avenue"),
        ("BD", "Boulevard"),
        ("CHE", "Chemin"),
        ("RTE", "Route"),
        ("ALL", "Allée"),
        ("IMP", "Impasse"),
        ("PL", "Place"),
        ("QUAI", "Quai"),
        ("RUE", "Rue"),
        ("CRS", "Cours"),
        ("SQ", "Square"),
        ("PAS", "Passage"),
        ("ZA", "Zone d'activités"),
        ("ZI", "Zone industrielle"),
        ("AVENUE", "Avenue"),
        ("", ""),
        (None, ""),
    ],
)
def test_a_street_type_code_is_spelled_out(code, spelled):
    """The register's own code list gives the street type, and a word already in full is cased."""
    assert casing.street_type(code) == spelled


@pytest.mark.parametrize(
    ("text", "written"),
    [
        ("DES CHAMPS ELYSEES", "des Champs Elysees"),
        ("DE WAGRAM", "de Wagram"),
        ("SAINT-DENIS", "Saint-Denis"),
        ("ST DENIS", "Saint Denis"),
        ("STE MARIE", "Sainte Marie"),
        ("NEUILLY-SUR-SEINE", "Neuilly-sur-Seine"),
        ("AIX-EN-PROVENCE", "Aix-en-Provence"),
        ("L'ISLE-D'ABEAU", "l'Isle-d'Abeau"),
        ("RUE DE L'EGLISE", "Rue de l'Eglise"),
        ("LOUIS XIV", "Louis XIV"),
        ("22A", "22A"),
        ("Avenue des Champs", "Avenue des Champs"),
    ],
)
def test_a_street_or_municipality_in_capitals_is_cased(text, written):
    """Small words, elisions, hyphens, numerals and numbered words are handled word by word."""
    assert casing.place(text, french=True, first=False) == written


def test_a_municipality_starts_with_a_capital_even_when_it_starts_with_a_small_word():
    """Only a street, which follows its type, may begin with `de`; a place does not."""
    assert casing.place("LA ROCHELLE", french=True) == "La Rochelle"
    assert casing.place("PARIS", french=True) == "Paris"


@pytest.mark.parametrize(
    ("text", "keep", "written"),
    [
        ("DUPONT SERVICES", (), "Dupont Services"),
        ("AXA", (), "AXA"),
        ("SNCF", (), "SNCF"),
        ("BNP PARIBAS", (), "BNP Paribas"),
        ("ELECTRICITE DE FRANCE (EDF)", (), "Electricite de France (EDF)"),
        ("ELECTRICITE DE FRANCE", ["EDF"], "Electricite de France"),
        ("ORANGE", (), "Orange"),
        ("EDF", (), "EDF"),
        ("L'ISLE-D'ABEAU SERVICES", (), "L'Isle-d'Abeau Services"),
        ("ORANGE SA", (), "Orange SA"),
        ("CABINET DUPONT SARL", (), "Cabinet Dupont SARL"),
        ("EQUINOR ASA", (), "Equinor ASA"),
        ("L'OREAL", (), "L'Oreal"),
        ("SAINT-GOBAIN", (), "Saint-Gobain"),
        ("POLSKIE KOLEJE PAŃSTWOWE", (), "Polskie Koleje Państwowe"),
        ("Société Générale Effekten GmbH", (), "Société Générale Effekten GmbH"),
        ("", (), ""),
    ],
)
def test_a_name_in_capitals_is_cased_and_acronyms_stay(text, keep, written):
    """Acronyms (no vowel, one short word, a sigle, a word in brackets) stay in capitals."""
    assert casing.name(text, keep=keep) == written


def test_a_name_that_is_not_in_capitals_is_left_alone():
    """Only what a register publishes wholly in capitals is rewritten, never mixed case."""
    assert casing.name("Dupont & Fils SAS") == "Dupont & Fils SAS"
    assert casing.place("Forusbeen 50") == "Forusbeen 50"


def test_saint_is_spelled_out_in_french_text_only():
    """`ST` is Saint in France; for another register it is not touched."""
    assert casing.name("ST OLAVS HOSPITAL", french=True) == "Saint Olavs Hospital"
    assert casing.name("ST OLAVS HOSPITAL") == "ST Olavs Hospital"
