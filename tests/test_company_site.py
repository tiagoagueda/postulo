"""The company's own website as a source of its details and its logo (#1256).

*Find logo* moved onto this plugin: the site is read for its icon as it always was -- the
tests that held that are here now, unchanged in what they hold -- and the same page is read
for what a schema.org Organization on it says, for *Find details*.
"""

from __future__ import annotations

import pytest

from postulo.core.memo import forget_decisions
from postulo.jobs import enrichment, logos
from postulo.jobs.models import Company
from postulo.plugins import api, fetching
from postulo.plugins.company_site import COMPANY_SITE, CompanySite, reading
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_a_company_enrichment_source
from tests.test_logos import a_page, an_image
from tests.test_logos import web as web  # the fixture: a web that answers as a test says

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Black Mesa", website="https://blackmesa.test")


def serving_page(monkeypatch, html, url="https://blackmesa.test/"):
    monkeypatch.setattr(
        fetching, "fetch_page", lambda asked: fetching.FetchedPage(url=url, html=html)
    )


# ------------------------------------------------------- finding one on a site
#
# Moved from tests/test_logos.py with the code they test: what they hold is what
# *Find logo* does, and it has not changed.


def test_the_site_is_asked_what_its_own_icon_is(web, company, monkeypatch):
    serving_page(
        monkeypatch,
        a_page(
            small='<link rel="icon" sizes="16x16" href="/small.png">',
            apple='<link rel="apple-touch-icon" sizes="180x180" href="/touch.png">',
        ).decode(),
    )
    web["responses"]["https://blackmesa.test/touch.png"] = (200, an_image(), "image/png")
    web["responses"]["https://blackmesa.test/small.png"] = (200, an_image(), "image/png")

    found = logos.find_on_website(company)
    assert found == "https://blackmesa.test/touch.png", "the largest declared icon first"
    company.refresh_from_db()
    assert company.has_logo and company.logo_source == "website"


def test_the_organisations_own_logo_and_the_favicon_are_tried_too(web, company, monkeypatch):
    serving_page(
        monkeypatch,
        a_page(
            ld=(
                '<script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Organization",'
                '"logo":{"url":"https://blackmesa.test/brand.png"}}'
                "</script>"
            )
        ).decode(),
    )
    web["responses"]["https://blackmesa.test/brand.png"] = (200, an_image(), "image/png")
    assert logos.find_on_website(company) == "https://blackmesa.test/brand.png"

    # With nothing declared, the conventional address is the last thing tried.
    serving_page(monkeypatch, a_page().decode())
    web["responses"]["https://blackmesa.test/favicon.ico"] = (200, an_image(), "image/png")
    assert logos.find_on_website(company).endswith("/favicon.ico")


def test_a_site_with_nothing_usable_says_so(web, company, monkeypatch):
    serving_page(monkeypatch, a_page().decode())
    with pytest.raises(logos.UnusableLogo, match=r"Nothing on blackmesa\.test"):
        logos.find_on_website(company)


def test_a_deeply_nested_json_ld_block_is_skipped_not_a_crash(web, company, monkeypatch):
    """A page's JSON-LD nested thousands deep must end in the sentence, not a traceback (#959)."""
    deep = "[" * 200_000 + "]" * 200_000
    serving_page(
        monkeypatch, a_page(ld=f'<script type="application/ld+json">{deep}</script>').decode()
    )
    with pytest.raises(logos.UnusableLogo, match=r"Nothing on blackmesa\.test"):
        logos.find_on_website(company)
    # A document nested as deep as the parser allows is walked without recursing.
    shallow = "[" * 900 + '{"@type":"Organization","logo":"https://x.test/l.png"}' + "]" * 900
    assert reading._organisation_logos([shallow]) == ["https://x.test/l.png"]


def test_a_site_that_blocks_the_icon_says_why(web, company, monkeypatch):
    serving_page(monkeypatch, a_page(icon='<link rel="icon" href="/only.png">').decode())
    web["responses"]["https://blackmesa.test/only.png"] = (403, b"", "text/plain")
    with pytest.raises(logos.UnusableLogo, match=r"Nothing on blackmesa\.test.*403"):
        logos.find_on_website(company)


def test_the_favicon_is_tried_however_many_icons_a_page_declares(web, company, monkeypatch):
    icons = "".join(
        f'<link rel="apple-touch-icon" sizes="{n}x{n}" href="/touch{n}.png">'
        for n in (57, 60, 72, 76, 114, 120, 144)
    )
    serving_page(monkeypatch, a_page(icons=icons).decode())
    with pytest.raises(logos.UnusableLogo):
        logos.find_on_website(company)
    assert "https://blackmesa.test/favicon.ico" in web["calls"]


def test_a_company_with_no_website_has_nowhere_to_look(user):
    company = Company.objects.create(owner=user, name="Aperture")
    with pytest.raises(logos.UnusableLogo, match="no website"):
        logos.find_on_website(company)


