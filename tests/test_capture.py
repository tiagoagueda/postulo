"""Capturing postings: parsing, the safety rules around fetching, and the plugin registry.

Nothing here reaches the network. The parsers are given HTML directly, and the fetching
rules are exercised against a stubbed resolver, because a test that depends on somebody
else's website is a test that fails for reasons which have nothing to do with Postulo.
"""

import json

import pytest

from postulo.jobs.models import Capture, CaptureStatus
from postulo.plugins import fetching
from postulo.plugins.base import JobPostingData
from postulo.plugins.builtin import PageMetadataSource, SchemaOrgSource
from postulo.plugins.htmlutil import extract_jsonld, extract_meta, html_to_text
from postulo.plugins.registry import parse_page


def page_with_jsonld(payload: dict) -> str:
    return (
        "<html><head><title>Ignore me</title>"
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        "</head><body><p>Body text</p></body></html>"
    )


FULL_POSTING = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Senior Backend Engineer",
    "hiringOrganization": {"@type": "Organization", "name": "Aperture Science"},
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Paris",
            "addressCountry": "FR",
        },
    },
    "employmentType": "FULL_TIME",
    "jobLocationType": "TELECOMMUTE",
    "datePosted": "2026-08-01",
    "validThrough": "2026-10-01T23:59:59",
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "EUR",
        "value": {
            "@type": "QuantitativeValue",
            "minValue": 65000,
            "maxValue": 80000,
            "unitText": "YEAR",
        },
    },
    "description": "<p>Build <b>things</b>.</p><ul><li>Python</li><li>Go</li></ul>",
}


# --------------------------------------------------------------- reading a page


def test_a_schema_org_posting_is_read_in_full():
    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(FULL_POSTING))

    assert data.title == "Senior Backend Engineer"
    assert data.company_name == "Aperture Science"
    assert data.location == "Paris, FR"
    assert data.remote_type == "remote"
    assert data.employment_type == "full_time"
    assert (data.salary_min, data.salary_max) == (65000, 80000)
    assert data.salary_currency == "EUR"
    assert data.salary_period == "year"
    assert str(data.posted_at) == "2026-08-01"
    assert str(data.closes_at) == "2026-10-01"


def test_html_inside_a_description_becomes_readable_text():
    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(FULL_POSTING))

    assert "<b>" not in data.description
    assert "Build things." in data.description
    assert "Python" in data.description


def test_a_posting_inside_a_graph_is_found():
    """Real sites wrap their structured data in @graph as often as not."""
    wrapped = {"@context": "https://schema.org/", "@graph": [{"@type": "WebSite"}, FULL_POSTING]}

    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(wrapped))

    assert data is not None
    assert data.title == "Senior Backend Engineer"


def test_a_page_with_no_posting_yields_nothing_from_the_schema_source():
    page = page_with_jsonld({"@type": "WebSite", "name": "Not a job"})

    assert SchemaOrgSource().parse("https://example.org/", page) is None


def test_one_malformed_block_does_not_lose_the_others():
    page = (
        '<html><head><script type="application/ld+json">{ this is not json</script>'
        f'<script type="application/ld+json">{json.dumps(FULL_POSTING)}</script></head></html>'
    )

    assert len(extract_jsonld(page)) == 1
    assert SchemaOrgSource().parse("https://example.org/j/1", page).title


def test_a_posting_without_a_title_is_refused():
    """A capture with no title would be a row of empty fields pretending to be a job."""
    page = page_with_jsonld({"@type": "JobPosting", "hiringOrganization": {"name": "Acme"}})

    assert SchemaOrgSource().parse("https://example.org/j/1", page) is None


@pytest.mark.parametrize(
    "declared,expected",
    [("FULL_TIME", "full_time"), ("CONTRACTOR", "contract"), ("INTERN", "internship")],
)
def test_employment_types_are_translated(declared, expected):
    posting = {**FULL_POSTING, "employmentType": declared}

    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(posting))

    assert data.employment_type == expected


def test_an_unrecognised_employment_type_is_left_empty_rather_than_guessed():
    posting = {**FULL_POSTING, "employmentType": "SEASONAL_WHATEVER"}

    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(posting))

    assert data.employment_type == ""


