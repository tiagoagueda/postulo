"""Where a plugin came from, derived rather than declared (#94).

> plugins kinds: internal (shipped with postulo), oficial plugin (downloaded from official
> repo, or thru a zip) custom plugin (downloaded from custom repo, or thru a zip)

The difficulty is entirely in the second kind, and it is one sentence: **a zip is a zip.** A
file somebody uploads carries no evidence of who published it. Calling it official because it
is named after an official plugin would be worse than not labelling it — so the label here
describes evidence or it says "uploaded", and there is no third answer.

The evidence is a checksum in a signed index. It covers the *bytes*, so a wheel that arrived
by upload and the same wheel downloaded from the repository are the same wheel, and one byte
of difference means they are not.
"""

from __future__ import annotations

import pytest

from postulo.plugins import provenance
from postulo.plugins.catalogue import Catalogue, Listing, Release
from postulo.plugins.installing import Installed

pytestmark = pytest.mark.django_db

OFFICIAL_KEY = "bW9jay1vZmZpY2lhbC1rZXk="
OTHER_KEY = "c29tZWJvZHktZWxzZXMta2V5"
DIGEST = "a" * 64


@pytest.fixture
def a_signed_index(monkeypatch):
    """Two repositories, one of which this instance was built trusting."""

    def configured():
        return {
            "postulo": {"url": "https://example.test/i.json", "key": OFFICIAL_KEY},
            "someone": {"url": "https://other.test/i.json", "key": OTHER_KEY},
        }

    def fetch_all():
        return (
            [
                Catalogue(
                    name="postulo",
                    url="https://example.test/i.json",
                    public_key=OFFICIAL_KEY,
                    listings=[
                        Listing(
                            name="postulo-example",
                            releases=(Release(version="1.0", url="u", sha256=DIGEST),),
                        )
                    ],
                ),
                Catalogue(
                    name="someone",
                    url="https://other.test/i.json",
                    public_key=OTHER_KEY,
                    listings=[
                        Listing(
                            name="postulo-theirs",
                            releases=(Release(version="2.0", url="u", sha256="b" * 64),),
                        )
                    ],
                ),
            ],
            [],
        )

    from postulo.plugins import catalogue

    monkeypatch.setattr(catalogue, "configured", configured)
    monkeypatch.setattr(catalogue, "fetch_all", fetch_all)
    monkeypatch.setattr(provenance, "OFFICIAL_KEYS", (OFFICIAL_KEY,))


# ------------------------------------------------------------- the three kinds


def test_nothing_is_official_until_postulo_publishes_a_catalogue():
    """The shipped answer, and it is a fact rather than an unfinished edge.

    `OFFICIAL_KEYS` is empty because there is no official repository yet, so every plugin an
    instance has today is custom or uploaded — which is what the page should say.
    """
    assert provenance.OFFICIAL_KEYS == ()
    assert provenance.official_repositories() == set()


def test_a_built_in_is_internal_and_cannot_be_removed():
    from postulo.plugins.builtin import BUILTIN_SOURCES

    mark = provenance.of_builtin(BUILTIN_SOURCES[0]())

    assert mark.kind == provenance.INTERNAL
    assert mark.removable is False


def test_a_download_from_the_official_repository_is_official(a_signed_index):
    entry = Installed(name="postulo-example", version="1.0", origin="catalogue:postulo")

    mark = provenance.of_record(entry)

    assert mark.kind == provenance.OFFICIAL
    assert mark.repository == "postulo"


def test_a_download_from_any_other_repository_is_custom(a_signed_index):
    """Not a lesser thing — most repositories are somebody's, and that is the point of
    letting an operator add one. It is simply not *ours*, and the page says which.
    """
    entry = Installed(name="postulo-theirs", version="2.0", origin="catalogue:someone")

    mark = provenance.of_record(entry)

    assert mark.kind == provenance.CUSTOM
    assert mark.repository == "someone"


# ------------------------------------------------------- and the difficult one