def test_an_icons_sizes_are_read_in_time_that_follows_their_length():
    """`sizes` is the page's to write. Read without a look-behind, a run of digits with no
    `x` after it was tried again from every digit in it: 40,000 of them took fifteen seconds
    and a page may be two megabytes, so *Find logo* on a hostile site held a worker for
    hours (#321)."""
    import time

    assert reading._largest("16x16 32X32 180x180") == 180
    assert reading._largest("any") == 0
    assert reading._largest("") == 0
    assert reading._largest(None) == 0

    started = time.perf_counter()
    assert reading._largest("1" * 400_000) == 0
    assert reading._largest("1" * 400_000 + "x16") == 0, "no size is 400,000 digits long"
    assert reading._largest("7" * 200_000 + " 48x48") == 48
    assert time.perf_counter() - started < 2.0


# ----------------------------------------------------------- its switch (D4)


def test_it_is_shipped_and_on_by_default_and_an_administrator_switches_it_off(
    web, company, monkeypatch
):
    from postulo.plugins.policy import decide

    assert decide(COMPANY_SITE, company.owner).on
    assert [source.key for source in enrichment.usable_for(company, company.owner)] == [
        "company-site"
    ]

    PluginPolicy.objects.create(plugin=COMPANY_SITE, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()
    serving_page(monkeypatch, a_page().decode())

    with pytest.raises(logos.UnusableLogo, match="switched on"):
        logos.find_on_website(company, by=company.owner)
    assert web["calls"] == [], "nothing read the site"


# ------------------------------------------------------- what the site declares


ORGANISATION = """
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Corporation",
 "name": "Black Mesa", "legalName": "Black Mesa Research Facility", "alternateName": "BMRF",
 "email": "jobs@blackmesa.test", "telephone": "+1 505 555 0100",
 "sameAs": ["https://www.linkedin.com/company/black-mesa", "https://www.wikidata.org/wiki/Q95"],
 "address": {"@type": "PostalAddress", "streetAddress": "1 Mesa Way",
             "addressLocality": "Mesa", "postalCode": "87001", "addressCountry": "US"},
 "leiCode": "529900T8BM49AURSDO55", "foundingDate": "1958-03-01",
 "numberOfEmployees": {"@type": "QuantitativeValue", "minValue": 1000, "maxValue": 4999}}
</script>
"""


def test_the_organisation_a_page_declares_is_read_with_where_each_value_was():
    found = reading.read(
        [api.EnrichmentAnswer(url="https://blackmesa.test/", status=200, text=ORGANISATION)],
        api.EnrichmentSubject(website="https://blackmesa.test/"),
    )
    said = {
        (
            one.field,
            one.value if not isinstance(one.value, api.RegisterAddress) else one.value.street,
        )
        for one in found
    }

    assert {
        ("name", "Black Mesa Research Facility"),
        ("acronym", "BMRF"),
        ("email", "jobs@blackmesa.test"),
        ("phone", "+1 505 555 0100"),
        ("social", "https://www.linkedin.com/company/black-mesa"),
        ("identifier", ("wikidata", "Q95")),
        ("address", "1 Mesa Way"),
        ("identifier", ("lei", "529900T8BM49AURSDO55")),
        ("founded", "1958-03-01"),
        ("size", (1000, 4999)),
        ("logo", "https://blackmesa.test/favicon.ico"),
    } <= said
    lei = next(one for one in found if one.value == ("lei", "529900T8BM49AURSDO55"))
    assert lei.origin == 'schema.org Corporation, "leiCode" (JSON-LD)'
    assert lei.url == "https://blackmesa.test/"


def test_microdata_and_rdfa_are_read_as_json_ld_is():
    page = (
        '<div itemscope itemtype="https://schema.org/Organization">'
        '<span itemprop="email">hr@blackmesa.test</span></div>'
        '<div vocab="https://schema.org/" typeof="LocalBusiness">'
        '<span property="foundingDate">1958</span></div>'
    )

    found = reading.organisation(page, "https://blackmesa.test/")

    assert [(one.field, one.value, one.origin) for one in found] == [
        ("email", "hr@blackmesa.test", 'schema.org Organization, "email" (microdata)'),
        ("founded", "1958", 'schema.org LocalBusiness, "foundingDate" (RDFa)'),
    ]


def test_a_page_nested_too_deep_for_the_reader_is_read_for_what_else_it_has():
    deep = '<script type="application/ld+json">' + "[" * 200_000 + "]" * 200_000 + "</script>"

    found = reading.read(
        [api.EnrichmentAnswer(url="https://blackmesa.test/", status=200, text=deep)],
        api.EnrichmentSubject(website="https://blackmesa.test/"),
    )

    assert [one.value for one in found] == ["https://blackmesa.test/favicon.ico"]


def test_it_passes_the_published_check():
    found = assert_is_a_company_enrichment_source(
        CompanySite(),
        subject=api.EnrichmentSubject(website="https://blackmesa.test/"),
        answers=[
            api.EnrichmentAnswer(url="https://blackmesa.test/", status=200, text=ORGANISATION)
        ],
    )

    assert ("size", (1000, 4999)) in [(one.field, one.value) for one in found]