def test_the_fallback_uses_what_the_page_says_about_itself():
    page = (
        "<html><head><title>Junior Dev - Black Mesa</title>"
        '<meta property="og:site_name" content="Black Mesa"></head>'
        "<body><script>var x = 1</script><p>We need someone.</p></body></html>"
    )

    data = PageMetadataSource().parse("https://example.org/j/1", page)

    assert data.title == "Junior Dev - Black Mesa"
    assert data.company_name == "Black Mesa"
    assert "We need someone." in data.description
    assert "var x" not in data.description, "script contents are not part of an advert"


def test_the_structured_source_is_preferred_over_the_fallback():
    result = parse_page("https://example.org/j/1", page_with_jsonld(FULL_POSTING))

    assert result is not None
    assert result[1].name == "schema.org"


def test_the_fallback_catches_what_the_structured_source_cannot():
    result = parse_page("https://example.org/j/1", "<html><head><title>A job</title></head></html>")

    assert result is not None
    assert result[1].name == "page-metadata"


def test_a_page_with_nothing_at_all_yields_no_capture():
    assert parse_page("https://example.org/", "<html><body></body></html>") is None


def test_a_source_that_raises_is_skipped_rather_than_failing_the_capture(monkeypatch):
    class Exploding:
        name, version = "exploding", "1.0"

        def can_handle(self, url):
            return True

        def parse(self, url, html):
            raise RuntimeError("this plugin is broken")

    from postulo.plugins import registry

    monkeypatch.setattr(
        registry, "available_sources", lambda **_kw: [Exploding(), SchemaOrgSource()]
    )
    result = registry.parse_page("https://example.org/j/1", page_with_jsonld(FULL_POSTING))

    assert result is not None, "a broken plugin must not take capture down with it"
    assert result[1].name == "schema.org"


def test_an_enormous_description_is_truncated_rather_than_rejected():
    data = JobPostingData(title="A role", description="x" * 60_000)

    assert len(data.description) < 60_000
    assert data.description.endswith("[…truncated]")


def test_the_schema_forbids_fields_it_does_not_know():
    """A plugin cannot smuggle a value past the review screen by inventing a field."""
    with pytest.raises(Exception, match="extra"):
        JobPostingData(title="A role", salary_note="secretly enormous")


# ------------------------------------------------------------- fetching safely


@pytest.fixture
def resolves_to(monkeypatch):
    """Point every hostname at an address of our choosing."""

    def _install(address: str):
        monkeypatch.setattr(
            fetching.socket,
            "getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, "", (address, 0))],
        )

    return _install


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "192.168.1.1",  # the router everyone has
        "169.254.169.254",  # cloud metadata
        "172.16.0.1",  # private
        "100.64.0.1",  # carrier-grade NAT
        "::1",  # loopback, again
    ],
)
def test_addresses_off_the_public_internet_are_refused(resolves_to, address):
    """Postulo usually runs on a network with a router and a NAS on it."""
    resolves_to(address)

    with pytest.raises(fetching.UnsafeURL, match="private or local"):
        fetching.validate_public_url("https://looks-fine.example.org/jobs/1")


def test_a_public_address_is_accepted(resolves_to):
    resolves_to("93.184.216.34")

    assert fetching.validate_public_url("https://example.org/jobs/1")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://x/1"])
def test_only_http_and_https_are_fetched(url):
    with pytest.raises(fetching.UnsafeURL, match="http and https"):
        fetching.validate_public_url(url)


def test_a_hostname_resolving_to_both_public_and_private_is_refused(monkeypatch):
    """Answering with one of each would otherwise be a way in."""
    monkeypatch.setattr(
        fetching.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (None, None, None, "", ("93.184.216.34", 0)),
            (None, None, None, "", ("127.0.0.1", 0)),
        ],
    )

    with pytest.raises(fetching.UnsafeURL):
        fetching.validate_public_url("https://split-horizon.example.org/")


def test_a_hostname_that_does_not_resolve_is_refused(monkeypatch):
    def explode(*args, **kwargs):
        raise fetching.socket.gaierror("no such host")

    monkeypatch.setattr(fetching.socket, "getaddrinfo", explode)

    with pytest.raises(fetching.UnsafeURL, match="could not be resolved"):
        fetching.validate_public_url("https://nowhere.example.org/")


