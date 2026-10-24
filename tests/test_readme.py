"""The README's generated parts stay in step with their sources (#936, #937, #938).

- the language table is what ``scripts/readme_languages.py`` writes from the registry;
- ``.github/README.md`` is what ``scripts/github_readme.py`` writes from the README;
- the badges that state a version agree with ``pyproject.toml``;
- nothing in ``.github/`` is something Forgejo would pick up instead of its own.
"""

from __future__ import annotations

import importlib.util
import os
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_language_table_is_current():
    """On a contributor's machine; in CI only that the table can still be written.

    The table follows the translations, which land from Weblate, so it is stale on every
    Weblate pull request and on ``main`` until the README workflow's pull request brings
    it up to date (#1044). Comparing it in CI turned those runs red for nothing.
    """
    script = _script("readme_languages")
    if os.environ.get("CI"):
        script.render((REPO / "README.md").read_text(encoding="utf-8"))
        pytest.skip("CI: .forgejo/workflows/readme.yml keeps the table current (#1044)")
    assert script.main(["--check"]) == 0, "run scripts/readme_languages.py"


def test_the_tagline_the_script_repeats_is_the_translatable_one():
    from postulo.core.tagline import TAGLINE

    assert _script("readme_languages").SOURCE_SENTENCE == TAGLINE


def test_every_flag_in_the_table_exists():
    section = (REPO / "README.md").read_text(encoding="utf-8")
    for path in re.findall(r'<img src="(src/postulo/static/flags/[^"]+)"', section):
        assert (REPO / path).is_file(), path


def test_the_github_readme_is_current():
    assert _script("github_readme").main(["--check"]) == 0, "run scripts/github_readme.py"


def test_the_github_readme_has_no_relative_links():
    text = (REPO / ".github" / "README.md").read_text(encoding="utf-8")
    assert text.count("This is a read-only mirror") == 1
    targets = re.findall(r"\]\(([^)\s]+)", text) + re.findall(r'(?:src|href)="([^"]+)"', text)
    relative = [t for t in targets if not re.match(r"([a-z][a-z0-9+.-]*:|//|#)", t, re.I)]
    assert not relative


def test_the_badges_state_the_versions_pyproject_requires():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    python = re.search(r">=(\d+\.\d+)", project["requires-python"])[1]
    assert f"badge/python-{python}%2B" in readme
    django = next(d for d in project["dependencies"] if d.lower().startswith("django>="))
    assert f"badge/django-{re.search(r'>=(\d+\.\d+)', django)[1]}-" in readme


def test_the_row_of_badges_is_short():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    head = readme.split("**Self-hosted job application manager", 1)[0]
    assert 8 <= len(re.findall(r"\[!\[", head)) <= 10


def test_forgejo_never_falls_back_to_a_github_only_file():
    """Forgejo reads ``.github/`` for templates and workflows when ``.forgejo/`` has none."""
    github, forgejo = REPO / ".github", REPO / ".forgejo"
    for folder in ("workflows", "ISSUE_TEMPLATE"):
        if (github / folder).is_dir():
            assert (forgejo / folder).is_dir() and any((forgejo / folder).iterdir()), folder
    assert not (github / "workflows").is_dir() or not (
        {p.name for p in (github / "workflows").iterdir()}
        - {p.name for p in (forgejo / "workflows").iterdir()}
    )
    assert (
        not (github / "PULL_REQUEST_TEMPLATE.md").exists()
        or (forgejo / "PULL_REQUEST_TEMPLATE.md").exists()
    )
