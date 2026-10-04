"""Capturing postings: parsing, the safety rules around fetching, and the plugin registry.

Nothing here reaches the network. The parsers are given HTML directly, and the fetching
rules are exercised against a stubbed resolver, because a test that depends on somebody
else's website is a test that fails for reasons which have nothing to do with Postulo.
"""

import json

import httpx
import pytest

from postulo.jobs.models import Capture, CaptureStatus
from postulo.plugins import fetching, public_addresses
from postulo.plugins.base import JobPostingData
from postulo.plugins.builtin import PageMetadataSource, SchemaOrgSource
from postulo.plugins.builtin.htmlutil import extract_jsonld, extract_meta, html_to_text
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
    """The site's own name is the employer here, and is taken out of the title (#267)."""
    page = (
        "<html><head><title>Junior Dev - Black Mesa</title>"
        '<meta property="og:site_name" content="Black Mesa"></head>'
        "<body><script>var x = 1</script><p>We need someone.</p></body></html>"
    )

    data = PageMetadataSource().parse("https://example.org/j/1", page)

    assert data.title == "Junior Dev"
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


def test_the_address_is_held_to_its_scheme_and_to_nothing_else():
    """The scheme is the danger; the shape of a hostname is not (#218).

    A posting's address is drawn as a link, so `javascript:` in one runs as whoever opened
    the page. What follows the scheme is left alone on purpose: an internal board on a host
    with no dot in its name is a real place to capture from, and refusing the whole capture
    over that would lose more than it saves -- the review screen is still between it and
    anything saved.
    """
    with pytest.raises(ValueError, match="http"):
        JobPostingData(title="A role", url="javascript:alert(1)")
    with pytest.raises(ValueError, match="http"):
        JobPostingData(title="A role", url="data:text/html,<script>alert(1)</script>")

    assert JobPostingData(title="A role", url="").url == "", "a source that found none"
    assert JobPostingData(title="A role", url="https://jobs/42").url == "https://jobs/42"


# ------------------------------------------------------------- fetching safely


@pytest.fixture
def resolves_to(monkeypatch):
    """Point every hostname at an address of our choosing."""

    def _install(address: str):
        monkeypatch.setattr(
            public_addresses.socket,
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


def test_the_addresses_that_passed_come_back_for_the_caller_to_use(resolves_to):
    """Checking a name and connecting to it must be one act, not two lookups.

    A record that lives one second can answer with a public address while it is being
    checked and a private one a moment later, when the connection is made — so the check
    would have passed on an address nobody ever contacted. ``public_addresses_for`` hands
    back what it approved so the connection can go there instead of asking again.
    """
    resolves_to("93.184.216.34")

    addresses = fetching.public_addresses_for("https://example.org/jobs/1")

    assert [str(a) for a in addresses] == ["93.184.216.34"]


def test_a_fetch_connects_to_the_approved_address_and_still_names_the_site(
    resolves_to, monkeypatch, db
):
    """The page is fetched from the address that passed, asking for the site by name."""
    import httpx

    from postulo.plugins import http as plugin_http

    resolves_to("93.184.216.34")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen["sent_to"] = request.url.host
        seen["asked_for"] = request.headers.get("Host", "").split(":")[0]
        seen["tls_name"] = request.extensions.get("sni_hostname")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><head><title>A job</title></head><body>Work here</body></html>",
        )

    real = httpx.Client
    monkeypatch.setattr(
        plugin_http.httpx,
        "Client",
        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )

    fetching.fetch_page("https://example.org/jobs/1")

    assert seen["sent_to"] == "93.184.216.34", "connected to the address that was approved"
    assert seen["asked_for"] == "example.org", "and asked the site for its own page"
    assert seen["tls_name"] == "example.org", "so the certificate is checked against the name"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://x/1"])
def test_only_http_and_https_are_fetched(url):
    with pytest.raises(fetching.UnsafeURL, match="http and https"):
        fetching.validate_public_url(url)


