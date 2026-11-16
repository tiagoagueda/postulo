"""ESCO as a bundled `suggestion-source` plugin (#1252, slice 2).

The classification is read by `postulo.plugins.esco`, and the rest of Postulo asks for it
through `postulo.core.occupations`, which asks the registry. With the plugin switched off, or
absent, every form and model still works with plainer fields, and what was kept stays kept.
"""

from __future__ import annotations

import pytest

from postulo.core import occupations, suggestion_sources
from postulo.core.memo import forget_decisions
from postulo.jobs.forms import JobPostingForm
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import registry
from postulo.plugins.esco import classification as esco
from postulo.plugins.models import PluginPolicy
from postulo.resume.models import Skill

from .conftest import write_esco_occupations, write_esco_skills
from .test_esco_occupations import DOCUMENT


@pytest.fixture
def classification(tmp_path, monkeypatch):
    """The small classification and skills files where the loader looks, dropped either side."""
    directory = tmp_path / "esco"
    directory.mkdir()
    write_esco_occupations(directory, DOCUMENT)
    write_esco_skills(directory)
    monkeypatch.setattr(esco, "DATA_DIR", directory)
    esco.forget()
    yield directory
    esco.forget()


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Acme")


@pytest.fixture
def esco_off(user):
    """ESCO switched off for the instance."""
    PluginPolicy.objects.create(plugin="esco", person=None, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()


def test_esco_is_a_shipped_offline_licensed_suggestion_source_for_titles():
    plugin = registry.find_plugin("suggestion-source", "esco")
    (source,) = plugin.sources

    assert plugin.kind == "suggestion-source"
    assert source.offline and not source.hosts
    assert source.licence and source.fields == ("job_title",)
    assert plugin.classification is esco


def test_the_registry_offers_the_unit_groups_for_a_title(db, classification):
    offered = suggestion_sources.offered("job_title", language="fr")

    assert [text for text, _group in offered] == [
        "Analystes de systèmes",
        "Concepteurs de logiciels",
        "Serveurs",
    ]
    assert suggestion_sources.offered("job_title", exclude=["Serveurs"], language="fr")[-1][0] != (
        "Serveurs"
    )


def test_the_registry_answers_the_questions_the_models_ask(db, classification):
    assert occupations.available()
    assert occupations.code_for("head waiter") == "5131"
    assert occupations.name_for("5131", "fr") == "Serveurs"
    assert occupations.divisions_for("2512") == frozenset({"62", "63"})
    assert occupations.skills_available()
    assert occupations.skill_suggestions("spread", "en")


def test_a_posting_takes_its_code_from_the_plugin(user, company, classification):
    posting = JobPosting.objects.create(owner=user, company=company, title="head waiter")

    assert posting.isco_code == "5131"
    assert posting.isco_name == "Waiters"


def test_a_title_and_a_skill_with_the_plugin_off_are_plain_and_keep_what_is_kept(
    user, company, classification, esco_off
):
    assert not occupations.available()
    assert occupations.code_for("head waiter") == ""
    assert occupations.name_for("5131") == ""
    assert occupations.divisions_for("2512") == frozenset()
    assert occupations.skill_suggestions("spread", "en") == []
    assert occupations.skills_named("I use spreadsheet software", "en") == ()
    assert not occupations.skills_available()
    assert suggestion_sources.offered("job_title", user=user) == []

    posting = JobPosting(owner=user, company=company, title="head waiter", isco_code="5131")
    posting.save()
    assert posting.isco_code == "5131", "not worked out again, and not cleared"
    skill = Skill(owner=user, name="anything", esco_uri="http://data.europa.eu/esco/skill/kept")
    skill.save()
    assert skill.esco_uri.endswith("/kept")


def test_the_forms_work_with_the_plugin_off(user, classification, esco_off):
    form = JobPostingForm(user=user)

    assert form.datalists["title-suggestions"] == []
    assert "title" in form.fields


def test_the_forms_work_with_the_plugin_absent(user, company, monkeypatch):
    monkeypatch.setitem(registry._builtin, "suggestion-source", [])
    registry._cache.pop("suggestion-source", None)
    try:
        assert not occupations.available()
        assert occupations.code_for("head waiter") == ""
        assert JobPostingForm(user=user).datalists["title-suggestions"] == []
        posting = JobPosting.objects.create(
            owner=user, company=company, title="head waiter", isco_code="5131"
        )
        assert posting.isco_code == "5131", "kept, since nothing is asked"
    finally:
        registry._cache.pop("suggestion-source", None)


def test_core_imports_nothing_of_the_plugin():
    from pathlib import Path

    source = Path("src/postulo/core/occupations.py").read_text(encoding="utf-8")

    imports = [
        line for line in source.splitlines() if line.strip().startswith(("import ", "from "))
    ]
    assert not [line for line in imports if "esco" in line]
