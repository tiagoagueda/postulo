"""The release tooling: a tag must agree with the code and the changelog before anything ships."""

import importlib.util
import json
from pathlib import Path

import pytest
from django.urls import reverse

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "release_tools", ROOT / "scripts" / "release_tools.py"
)
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "postulo"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    (tmp_path / "src" / "postulo").mkdir(parents=True)
    (tmp_path / "src" / "postulo" / "__init__.py").write_text(
        '__version__ = "0.2.0"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Something coming.\n\n"
        "## [0.2.0] — 2026-10-01\n\n### Added\n\n- Interviews.\n- Search.\n\n"
        "## [0.1.0] — 2026-09-04\n\n- The first release.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_a_matching_tag_passes_and_the_notes_are_that_section(repo):
    assert tools.check("v0.2.0", repo) == "0.2.0"
    notes = tools.changelog_section("0.2.0", repo)
    assert notes.startswith("### Added") and "- Search." in notes
    assert "Unreleased" not in notes and "first release" not in notes


def test_disagreements_are_refused_in_words(repo):
    with pytest.raises(tools.ReleaseError, match="not a release tag"):
        tools.check("0.2.0", repo)
    with pytest.raises(tools.ReleaseError, match=r"pyproject\.toml says 0\.2\.0"):
        tools.check("v0.3.0", repo)

    (repo / "src" / "postulo" / "__init__.py").write_text(
        '__version__ = "0.1.9"\n', encoding="utf-8"
    )
    with pytest.raises(tools.ReleaseError, match=r"__init__\.py says 0\.1\.9"):
        tools.check("v0.2.0", repo)
    (repo / "src" / "postulo" / "__init__.py").write_text(
        '__version__ = "0.2.0"\n', encoding="utf-8"
    )

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n- x\n", encoding="utf-8")
    with pytest.raises(tools.ReleaseError, match=r"no '## \[0\.2\.0\]' section"):
        tools.check("v0.2.0", repo)


def test_the_real_repository_agrees_with_itself():
    """pyproject.toml and __version__ must always say the same thing, tag or no tag."""
    assert tools.pyproject_version() == tools.package_version()


def test_publish_creates_the_release_once_and_attaches_what_is_missing(tmp_path, monkeypatch):
    calls = []
    state = {"release": None}

    def fake_api(method, url, token, body=None, content_type="application/json"):
        calls.append((method, url))
        if method == "GET":
            if state["release"] is None:
                raise tools.ReleaseError(f"GET {url} -> 404: not found")
            return state["release"]
        if method == "POST" and url.endswith("/releases"):
            payload = json.loads(body)
            state["release"] = {
                "id": 7,
                "name": payload["name"],
                "html_url": "https://x/r/7",
                "assets": [],
            }
            assert payload["tag_name"] == "v0.2.0" and "Interviews" in payload["body"]
            return state["release"]
        if "/assets?name=" in url:
            state["release"]["assets"].append({"name": url.split("name=")[1]})
            return {}
        raise AssertionError(url)

    monkeypatch.setattr(tools, "_api", fake_api)
    wheel = tmp_path / "postulo-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"PK")
    sdist = tmp_path / "postulo-0.2.0.tar.gz"
    sdist.write_bytes(b"\x1f\x8b")

    release = tools.publish(
        "v0.2.0",
        [wheel, sdist],
        server="https://forge.example/",
        repository="a/postulo",
        token="t",
        notes="- Interviews.\n",
    )
    assert release["name"] == "Postulo 0.2.0"
    assert [name for _m, name in calls if "/assets?name=" in name] == [
        "https://forge.example/api/v1/repos/a/postulo/releases/7/assets?name=postulo-0.2.0-py3-none-any.whl",
        "https://forge.example/api/v1/repos/a/postulo/releases/7/assets?name=postulo-0.2.0.tar.gz",
    ]

    # Run again: the release is found, nothing is created, nothing is re-uploaded.
    before = len(calls)
    tools.publish(
        "v0.2.0",
        [wheel, sdist],
        server="https://forge.example",
        repository="a/postulo",
        token="t",
        notes="x",
    )
    assert [m for m, _u in calls[before:]] == ["GET"]


def test_attach_puts_a_file_on_a_release_that_exists_and_refuses_to_invent_one(
    tmp_path, monkeypatch
):
    """The image workflow's half of a release runs after the release workflow has made it,
    so a tag with no release is a tag that workflow never saw (#251).
    """
    calls = []
    release = {"id": 9, "name": "Postulo 0.2.0", "assets": [{"name": "postulo-0.2.0.tar.gz"}]}

    def fake_api(method, url, token, body=None, content_type="application/json"):
        calls.append((method, url))
        if method == "GET" and url.endswith("/releases/tags/v0.2.0"):
            return release
        if method == "GET":
            raise tools.ReleaseError(f"GET {url} -> 404: not found")
        if "/assets?name=" in url:
            return {}
        raise AssertionError(url)

    monkeypatch.setattr(tools, "_api", fake_api)
    sbom = tmp_path / "postulo-0.2.0-image-sbom.cdx.json"
    sbom.write_text("{}", encoding="utf-8")
    sdist = tmp_path / "postulo-0.2.0.tar.gz"
    sdist.write_bytes(b"x")

    attached = tools.attach(
        "v0.2.0", [sbom, sdist], server="https://forge.example", repository="a/postulo", token="t"
    )

    assert attached == [sbom.name], "the sdist was already there and is left alone"
    assert [method for method, _url in calls] == ["GET", "POST"]

    with pytest.raises(tools.ReleaseError, match=r"no release for v0\.3\.0"):
        tools.attach(
            "v0.3.0", [sbom], server="https://forge.example", repository="a/postulo", token="t"
        )
    assert not [u for m, u in calls if m == "POST" and u.endswith("/releases")], "none invented"


def an_index(*platforms: str) -> dict:
    return {
        "manifests": [
            {
                "digest": "sha256:" + "a" * 64,
                "platform": {"os": platform.split("/")[0], "architecture": platform.split("/")[1]},
            }
            for platform in platforms
        ]
    }


def test_verify_image_wants_every_tag_every_architecture_and_one_image(monkeypatch):
    """A green release says nothing about the image (#81), and v0.3.0 had none for a day.
    Asked the way `docker pull` asks, tag by tag, and the first thing wrong is named (#251).
    """
    both = an_index("linux/amd64", "linux/arm64")
    served = dict.fromkeys(("0.2.0", "0.2", "latest"), ("sha256:" + "1" * 64, both))
    asked = []

    def fake_manifest(registry, image, reference, *, user="", token=""):
        asked.append((registry, image, reference))
        if reference not in served:
            raise tools.ReleaseError(f"{image}:{reference} is not in the registry at {registry}.")
        return served[reference]

    monkeypatch.setattr(tools, "_registry_manifest", fake_manifest)

    seen = tools.verify_image("0.2.0", registry="reg.example", image="a/postulo")
    assert seen == ["0.2.0", "0.2", "latest"], "the version, its minor and latest"
    assert asked == [("reg.example", "a/postulo", tag) for tag in seen]

    served["latest"] = ("sha256:" + "0" * 64, both)
    with pytest.raises(tools.ReleaseError, match="not one image"):
        tools.verify_image("0.2.0", registry="reg.example", image="a/postulo")

    served["latest"] = ("sha256:" + "1" * 64, an_index("linux/amd64"))
    with pytest.raises(tools.ReleaseError, match="latest is missing linux/arm64"):
        tools.verify_image("0.2.0", registry="reg.example", image="a/postulo")

    del served["0.2"]
    with pytest.raises(tools.ReleaseError, match=r"a/postulo:0\.2 is not in the registry"):
        tools.verify_image("0.2.0", registry="reg.example", image="a/postulo")


def test_the_minor_tag_is_computed_the_way_the_workflow_computes_it():
    """`${version%.*}` in image.yml: the last dotted part goes, whatever it is."""
    assert tools.image_tags("0.3.0") == ("0.3.0", "0.3", "latest")
    assert tools.image_tags("1.0.0-rc.1") == ("1.0.0-rc.1", "1.0.0-rc", "latest")


# ------------------------------------------------------ the version in the interface


@pytest.mark.django_db
def test_the_version_shows_in_the_footer_and_the_health_check(client, user):
    from postulo import __version__

    health = client.get(reverse("core:healthz")).json()
    assert health["version"] == __version__

    client.force_login(user)
    footer = client.get(reverse("core:home")).content.decode()
    assert f"Postulo {__version__}" in footer
