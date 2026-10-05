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


LINES = "services:{n}  postulo:{n}    image: source.example/postulo/postulo:{{tag}}{n}".format(
    n=chr(10)
) + chr(10)
DB = "  db:" + chr(10) + "    image: postgres:17-alpine" + chr(10)


def test_a_compose_file_naming_another_minor_is_refused(repo):
    """#403: nothing moved the image pin with the version, so installs ran the last minor."""
    (repo / "docker").mkdir()
    compose = repo / "docker" / "compose.yml"
    old = LINES.format(tag="0.1")
    compose.write_text(old, encoding="utf-8")
    with pytest.raises(tools.ReleaseError, match=r"compose\.yml names the image :0\.1, not :0\.2"):
        tools.check("v0.2.0", repo)

    compose.write_text(
        LINES.format(tag="0.2") + DB,
        encoding="utf-8",
    )
    assert tools.check("v0.2.0", repo) == "0.2.0"


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


def test_a_release_pushes_its_minor_and_latest_but_a_prerelease_only_itself():
    assert tools.image_tags("0.3.0") == ("0.3.0", "0.3", "latest")
    for version in ("0.4.0-rc.1", "0.4.1-rc1", "1.0.0-beta"):
        assert tools.is_prerelease(version)
        assert tools.image_tags(version) == (version,)
    assert not tools.is_prerelease("0.4.0")


def test_a_build_suffix_is_not_a_release_tag():
    assert tools.version_of_tag("v0.4.0-rc.1") == "0.4.0-rc.1"
    with pytest.raises(tools.ReleaseError):
        tools.version_of_tag("v0.4.0+build.1")


def test_the_tags_command_prints_what_the_workflow_pushes(capsys):
    assert tools.main(["tags", "v0.4.1-rc1", "--image", "r/o/postulo"]) == 0
    assert capsys.readouterr().out.strip() == "r/o/postulo:0.4.1-rc1"
    assert tools.main(["tags", "v0.4.0", "--image", "r/o/postulo"]) == 0
    assert capsys.readouterr().out.strip() == "r/o/postulo:0.4.0,r/o/postulo:0.4,r/o/postulo:latest"


# ------------------------------------------------------ the version in the interface


@pytest.mark.django_db
def test_the_version_shows_in_the_footer_and_the_health_check(client, user):
    from postulo import __version__

    health = client.get(reverse("core:healthz")).json()
    assert health["version"] == __version__

    client.force_login(user)
    footer = client.get(reverse("core:home")).content.decode()
    assert f"Postulo {__version__}" in footer


# ------------------------------------------------------------- the CI gate (#233)


def statuses(*pairs):
    """What Forgejo's combined status says: the latest status per context."""
    return {
        "state": "failure",
        "statuses": [{"context": context, "status": status} for context, status in pairs],
    }


GREEN = statuses(
    ("CI / Unit tests (Python 3.12) (push)", "success"),
    ("CI / Unit tests (Python 3.13) (push)", "success"),
    ("CI / Unit tests and coverage (Python 3.14) (push)", "success"),
    ("CI / Browser tests (Chromium) (push)", "success"),
    ("CI / Checks: lint, migrations, catalogues, settings, built files (push)", "success"),
    ("CI / Security audit (push)", "success"),
    # A registry timeout on the dev image says nothing about the code, and the combined
    # state above is "failure" because of it; the gate reads the jobs, not the state.
    ("Dev image / Dev image: build, scan, push (push)", "failure"),
)


def asking(monkeypatch, answer):
    asked = []

    def api(method, url, token, body=None, content_type="application/json"):
        asked.append((method, url, token))
        return answer

    monkeypatch.setattr(tools, "_api", api)
    return asked


