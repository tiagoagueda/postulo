"""The configuration checks: a value that cannot mean anything is refused at start-up (#233).

Each of these settings used to be read raw and surface at first use -- the first message,
the first export, the first throttled request -- as behaviour rather than as a message. They
are Django system checks now, so `manage.py check` in the entrypoint stops on them.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError

from postulo.core import checks


def ids(problems) -> set[str]:
    return {problem.id for problem in problems}


# ------------------------------------------------------------------ the vocabulary


def test_the_defaults_pass():
    """The settings as shipped say nothing wrong, or every fresh instance would refuse to start."""
    assert checks.choices() == []
    assert checks.rates() == []
    assert checks.public_url() == []
    assert checks.sizes() == []
    assert checks.footer_links() == []


@pytest.mark.parametrize(
    ("name", "typo"),
    [
        ("POSTULO_EMAIL_SECURITY", "startls"),
        ("POSTULO_EMAIL_AUTH", "oauth"),
        ("POSTULO_PDF_BACKEND", "weasy"),
        ("POSTULO_LOG_FORMAT", "text"),
    ],
)
def test_a_word_outside_the_vocabulary_is_an_error_that_names_the_vocabulary(settings, name, typo):
    setattr(settings, name, typo)

    problems = checks.choices()

    assert ids(problems) == {"postulo.E001"}
    (problem,) = problems
    assert name in problem.msg and repr(typo) in problem.msg
    for word in checks.CHOICES[name]:
        assert word in problem.msg, "the message lists what would have been accepted"


def test_every_word_in_the_vocabulary_passes(settings):
    for name, allowed in checks.CHOICES.items():
        for word in allowed:
            setattr(settings, name, word)
            assert checks.choices() == [], (name, word)


# ------------------------------------------------------------------------ rates


def test_every_rate_setting_is_found_by_its_name():
    """Any module may add one; the check picks it up without being told."""
    found = checks.rate_settings()
    assert "POSTULO_API_RATE" in found and "POSTULO_CAPTURE_RATE" in found
    assert all(name.startswith("POSTULO_") and name.endswith("_RATE") for name in found)


@pytest.mark.parametrize("value", ["30/h", "5/m", "600/d", "1/s", " 20 / h ", "", "0", None])
def test_a_rate_the_parser_reads_or_a_deliberate_no_limit_passes(settings, value):
    settings.POSTULO_CAPTURE_RATE = value
    assert checks.rates() == []


@pytest.mark.parametrize("value", ["30/hour", "thirty/h", "30", "30/", "/h", "30/w"])
def test_a_rate_the_parser_would_read_as_no_limit_is_an_error_instead(settings, value):
    """The parser's leniency is right at request time and wrong to keep quiet about."""
    settings.POSTULO_CAPTURE_RATE = value

    problems = checks.rates()

    assert ids(problems) == {"postulo.E002"}
    assert "POSTULO_CAPTURE_RATE" in problems[0].msg


# ------------------------------------------------------------------- public url


@pytest.mark.parametrize("value", ["", "https://postulo.example.org", "http://localhost:8000"])
def test_an_absent_or_reachable_public_url_passes(settings, value):
    settings.POSTULO_PUBLIC_URL = value
    assert checks.public_url() == []


@pytest.mark.parametrize("value", ["postulo.example.org", "ftp://postulo.example.org", "https://"])
def test_a_public_url_without_a_scheme_or_a_host_is_an_error(settings, value):
    settings.POSTULO_PUBLIC_URL = value

    problems = checks.public_url()

    assert ids(problems) == {"postulo.E003"}


# ------------------------------------------------------- the footer's addresses (#212)


def test_the_source_is_the_upstream_repository_as_shipped():
    from django.conf import settings as configured

    assert configured.POSTULO_SOURCE_URL == "https://source.tiagoagueda.com/postulo/postulo"
    assert configured.POSTULO_LEGAL_NOTICE_URL == ""
    assert checks.footer_links() == []


@pytest.mark.parametrize(
    "value", ["https://git.example.org/acme/postulo", "http://git.lan:3000/me/postulo"]
)
def test_a_source_a_browser_can_follow_passes(settings, value):
    settings.POSTULO_SOURCE_URL = value
    assert checks.footer_links() == []