def test_an_upload_whose_checksum_the_official_repository_signed_is_official(a_signed_index):
    """The honest way to label a zip: the signature covers the bytes, not the delivery.

    So a file that reaches the instance by upload and the file that repository published are
    the same file, or they are not, and a checksum settles which.
    """
    entry = Installed(name="postulo-example", version="1.0", origin="upload", sha256=DIGEST)

    mark = provenance.of_record(entry)

    assert mark.kind == provenance.OFFICIAL
    assert mark.repository == "postulo"


def test_the_same_file_with_one_byte_changed_is_not(a_signed_index):
    """Which is the entire value of doing it this way rather than by name."""
    changed = DIGEST[:-1] + ("b" if DIGEST[-1] == "a" else "a")
    entry = Installed(name="postulo-example", version="1.0", origin="upload", sha256=changed)

    mark = provenance.of_record(entry)

    assert mark.kind == provenance.UPLOADED
    assert mark.repository == ""


def test_an_upload_matching_nothing_says_so_plainly(a_signed_index):
    entry = Installed(name="postulo-mine", version="0.1", origin="upload", sha256="c" * 64)

    mark = provenance.of_record(entry)

    assert mark.kind == provenance.UPLOADED
    assert "vouches" in str(mark.explanation)


def test_when_no_repository_can_be_reached_the_answer_is_uploaded_and_not_a_guess(monkeypatch):
    """The case the issue is most careful about.

    A repository that is switched off, or whose index will not fetch or will not verify,
    contributes no checksums — and the label must fall to "uploaded" rather than to whatever
    it was last time or to a guess from the name.
    """
    from postulo.plugins import catalogue

    monkeypatch.setattr(catalogue, "fetch_all", lambda: ([], ["postulo: unreachable"]))
    entry = Installed(name="postulo-example", version="1.0", origin="upload", sha256=DIGEST)

    assert provenance.of_record(entry).kind == provenance.UPLOADED


# ------------------------------------------------- what the label must not mean


def test_the_page_does_not_let_a_badge_undo_its_warning(admin_client):
    """`Official` is exactly the kind of word that quietly means "safe".

    So the page that shows it still says, on the same page, that installing a plugin runs
    somebody else's code — and the explanation beside each badge talks about a file matching
    a signature rather than about trust.
    """
    from django.urls import reverse

    html = admin_client.get(reverse("server:plugins")).content.decode()

    assert "runs" in html and "code" in html, "the warning about running somebody's code"
    for wording in provenance.EXPLANATIONS.values():
        assert "safe" not in str(wording).lower()


def test_the_badge_says_nothing_about_dependencies():
    """`installing.py` is explicit that the checksum covers the plugin's own wheel and that
    its requirements come from PyPI at install time. An official plugin's dependencies are
    as unverified as anybody's, and none of these sentences may appear to cover them.
    """
    for wording in provenance.EXPLANATIONS.values():
        assert "dependenc" not in str(wording).lower()


# ------------------------------------------------------- a built-in cannot be removed


def test_the_page_lists_what_the_instance_can_do_and_not_only_what_was_installed(admin_client):
    """Somebody asking "can this instance read a posting off a page" was looking in the
    wrong place: the built-in sources never pass through the installer, so the page listed
    everything except them.
    """
    from django.urls import reverse

    html = admin_client.get(reverse("server:plugins")).content.decode()

    assert 'data-plugin="schema.org"' in html
    assert 'data-provenance="internal"' in html


@pytest.mark.parametrize("name", ["schema.org", "europass", "local", "smtp"])
def test_a_built_in_cannot_be_removed_through_any_route(name):
    from postulo.plugins import installing

    assert installing.is_internal(name)
    with pytest.raises(installing.InstallError, match="ships inside"):
        installing.remove(name)
    with pytest.raises(installing.InstallError, match="ships inside"):
        installing.set_disabled(name, True)


def test_a_built_in_offers_no_button_that_could_not_work(admin_client):
    """Rather than a control that fails when pressed, or a row left out of the page."""
    from django.urls import reverse

    html = admin_client.get(reverse("server:plugins")).content.decode()
    row = html.split('data-plugin="schema.org"')[1].split("</li>")[0]

    assert "Remove" not in row
    assert "Switch off" not in row