def test_every_test_leg_and_the_browser_green_is_a_go(monkeypatch):
    asked = asking(monkeypatch, GREEN)

    problems = tools.ci_problems(
        "v0.3.0", server="https://forge.example.org", repository="postulo/postulo", token="t"
    )

    assert problems == []
    assert asked == [
        ("GET", "https://forge.example.org/api/v1/repos/postulo/postulo/commits/v0.3.0/status", "t")
    ]


def test_one_red_leg_is_named(monkeypatch):
    asking(
        monkeypatch,
        statuses(
            ("CI / Unit tests (Python 3.12) (push)", "success"),
            ("CI / Unit tests (Python 3.13) (push)", "success"),
            ("CI / Unit tests and coverage (Python 3.14) (push)", "failure"),
            ("CI / Browser tests (Chromium) (push)", "success"),
            ("CI / Checks: lint, migrations, catalogues (push)", "success"),
        ),
    )

    problems = tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t")

    assert problems == ["CI / Unit tests and coverage (Python 3.14) (push): failure"]


def test_a_leg_still_running_is_not_a_pass(monkeypatch):
    asking(
        monkeypatch,
        statuses(
            ("CI / Unit tests (Python 3.12) (workflow_dispatch)", "success"),
            ("CI / Unit tests (Python 3.13) (workflow_dispatch)", "success"),
            ("CI / Unit tests and coverage (Python 3.14) (push)", "pending"),
            ("CI / Browser tests (Chromium) (push)", "success"),
            ("CI / Checks: lint, migrations, catalogues (push)", "success"),
        ),
    )

    problems = tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t")

    assert problems == ["CI / Unit tests and coverage (Python 3.14) (push): pending"]


def test_no_ci_at_all_is_refused_rather_than_waved_through(monkeypatch):
    """A tag on a commit CI never saw has no red job; that is not the same as green."""
    asking(monkeypatch, statuses(("Dev image / Dev image: build, scan, push (push)", "success")))

    problems = tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t")

    assert len(problems) == 6
    assert any("a test leg" in p for p in problems) and any("the browser" in p for p in problems)
    assert any("the checks" in p for p in problems)
    assert sum("no unit tests on Python" in p for p in problems) == 3


def test_the_checks_are_required_now_that_they_left_the_test_legs(monkeypatch):
    """Lint, the migrations and the catalogues used to fail a test leg; since #712 they run
    once, in a job of their own, and a release over a red one is still refused."""
    asking(
        monkeypatch,
        statuses(
            ("CI / Unit tests (Python 3.12) (workflow_dispatch)", "success"),
            ("CI / Unit tests (Python 3.13) (workflow_dispatch)", "success"),
            ("CI / Unit tests and coverage (Python 3.14) (push)", "success"),
            ("CI / Browser tests (Chromium) (push)", "success"),
            ("CI / Checks: lint, migrations, translations (push)", "failure"),
        ),
    )

    problems = tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t")

    assert problems == ["CI / Checks: lint, migrations, translations (push): failure"]


#: What a push to `main` leaves on its commit since #713: the newest Python only.
PUSHED = (
    ("CI / Unit tests and coverage (Python 3.14) (push)", "success"),
    ("CI / Browser tests (Chromium) (push)", "success"),
    ("CI / Checks: lint, migrations, catalogues, settings, built files (push)", "success"),
)
A_PUSH = statuses(*PUSHED)


def test_a_push_alone_does_not_make_a_release(monkeypatch):
    """A push tests the newest Python; a release promises every one the classifiers name."""
    asking(monkeypatch, A_PUSH)

    problems = tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t")

    assert [p.split(" for ")[0] for p in problems] == [
        "no unit tests on Python 3.12",
        "no unit tests on Python 3.13",
    ]
    assert all("start *Every Python* by hand" in p for p in problems), problems


def test_a_push_and_every_python_by_hand_make_a_release(monkeypatch):
    asking(
        monkeypatch,
        statuses(
            *PUSHED,
            ("Every Python / Unit tests (Python 3.12) (workflow_dispatch)", "success"),
            ("Every Python / Unit tests (Python 3.13) (workflow_dispatch)", "success"),
        ),
    )

    assert tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t") == []