@pytest.mark.parametrize(
    "value",
    ["", "git.example.org/acme/postulo", "git@git.example.org:acme/postulo.git", "https://"],
)
def test_a_source_that_is_not_an_address_is_an_error(settings, value):
    """Empty too: the link is the AGPL's offer of source, and an offer pointing nowhere is
    not one. Unsetting the variable is how the upstream default is taken."""
    settings.POSTULO_SOURCE_URL = value

    problems = checks.footer_links()

    assert ids(problems) == {"postulo.E006"}
    assert "POSTULO_SOURCE_URL" in problems[0].msg and repr(value) in problems[0].msg


@pytest.mark.parametrize("value", ["", "https://example.org/impressum", "http://example.org/"])
def test_an_absent_or_followable_legal_notice_passes(settings, value):
    settings.POSTULO_LEGAL_NOTICE_URL = value
    assert checks.footer_links() == []


@pytest.mark.parametrize("value", ["example.org/impressum", "mailto:legal@example.org", "https://"])
def test_a_legal_notice_that_is_not_an_address_is_an_error(settings, value):
    settings.POSTULO_LEGAL_NOTICE_URL = value

    problems = checks.footer_links()

    assert ids(problems) == {"postulo.E007"}
    assert "POSTULO_LEGAL_NOTICE_URL" in problems[0].msg


def test_manage_py_check_stops_on_a_source_that_is_not_an_address(settings):
    settings.POSTULO_SOURCE_URL = "git.example.org/acme/postulo"

    with pytest.raises(SystemCheckError, match=r"postulo\.E006"):
        call_command("check", "--tag", "postulo")


# ------------------------------------------------------ what a capture may keep (#256)


def test_every_cap_on_a_kept_page_is_checked():
    """The three a `Content-Length` is compared with, and the one that counts days."""
    from django.conf import settings as configured

    named = {
        name
        for name in dir(configured)
        if name.startswith("POSTULO_CAPTURE_") and name.endswith(("_MAX_BYTES", "_KEEP_DAYS"))
    }

    assert named == set(checks.BYTE_CAPS) | set(checks.DAY_COUNTS)
    assert len(named) == 4


@pytest.mark.parametrize("name", checks.BYTE_CAPS)
@pytest.mark.parametrize("value", [0, -1, "10 MB", None, 1.5, True])
def test_a_cap_that_cannot_be_compared_against_is_an_error(settings, name, value):
    """It is what an upload is refused against before it is read."""
    setattr(settings, name, value)

    problems = checks.sizes()

    assert ids(problems) == {"postulo.E004"}
    assert name in problems[0].msg and repr(value) in problems[0].msg
    assert "POSTULO_CAPTURE_KEEP_SOURCE" in problems[0].hint, "and it says where off is said"


@pytest.mark.parametrize("name", checks.BYTE_CAPS)
@pytest.mark.parametrize("value", [1, 2_000_000, 10 * 1024 * 1024 * 1024])
def test_a_cap_that_is_a_number_of_bytes_passes(settings, name, value):
    setattr(settings, name, value)
    assert checks.sizes() == []


@pytest.mark.parametrize("value", [-1, "a month", None, 1.5])
def test_a_count_of_days_that_is_not_one_is_an_error(settings, value):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = value

    problems = checks.sizes()

    assert ids(problems) == {"postulo.E005"}
    assert "POSTULO_CAPTURE_PAGE_KEEP_DAYS" in problems[0].msg


@pytest.mark.parametrize("value", [0, 1, 30, 3650])
def test_nought_days_means_for_ever_and_passes(settings, value):
    settings.POSTULO_CAPTURE_PAGE_KEEP_DAYS = value
    assert checks.sizes() == []


# ------------------------------------------------------------- through manage.py


def test_manage_py_check_stops_on_one(settings):
    """The entrypoint runs `check --deploy --fail-level ERROR`; an error there is a refusal."""
    settings.POSTULO_PDF_BACKEND = "weasy"

    with pytest.raises(SystemCheckError, match=r"postulo\.E001"):
        call_command("check", "--tag", "postulo")


def test_manage_py_check_passes_as_configured():
    call_command("check", "--tag", "postulo")