def test_a_hostname_resolving_to_both_public_and_private_is_refused(monkeypatch):
    """Answering with one of each would otherwise be a way in."""
    monkeypatch.setattr(
        public_addresses.socket,
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
        raise public_addresses.socket.gaierror("no such host")

    monkeypatch.setattr(public_addresses.socket, "getaddrinfo", explode)

    with pytest.raises(fetching.UnsafeURL, match="could not be resolved"):
        fetching.validate_public_url("https://nowhere.example.org/")


def test_a_name_that_stops_resolving_after_the_check_is_still_a_capture_refusal(monkeypatch, db):
    """`fetch_page` looks the name up to validate the address, and the client's hook looks it
    up again to pin the connection. When the second answer is a failure the hook refuses with
    `DestinationRefused`, which is not a `CaptureError`: the API answered 500 and the errand
    logged a traceback, for a refusal that already had its sentence (#607)."""
    answers = iter([[(None, None, None, "", ("93.184.216.34", 0))]])

    def getaddrinfo(*args, **kwargs):
        for answer in answers:
            return answer
        raise public_addresses.socket.gaierror("no such host")

    monkeypatch.setattr(public_addresses.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(fetching, "robots_allow", lambda url, client=None: True)

    with pytest.raises(fetching.UnsafeURL, match="could not be resolved"):
        fetching.fetch_page("https://example.org/job")


def robots_client(handler):
    """A client answering from ``handler``: robots.txt is streamed and capped like a page (#321)."""
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.django_db
def test_robots_can_be_honoured_and_can_be_overridden(settings, monkeypatch):
    def answer(request):
        return httpx.Response(200, text="User-agent: *\nDisallow: /jobs/")

    settings.POSTULO_CAPTURE_IGNORE_ROBOTS = False
    with robots_client(answer) as client:
        assert fetching.robots_allow("https://example.org/jobs/1", client=client) is False
        assert fetching.robots_allow("https://example.org/about", client=client) is True

    settings.POSTULO_CAPTURE_IGNORE_ROBOTS = True
    with robots_client(answer) as client:
        assert fetching.robots_allow("https://example.org/jobs/1", client=client) is True


@pytest.mark.django_db
def test_a_site_without_robots_txt_is_treated_as_allowing_everything():
    with robots_client(lambda request: httpx.Response(404)) as client:
        assert fetching.robots_allow("https://example.org/jobs/1", client=client) is True


@pytest.mark.django_db
def test_an_unreachable_robots_txt_does_not_block_the_capture():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    with robots_client(refuse) as client:
        assert fetching.robots_allow("https://example.org/jobs/1", client=client) is True


@pytest.mark.django_db
def test_a_robots_txt_that_redirects_to_a_file_that_disallows_is_obeyed(settings):
    """http to https and the bare domain to ``www.`` are the common redirects, and the
    redirect was once taken for "no robots.txt" (#364)."""
    settings.POSTULO_CAPTURE_IGNORE_ROBOTS = False

    def answer(request):
        if request.url.host == "example.org":
            return httpx.Response(301, headers={"Location": "https://www.example.org/robots.txt"})
        return httpx.Response(200, text="User-agent: *\nDisallow: /careers")

    with robots_client(answer) as client:
        assert fetching.robots_allow("https://example.org/careers/1", client=client) is False
        assert fetching.robots_allow("https://example.org/about", client=client) is True


@pytest.mark.django_db
def test_a_posting_that_redirects_to_a_site_whose_robots_txt_disallows_it(
    resolves_to, settings, monkeypatch
):
    """The first host's robots.txt is not the second's: the one the page ends at is asked."""
    from postulo.plugins import http as plugin_http

    settings.POSTULO_CAPTURE_IGNORE_ROBOTS = False
    resolves_to("93.184.216.34")
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["Host"].split(":")[0]
        if request.url.path == "/robots.txt":
            rules = "User-agent: *\nDisallow: /careers" if host == "jobs.example.com" else ""
            return httpx.Response(200, text=rules)
        pages.append((host, request.url.path))
        if host == "short.example":
            return httpx.Response(302, headers={"Location": "https://jobs.example.com/careers/123"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>Hi</p>")

    real = httpx.Client
    monkeypatch.setattr(
        plugin_http.httpx,
        "Client",
        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )

    with pytest.raises(fetching.RobotsDisallowed):
        fetching.fetch_page("https://short.example/123")

    assert pages == [("short.example", "/123")], "the second site's page was never requested"


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


# ------------------------------------------- a careless field costs the field (#592)


def _stated(**changes) -> dict:
    return {**FULL_POSTING, **changes}


def _salary_of(value, currency="EUR") -> dict:
    return {"baseSalary": {"currency": currency, "value": {"minValue": value, "unitText": "YEAR"}}}


@pytest.mark.parametrize("amount", ["NaN", "Infinity", 1e400, 1e30, "-5"])
def test_an_unusable_salary_amount_costs_the_salary_only(amount):
    posting = _stated(**_salary_of(amount))
    page = page_with_jsonld(posting).replace("1e+400", "1e400").replace("Infinity", "1e400")
    data = SchemaOrgSource().parse("https://example.org/j/1", page)
    assert data.title == "Senior Backend Engineer"
    assert data.location == "Paris, FR"
    assert data.salary_min is None and data.salary_max is None
    assert data.salary_currency == ""


@pytest.mark.parametrize("written", ["US Dollar", "$"])
def test_a_currency_that_is_no_code_is_left_empty(written):
    posting = _stated(**_salary_of(50000, written))
    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(posting))
    assert data.salary_min == 50000
    assert data.salary_currency == ""


def test_a_lowercase_code_is_still_read():
    posting = _stated(**_salary_of(50000, "usd"))
    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(posting))
    assert data.salary_currency == "USD"


def test_a_long_title_company_and_place_are_cut_to_what_a_posting_holds():
    long = "x" * 600
    posting = _stated(
        title=long,
        hiringOrganization={"@type": "Organization", "name": long},
        jobLocation={"@type": "Place", "address": long},
    )
    data = SchemaOrgSource().parse("https://example.org/j/1", page_with_jsonld(posting))
    assert len(data.title) == len(data.company_name) == len(data.location) == 500
    assert data.posted_at is not None


def test_a_board_recipe_with_a_long_field_still_answers():
    page = (
        "<html><body>"
        '<h1 class="topcard__title">Engineer</h1>'
        '<a class="topcard__org-name-link">Acme</a>'
        f'<span class="topcard__flavor topcard__flavor--bullet">{"y" * 700}</span>'
        "</body></html>"
    )
    from postulo.plugins.builtin import BoardSource

    data = BoardSource().parse("https://www.linkedin.com/jobs/view/1", page)
    assert data is not None
    assert data.title == "Engineer" and len(data.location) == 500


@pytest.mark.parametrize("ignored", [False, True])
def test_the_capture_form_tells_the_truth_about_robots_txt(client, user, ignored):
    """The help beside the form states what this instance does, not what it usually does (#630)."""
    from django.urls import reverse

    from postulo.core.models import SiteSettings

    row = SiteSettings.get()
    row.capture_ignore_robots = ignored
    row.save()
    client.force_login(user)
    html = client.get(reverse("jobs:capture_create")).content.decode()
    honoured = (
        "The site&#x27;s robots.txt is honoured." in html
        or "The site's robots.txt is honoured." in html
    )
    assert honoured is not ignored
    assert ("set not to consult" in html) is ignored