def test_a_red_leg_in_every_python_is_named(monkeypatch):
    asking(
        monkeypatch,
        statuses(
            *PUSHED,
            ("Every Python / Unit tests (Python 3.12) (workflow_dispatch)", "failure"),
            ("Every Python / Unit tests (Python 3.13) (workflow_dispatch)", "success"),
        ),
    )

    problems = tools.ci_problems("v0.3.0", server="https://f", repository="o/r", token="t")

    assert problems == ["Every Python / Unit tests (Python 3.12) (workflow_dispatch): failure"]


def test_ci_and_every_python_together_test_every_supported_python():
    """One job in two workflows: CI's on every push, the same job on the other Pythons in
    every-python.yml. A step changed in one and not the other is a Python that stops being
    tested the way the newest is (#713)."""
    import yaml

    workflows = ROOT / ".forgejo" / "workflows"
    newest = yaml.safe_load((workflows / "ci.yml").read_text(encoding="utf-8"))["jobs"]["test"]
    rest = yaml.safe_load((workflows / "every-python.yml").read_text(encoding="utf-8"))["jobs"][
        "test"
    ]

    def without_matrix(job):
        return {key: value for key, value in job.items() if key != "strategy"}

    assert without_matrix(newest) == without_matrix(rest), "the two copies of the job differ"
    supported = tools.supported_pythons()
    assert newest["strategy"]["matrix"]["python-version"] == [supported[-1]]
    assert rest["strategy"]["matrix"]["python-version"] == list(supported[:-1])


def test_a_push_alone_is_all_a_push_is_asked_for(monkeypatch):
    asking(monkeypatch, A_PUSH)

    assert (
        tools.ci_problems(
            "abc123", server="https://f", repository="o/r", token="t", every_python=False
        )
        == []
    )


def test_the_supported_pythons_are_the_classifiers():
    assert tools.supported_pythons() == ("3.12", "3.13", "3.14")


def test_the_check_command_asks_only_when_told_to(monkeypatch, capsys):
    """`check vX.Y.Z` stays what it was; `--ci` adds the question and needs the token."""
    asked = asking(monkeypatch, GREEN)
    version = "v" + tools.pyproject_version()

    assert tools.main(["check", version]) == 0
    assert asked == []

    monkeypatch.setenv("FORGEJO_URL", "https://f")
    monkeypatch.setenv("FORGEJO_REPOSITORY", "o/r")
    monkeypatch.setenv("FORGEJO_TOKEN", "t")
    assert tools.main(["check", version, "--ci"]) == 0
    assert len(asked) == 1
    assert "every test leg and the browser job passed" in capsys.readouterr().out


def test_the_check_command_fails_in_words_when_ci_did_not_pass(monkeypatch, capsys):
    asking(monkeypatch, statuses(("CI / Unit tests and coverage (Python 3.14) (push)", "failure")))
    version = "v" + tools.pyproject_version()
    monkeypatch.setenv("FORGEJO_URL", "https://f")
    monkeypatch.setenv("FORGEJO_REPOSITORY", "o/r")
    monkeypatch.setenv("FORGEJO_TOKEN", "t")

    assert tools.main(["check", version, "--ci"]) != 0

    said = capsys.readouterr().err
    assert (
        "CI has not passed" in said
        and "CI / Unit tests and coverage (Python 3.14) (push): failure" in said
    )


# ------------------------------------------- waiting for CI before the dev image (#716)


def answering(monkeypatch, *answers):
    """`_api` giving each call the next of ``answers``, and the last one for ever after."""
    queue = list(answers)
    asked = []

    def api(method, url, token, body=None, content_type="application/json"):
        asked.append(url)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(tools, "_api", api)
    return asked


