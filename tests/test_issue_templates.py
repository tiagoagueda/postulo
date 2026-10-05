"""The issue forms ask what a fix needs, and send a vulnerability somewhere private (#351).

The other repositories carry their own copies of these files, changed only where their
fields differ; the shared half is what this test holds, and it can be copied beside them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
FORMS = REPO / ".forgejo" / "ISSUE_TEMPLATE"
SECURITY = "postulo/postulo/src/branch/main/SECURITY.md"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def ids(form: dict) -> set[str]:
    return {f["id"] for f in form["body"] if "id" in f}


def test_bug_form_asks_for_what_a_fix_needs() -> None:
    form = load(FORMS / "bug.yaml")
    assert form["labels"] == ["bug"]
    assert {"version", "install", "database", "browser", "steps", "happened", "expected"} <= ids(
        form
    )
    required = {f["id"] for f in form["body"] if f.get("validations", {}).get("required")}
    assert {"version", "install", "database", "browser"} <= required


def test_bug_form_warns_about_personal_details_and_security() -> None:
    form = load(FORMS / "bug.yaml")
    intro = form["body"][0]["attributes"]["value"]
    assert SECURITY in intro
    assert "personal details" in intro


def test_feature_form_asks_for_the_problem_first() -> None:
    form = load(FORMS / "feature.yaml")
    assert form["labels"] == ["enhancement"]
    order = [f["id"] for f in form["body"] if "id" in f]
    assert order.index("problem") < order.index("proposal")
    assert {"alternatives", "commitments"} <= set(order)


def test_config_links_security_and_keeps_blank_issues() -> None:
    config = load(FORMS / "config.yaml")
    assert config["blank_issues_enabled"] is True
    assert SECURITY in config["contact_links"][0]["url"]


def test_github_mirror_sends_people_to_forgejo() -> None:
    config = load(REPO / ".github" / "ISSUE_TEMPLATE" / "config.yml")
    assert config["blank_issues_enabled"] is False
    assert "source.tiagoagueda.com" in config["contact_links"][0]["url"]


def test_contributing_names_the_forms() -> None:
    text = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Bug report" in text and "Feature request" in text
