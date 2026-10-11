"""The structure module says companies, and keeps its internal name (#881)."""

from __future__ import annotations

from pathlib import Path

from postulo.plugins.api import description_of, label_of
from postulo.plugins.employer_structure import EMPLOYER_STRUCTURE, EmployerStructureFeature

ICONS = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "icons"


def test_the_label_and_description_say_companies_and_the_internal_name_stays():
    assert EMPLOYER_STRUCTURE == "employer-structure"
    assert str(label_of(EmployerStructureFeature)) == "Companies with a structure"
    assert "employer" not in str(description_of(EmployerStructureFeature)).lower()


def test_the_pyramid_icon_is_shipped_for_its_logo():
    assert (ICONS / "pyramid.svg").is_file()