class Clock:
    """A clock that moves only when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


RUNNING = statuses(
    ("CI / Unit tests and coverage (Python 3.14) (push)", "success"),
    ("CI / Browser tests (Chromium) (push)", "pending"),
    ("CI / Checks: lint, migrations, catalogues, settings, built files (push)", "success"),
)


def wait(clock, **options):
    return tools.wait_for_ci(
        "abc123",
        server="https://f",
        repository="o/r",
        token="t",
        sleep=clock.sleep,
        clock=clock,
        **options,
    )


def test_the_dev_image_waits_for_ci_and_goes_once_it_passes(monkeypatch):
    clock = Clock()
    asked = answering(monkeypatch, statuses(), RUNNING, A_PUSH)

    assert wait(clock) == []
    assert len(asked) == 3, "not recorded yet, then running, then passed"
    assert clock.now == 60


def test_a_failed_job_ends_the_wait_without_waiting_for_the_rest(monkeypatch):
    """The browser is still running, but a red check already means no image."""
    clock = Clock()
    answering(
        monkeypatch,
        statuses(
            ("CI / Unit tests and coverage (Python 3.14) (push)", "pending"),
            ("CI / Browser tests (Chromium) (push)", "pending"),
            ("CI / Checks: lint (push)", "failure"),
        ),
    )

    problems = wait(clock)

    assert "CI / Checks: lint (push): failure" in problems
    assert clock.now == 0


def test_a_run_cancelled_by_a_newer_push_builds_nothing(monkeypatch):
    clock = Clock()
    answering(
        monkeypatch,
        statuses(*PUSHED[::2], ("CI / Browser tests (Chromium) (push)", "cancelled")),
    )

    assert wait(clock) == ["CI / Browser tests (Chromium) (push): cancelled"]


def test_the_wait_gives_up_and_says_so(monkeypatch):
    clock = Clock()
    answering(monkeypatch, RUNNING)

    problems = wait(clock, minutes=2)

    assert problems[-1] == "CI had not answered for abc123 after 2 minutes."
    assert clock.now == 120


def test_a_push_is_not_asked_for_every_python(monkeypatch):
    """The older Pythons run weekly and before a release; a push's image cannot wait for them."""
    clock = Clock()
    answering(monkeypatch, A_PUSH)

    assert wait(clock) == []


def test_the_wait_command_fails_unless_ci_passed(monkeypatch, capsys):
    monkeypatch.setenv("FORGEJO_URL", "https://f")
    monkeypatch.setenv("FORGEJO_REPOSITORY", "o/r")
    monkeypatch.setenv("FORGEJO_TOKEN", "t")
    monkeypatch.setattr(tools.time, "sleep", lambda seconds: None)
    answering(monkeypatch, statuses(*PUSHED[:2], ("CI / Checks: lint (push)", "failure")))

    assert tools.main(["wait-ci", "abc123"]) == 1
    assert "CI / Checks: lint (push): failure" in capsys.readouterr().err

    answering(monkeypatch, A_PUSH)
    assert tools.main(["wait-ci", "abc123"]) == 0
    assert "abc123: CI passed." in capsys.readouterr().out


def test_a_failed_lookup_is_asked_again_rather_than_ending_the_wait(monkeypatch):
    """Run 733 lost its dev image to one `Temporary failure in name resolution`."""
    import urllib.error

    clock = Clock()
    answers = [urllib.error.URLError("Temporary failure in name resolution"), A_PUSH]

    def api(method, url, token, body=None, content_type="application/json"):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(tools, "_api", api)

    assert wait(clock) == []
    assert clock.now == 30


def test_a_network_that_never_comes_back_gives_up_at_the_deadline(monkeypatch):
    import urllib.error

    clock = Clock()

    def api(method, url, token, body=None, content_type="application/json"):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(tools, "_api", api)

    problems = wait(clock, minutes=1)

    assert len(problems) == 1 and "could not ask Forgejo" in problems[0]