@pytest.mark.django_db
def test_robots_can_be_honoured_and_can_be_overridden(settings, monkeypatch):
    class Response:
        status_code = 200
        text = "User-agent: *\nDisallow: /jobs/"

    class Client:
        def get(self, *args, **kwargs):
            return Response()

    settings.POSTULO_CAPTURE_IGNORE_ROBOTS = False
    assert fetching.robots_allow("https://example.org/jobs/1", client=Client()) is False
    assert fetching.robots_allow("https://example.org/about", client=Client()) is True

    settings.POSTULO_CAPTURE_IGNORE_ROBOTS = True
    assert fetching.robots_allow("https://example.org/jobs/1", client=Client()) is True


@pytest.mark.django_db
def test_a_site_without_robots_txt_is_treated_as_allowing_everything():
    class Missing:
        status_code = 404
        text = ""

    class Client:
        def get(self, *args, **kwargs):
            return Missing()

    assert fetching.robots_allow("https://example.org/jobs/1", client=Client()) is True


@pytest.mark.django_db
def test_an_unreachable_robots_txt_does_not_block_the_capture():
    class Client:
        def get(self, *args, **kwargs):
            raise OSError("connection refused")

    assert fetching.robots_allow("https://example.org/jobs/1", client=Client()) is True


# --------------------------------------------------------------- text handling


def test_text_extraction_keeps_paragraphs_apart():
    text = html_to_text("<p>First thing.</p><p>Second thing.</p>")

    assert text == "First thing.\n\nSecond thing."


def test_text_extraction_drops_scripts_and_styles():
    text = html_to_text("<style>body{color:red}</style><script>alert(1)</script><p>Real.</p>")

    assert text == "Real."


def test_meta_extraction_reads_title_and_open_graph():
    meta = extract_meta(
        '<html><head><title>T</title><meta property="og:title" content="OG"></head>'
    )

    assert meta["title"] == "T"
    assert meta["og:title"] == "OG"


# ------------------------------------------------------------------- captures


@pytest.fixture
def capture(db, user):
    return Capture.objects.create(
        owner=user,
        url="https://example.org/j/1",
        source_name="schema.org",
        source_version="1.0",
        data=JobPostingData(
            title="Senior Backend Engineer", company_name="Aperture Science"
        ).model_dump(mode="json"),
    )


def test_a_capture_starts_out_waiting_for_review(capture):
    """Nothing parsed becomes a record until somebody has looked at it."""
    assert capture.status == CaptureStatus.PENDING
    assert capture.application is None


def test_a_capture_validates_its_stored_data_on_the_way_out(capture):
    data = capture.posting_data

    assert data.title == "Senior Backend Engineer"
    assert data.company_name == "Aperture Science"


# ------------------------------------------------- when a site refuses Postulo


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, "bot protection"),
        (401, "bot protection"),
        (404, "nothing at that address"),
        (429, "slow down"),
        (503, "having trouble"),
    ],
)
def test_a_refusal_says_what_to_do_about_it(status, expected):
    """A bare status code is true and useless.

    403 in particular is the common case: large employers sit behind bot protection that
    turns away anything not driving a browser, so the page somebody is looking at right
    now is genuinely unreachable from their server.
    """
    assert expected in fetching._describe_failure(status)


@pytest.mark.parametrize(
    "address,expected",
    [
        # What MathWorks actually publishes: the country in the locality, and again on
        # its own. Found by capturing a real posting, not by imagining one.
        (
            {"addressLocality": "Issy-les-Moulineaux, FR", "addressCountry": "FR"},
            "Issy-les-Moulineaux, FR",
        ),
        # The tidy case.
        (
            {"addressLocality": "Paris", "addressCountry": "FR"},
            "Paris, FR",
        ),
        # A country given as an object rather than a string, which is equally valid.
        (
            {"addressLocality": "Berlin", "addressCountry": {"name": "DE"}},
            "Berlin, DE",
        ),
        # Region and country that happen to match.
        (
            {"addressLocality": "Lisbon", "addressRegion": "PT", "addressCountry": "PT"},
            "Lisbon, PT",
        ),
        # A place that legitimately repeats a word must not lose it.
        (
            {"addressLocality": "New York", "addressRegion": "New York", "addressCountry": "US"},
            "New York, US",
        ),
    ],
)
def test_a_location_is_not_said_twice(address, expected):
    posting = {**FULL_POSTING, "jobLocation": {"@type": "Place", "address": address}}

    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(posting))

    assert data.location == expected
