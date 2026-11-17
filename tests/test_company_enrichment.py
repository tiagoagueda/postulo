"""Company details from open sources: the kind, the press, the review and what is kept (#1256).

Two sources written for these tests, in a plugin registered as Postulo's own are: one that
answers JSON on a host of its own, and one that reads the company's own website. The request
is the real guarded client over a transport that never leaves the machine, as the register
lookup's tests do it; the page reader is stubbed where a page is read, as the logo tests do.
"""

from __future__ import annotations

import ipaddress
import json
import re
from contextlib import contextmanager

import httpx
import pytest
from django.core.cache import caches
from django.urls import reverse

from postulo.core import gdpr
from postulo.core.memo import forget_decisions
from postulo.core.models import Errand, ErrandState
from postulo.jobs import enrichment, enrichment_offers
from postulo.jobs.models import Company, CompanyIdentifier, RecordChange, Share
from postulo.plugins import api, fetching, http, registry
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_a_company_enrichment_source

PUBLIC = [ipaddress.ip_address("93.184.216.34")]
pytestmark = pytest.mark.django_db

QID = "Q95"
ITEM = "https://data.details.test/item/Q95"


# ------------------------------------------------------------------ two sources


def _values(document) -> list:
    """The offered values a test's document lists, as a source would build them."""
    found = []
    for row in (document or {}).get("values", []):
        row = dict(row)
        if row["field"] == "address" and isinstance(row["value"], dict):
            row["value"] = api.RegisterAddress(**row["value"])
        elif isinstance(row["value"], list):
            row["value"] = tuple(row["value"])
        found.append(api.OfferedValue(**row))
    return found


def read_data(answers, subject):
    return _values(answers[0].json)


def read_site(answers, subject):
    return _values(json.loads(answers[0].text or "{}"))


DATA = api.EnrichmentSource(
    key="test-data",
    label="Test Data",
    starts_from=("wikidata",),
    offers=enrichment.FIELDS,
    hosts=("data.details.test",),
    addresses=lambda subject: [f"https://data.details.test/item/{subject.identifiers['wikidata']}"],
    read=read_data,
    sends="the company's Wikidata identifier",
    source="Test Data Office",
    licence="CC0",
    licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
)

SITE = api.EnrichmentSource(
    key="test-site",
    label="Its own website",
    starts_from=("website",),
    offers=("website", "email", "logo", "social", "founded", "name"),
    own_site=True,
    reads="page",
    addresses=lambda subject: [subject.website],
    read=read_site,
    sends="nothing but the request itself",
    source="The company's own website",
)


class Details:
    """A company-enrichment plugin, as a package installed beside Postulo would write one."""

    name = "test-details"
    kind = "company-enrichment"
    label = "Test details"
    sources = (DATA, SITE)


@contextmanager
def installed(plugin_class=Details):
    """``plugin_class`` registered as one of Postulo's own, and taken out again."""
    registry.register_builtin("company-enrichment", plugin_class)
    forget_decisions()
    try:
        yield plugin_class
    finally:
        registry.unregister_builtin("company-enrichment", plugin_class)
        forget_decisions()


@pytest.fixture
def details(db):
    caches["ratelimit"].clear()
    with installed():
        yield Details
    caches["ratelimit"].clear()


@pytest.fixture
def web(monkeypatch):
    """The real guard, every name resolving to a public address, over a mock transport.

    Answers are keyed on the address asked for; anything else is a 404. A page is read
    through `fetch_page`, stubbed to hand back what ``pages`` holds for an address.
    """
    seen: list[str] = []
    answers: dict[str, httpx.Response] = {}
    pages: dict[str, str] = {}
    read: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # The client connects to the address it checked, and says the name in `Host`.
        asked = f"{request.url.scheme}://{request.headers['host']}{request.url.raw_path.decode()}"
        seen.append(asked)
        return answers.get(asked, httpx.Response(404))

    def fetch_page(url):
        read.append(url)
        if url not in pages:
            raise fetching.FetchFailed("There is nothing at that address (404). Check the link.")
        return fetching.FetchedPage(url=url, html=pages[url])

    monkeypatch.setattr(http, "public_addresses_for", lambda url: PUBLIC)
    real = http.public_only_client
    monkeypatch.setattr(
        http,
        "public_only_client",
        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(fetching, "fetch_page", fetch_page)
    return {"answers": answers, "seen": seen, "pages": pages, "read": read}


def says(web, *values, url=ITEM, status=200):
    web["answers"][url] = httpx.Response(status, content=json.dumps({"values": values}).encode())


def site_says(web, *values, url="https://blackmesa.test"):
    web["pages"][url] = json.dumps({"values": values})


def value(name, what, origin="a property", **more) -> dict:
    return {"field": name, "value": what, "origin": origin, **more}


def acme(user, *, qid=QID, **fields) -> Company:
    company = Company.objects.create(owner=user, name=fields.pop("name", "Black Mesa"), **fields)
    if qid:
        CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value=qid)
    return company


def press(client, company, *keys, **more):
    """Press *Find details* with these sources ticked; the response is not followed."""
    return client.post(
        reverse("jobs:company_enrichment", args=[company.pk]), {"source": list(keys), **more}
    )


def review_of(client, company, *keys):
    """Press, and open the review the press leads to: the response and its errand."""
    response = press(client, company, *keys)
    assert response.status_code == 302, response.status_code
    assert "errand=" in response.url, response.url
    errand = Errand.objects.get(pk=int(response.url.split("errand=")[1]))
    return client.get(response.url), errand


def keep(client, company, errand, *ticks, **choices):
    return client.post(
        reverse("jobs:company_enrichment", args=[company.pk]),
        {"errand": errand.pk, "keep": "1", "take": list(ticks), **choices},
    )


def tick_for(response, name) -> str:
    """The identifier of the one choice the review offers for a field."""
    (choice,) = [
        choice
        for group in response.context["review"].groups
        if group.field == name
        for choice in group.choices
    ]
    return choice.id


# ------------------------------------------------------------------- the surface


def test_the_surface_holds_the_contract_and_they_are_the_registrys_own_classes():
    from postulo.plugins import base
    from postulo.plugins.builtin import htmlutil

    assert api.EnrichmentSource is enrichment.Source
    assert api.EnrichmentSubject is enrichment.Subject
    assert api.EnrichmentAnswer is enrichment.Answer
    assert api.OfferedValue is enrichment.Offered
    assert api.CompanyEnrichmentPlugin is base.CompanyEnrichmentPlugin
    assert api.extract_jsonld is htmlutil.extract_jsonld
    assert api.extract_microdata is htmlutil.extract_microdata
    assert api.extract_rdfa is htmlutil.extract_rdfa
    for name in (
        "EnrichmentSource",
        "EnrichmentSubject",
        "EnrichmentAnswer",
        "OfferedValue",
        "CompanyEnrichmentPlugin",
        "extract_jsonld",
        "extract_microdata",
        "extract_rdfa",
    ):
        assert name in api.__all__, name


def test_the_contract_module_imports_no_model_and_translates_nothing():
    """A package that imports the contract at module level loads wherever the surface does:
    the module's own imports are the standard library, httpx, Django's translation functions
    and two modules that hold to the same rule."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(enrichment.__file__).read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    modules = {
        f"{'.' * node.level}{node.module or ''}" if isinstance(node, ast.ImportFrom) else alias.name
        for node in top
        for alias in node.names
    }
    assert modules <= {
        "__future__",
        "json",
        "logging",
        "re",
        "collections.abc",
        "dataclasses",
        "datetime",
        "typing",
        "urllib.parse",
        "httpx",
        "django.utils.translation",
        "postulo.plugins",
        ".",
        ".register_lookup",
    }, modules


def test_the_group_is_open_and_each_plugin_of_the_kind_is_its_own_switch():
    from postulo.plugins import kinds, policy

    assert registry.GROUPS["company-enrichment"] == "postulo.company_enrichment"
    assert "company-enrichment" in policy.GOVERNED_KINDS
    assert "company-enrichment" not in policy.UNGOVERNED_KINDS
    assert str(kinds.label_for("company-enrichment")) == "Company details"
    assert kinds.tone_for("company-enrichment") == "violet"


def test_the_plugin_answers_to_the_protocol(details):
    assert isinstance(Details(), api.CompanyEnrichmentPlugin)
    assert set(enrichment.sources()) == {"test-data", "test-site"}


# ------------------------------------------------------------------ the registry


def test_a_source_that_cannot_be_used_is_left_out_and_a_key_is_claimed_once(details):
    def source(**changes):
        return api.EnrichmentSource(
            **{
                "key": "elsewhere",
                "label": "Elsewhere",
                "starts_from": ("name",),
                "offers": ("website",),
                "hosts": ("elsewhere.test",),
                "addresses": lambda subject: [],
                "read": lambda answers, subject: [],
                "sends": "the name",
                "source": "Elsewhere",
                **changes,
            }
        )

    class Elsewhere:
        name = "elsewhere"
        kind = "company-enrichment"
        sources = (
            source(),
            source(key="test-data", label="Not the first to claim it"),
            source(key="Not A Key"),
            source(key="no-host", hosts=()),
            source(key="both", own_site=True, starts_from=("website",)),
            source(key="site-without-a-website", hosts=(), own_site=True),
            source(key="a-field-nobody-knows", offers=("shoe_size",)),
            source(key="starts-from-nothing", starts_from=()),
            source(key="a-url-for-a-host", hosts=("https://elsewhere.test/",)),
            source(key="reads-pdf", reads="pdf"),
            "not a source",
        )

    with installed(Elsewhere):
        found = enrichment.registered()

    assert [key for _plugin, key in ((plugin, s.key) for plugin, s in found)] == [
        "test-data",
        "test-site",
        "elsewhere",
    ]
    assert {s.key: s.label for _plugin, s in found}["test-data"] == "Test Data"


def test_a_source_is_told_only_what_it_starts_from(details, user):
    company = acme(user, website="https://blackmesa.test")

    for_data = enrichment.subject_for(company, DATA)
    for_site = enrichment.subject_for(company, SITE)

    assert for_data.identifiers == {"wikidata": QID}
    assert (for_data.name, for_data.website, for_data.country) == ("", "", "")
    assert for_site.website == "https://blackmesa.test"
    assert (for_site.name, for_site.identifiers) == ("", {})


def test_a_company_lacking_what_a_source_starts_from_is_not_asked(details, user, web):
    company = acme(user, qid="")

    assert enrichment.missing(DATA, company) == ["wikidata"]
    assert enrichment.usable_for(company) == []
    assert enrichment.ask(company, ["test-data", "test-site"]) == []
    assert web["seen"] == [] and web["read"] == []


# ------------------------------------------------------------------ the requests


def test_only_the_address_is_asked_with_postulos_user_agent(details, user, web, monkeypatch):
    sent: list[httpx.Request] = []
    real = http.public_only_client

    def recording(**kwargs):
        client = real(**kwargs)
        client.event_hooks["request"].append(sent.append)
        return client

    monkeypatch.setattr(http, "public_only_client", recording)
    says(web, value("website", "https://blackmesa.test/"))

    (result,) = enrichment.ask(acme(user), ["test-data"])

    assert result.error == "" and [one.value for one in result.values] == [
        "https://blackmesa.test/"
    ]
    assert web["seen"] == [ITEM]
    (request,) = sent
    assert request.headers["User-Agent"].startswith("Postulo/")
    assert "cookie" not in request.headers and "referer" not in request.headers
    assert request.headers["Accept"] == "application/json"


def rogue(**changes):
    return api.EnrichmentSource(
        **{
            "key": "rogue",
            "label": "Rogue",
            "starts_from": ("name",),
            "offers": ("website",),
            "hosts": ("rogue.test",),
            "addresses": lambda subject: ["https://rogue.test/a"],
            "read": lambda answers, subject: [],
            "sends": "the name",
            "source": "Rogue",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "address",
    [
        "https://elsewhere.test/a",
        "http://rogue.test/a",
        "https://rogue.test.elsewhere.test/a",
        "https://rogue.test@elsewhere.test/a",
        "file:///etc/passwd",
        "http://[::1",
    ],
)
def test_an_address_off_its_hosts_refuses_the_source_and_nothing_is_sent(web, address):
    source = rogue(addresses=lambda subject: ["https://rogue.test/a", address])

    with pytest.raises(enrichment.Refused, match="did not declare"):
        enrichment.ask_source(source, enrichment.Subject(name="Black Mesa"))
    assert web["seen"] == []


def test_a_redirect_is_followed_among_its_hosts_and_refused_outside_them(web):
    web["answers"]["https://rogue.test/a"] = httpx.Response(
        302, headers={"Location": "https://rogue.test/b"}
    )
    web["answers"]["https://rogue.test/b"] = httpx.Response(200, content=b"{}")
    seen_by_read = []
    source = rogue(read=lambda answers, subject: seen_by_read.extend(answers) or [])

    enrichment.ask_source(source, enrichment.Subject(name="Black Mesa"))

    assert [answer.url for answer in seen_by_read] == ["https://rogue.test/b"]

    web["answers"]["https://rogue.test/a"] = httpx.Response(
        302, headers={"Location": "https://elsewhere.test/b"}
    )
    web["seen"].clear()
    with pytest.raises(enrichment.Refused, match="somewhere else"):
        enrichment.ask_source(source, enrichment.Subject(name="Black Mesa"))
    assert web["seen"] == ["https://rogue.test/a"], "the other host was never asked"


def test_a_source_that_reads_the_companys_site_is_held_to_the_websites_host(web):
    def site(addresses):
        return api.EnrichmentSource(
            key="site",
            label="Site",
            starts_from=("website",),
            offers=("email",),
            own_site=True,
            reads="page",
            addresses=addresses,
            read=lambda answers, subject: [],
            sends="nothing",
            source="Site",
        )

    subject = enrichment.Subject(website="https://Blackmesa.TEST/about")
    web["pages"]["https://blackmesa.test/contact"] = "{}"

    assert enrichment.ask_source(site(lambda s: ["https://blackmesa.test/contact"]), subject) == []
    for elsewhere in ("https://elsewhere.test/", "https://blackmesa.test.elsewhere.test/"):
        web["read"].clear()
        with pytest.raises(enrichment.Refused, match="did not declare"):
            enrichment.ask_source(site(lambda s, to=elsewhere: [to]), subject)
        assert web["read"] == [], "nothing was read"


def test_more_than_four_requests_in_one_press_are_refused(web):
    many = rogue(addresses=lambda subject: [f"https://rogue.test/{n}" for n in range(5)])
    with pytest.raises(enrichment.Refused, match="more requests"):
        enrichment.ask_source(many, enrichment.Subject(name="Black Mesa"))

    for n in range(3):
        web["answers"][f"https://rogue.test/{n}"] = httpx.Response(200, content=b"{}")
    following = rogue(
        addresses=lambda subject: [f"https://rogue.test/{n}" for n in range(3)],
        follow=lambda answers, subject: ["https://rogue.test/3", "https://rogue.test/4"],
    )
    web["seen"].clear()
    with pytest.raises(enrichment.Refused, match="more requests"):
        enrichment.ask_source(following, enrichment.Subject(name="Black Mesa"))
    assert len(web["seen"]) == 3, "the round that went past the allowance was not begun"


def test_one_more_round_may_follow_the_first_answers(web):
    web["answers"]["https://rogue.test/a"] = httpx.Response(200, content=b'{"next": "b"}')
    web["answers"]["https://rogue.test/b"] = httpx.Response(200, content=b'{"site": "x"}')
    source = rogue(
        follow=lambda answers, subject: [f"https://rogue.test/{answers[0].json['next']}"],
        read=lambda answers, subject: [
            api.OfferedValue("website", "https://blackmesa.test/", origin=answers[1].url)
        ],
    )

    (found,) = enrichment.ask_source(source, enrichment.Subject(name="Black Mesa"))

    assert found.origin == "https://rogue.test/b"
    assert web["seen"] == ["https://rogue.test/a", "https://rogue.test/b"]


def test_a_read_that_raises_is_that_sources_line_and_not_the_others_failure(
    details, user, web, monkeypatch
):
    def broken(answers, subject):
        raise RuntimeError("the page changed shape")

    monkeypatch.setattr(Details, "sources", (rogue(read=broken, hosts=("rogue.test",)), DATA))
    enrichment._merged = None
    web["answers"]["https://rogue.test/a"] = httpx.Response(200, content=b"{}")
    says(web, value("website", "https://blackmesa.test/"))

    first, second = enrichment.ask(acme(user), ["rogue", "test-data"])

    assert first.error == "Rogue's answer could not be read." and first.values == ()
    assert second.error == "" and len(second.values) == 1
    enrichment._merged = None


@pytest.mark.parametrize(
    "response,says_what",
    [
        (httpx.Response(429), "fewer requests"),
        (httpx.Response(200, content=b"<html>not json</html>"), "could not be read"),
        (httpx.Response(200, content=b"x" * (enrichment.MAX_BYTES + 1)), "larger than"),
    ],
)
def test_a_service_that_misbehaves_is_a_sentence(web, response, says_what):
    web["answers"]["https://rogue.test/a"] = response
    with pytest.raises(enrichment.Refused, match=says_what):
        enrichment.ask_source(rogue(), enrichment.Subject(name="Black Mesa"))


def test_an_answer_that_is_not_a_200_is_handed_over_with_its_status(web):
    statuses = []
    source = rogue(read=lambda answers, subject: statuses.extend(a.status for a in answers) or [])

    assert enrichment.ask_source(source, enrichment.Subject(name="Black Mesa")) == []
    assert statuses == [404]


# ------------------------------------------------------------- a stranger's text


def cleaned(name, what, **more):
    return enrichment.cleaned(
        api.OfferedValue(name, what, more.pop("origin", "here"), **more), DATA
    )


def test_what_a_source_returns_is_cleaned():
    assert cleaned("name", "Black‮ Mesa\u0007  Research").value == "Black Mesa Research"
    assert cleaned("website", "https://blackmesa.test/", origin="  sameAs\n").origin == "sameAs"
    assert cleaned("founded", "1998-11").value == "1998-11"
    assert cleaned("size", [50, 249]).value == (50, 249)
    assert cleaned("size", (250, None)).value == (250, None)
    assert cleaned("email", "mailto:Jobs@blackmesa.test?subject=Hi").value == "Jobs@blackmesa.test"
    assert cleaned("identifier", ("wikidata", " q95 ")).value == ("wikidata", "Q95")
    assert cleaned("industry", "62").value == "62"
    kept = cleaned(
        "address",
        api.RegisterAddress(street="1 Main‏ St", municipality="Mesa", country="zz"),
    ).value
    assert (kept.street, kept.municipality, kept.country) == ("1 Main St", "Mesa", "")
    held = cleaned(
        "logo",
        "https://blackmesa.test/logo.svg",
        url="javascript:alert(1)",
        licence_url="ftp://x.test/",
        language="pt_br",
        as_of="yesterday",
    )
    assert (held.url, held.licence_url, held.as_of) == ("", "", "")


@pytest.mark.parametrize(
    "name,what",
    [
        ("website", "javascript:alert(1)"),
        ("website", "//blackmesa.test/"),
        ("logo", "data:image/png;base64,AAAA"),
        ("logo", "https:///no-host"),
        ("social", "ftp://blackmesa.test/"),
        ("email", "not an address"),
        ("identifier", ("wikidata", "not an item")),
        ("identifier", ("a-scheme-nobody-knows", "1")),
        ("identifier", "Q95"),
        ("industry", "00"),
        ("legal_state", "acquired"),
        ("founded", "1998-13"),
        ("founded", "next year"),
        ("size", (10, 5)),
        ("size", (-1, 5)),
        ("size", "many"),
        ("address", api.RegisterAddress(country="FR")),
        ("address", "1 Main St"),
        ("name", ""),
        ("shoe_size", "44"),
    ],
)
def test_a_value_its_field_cannot_hold_is_dropped(name, what):
    assert cleaned(name, what) is None


def test_a_value_outside_what_the_source_offers_or_without_an_origin_is_dropped():
    offered = api.OfferedValue("address", api.RegisterAddress(street="1 Main St"), "itemprop")

    assert enrichment.cleaned(offered, SITE) is None, "the site source does not offer addresses"
    assert enrichment.cleaned(offered, DATA) is not None
    assert cleaned("website", "https://blackmesa.test/", origin=" ​ ") is None
    assert enrichment.cleaned({"field": "website"}, DATA) is None


def test_a_source_may_offer_forty_values_and_no_more():
    many = [api.OfferedValue("acronym", f"BM{n}", "list") for n in range(60)]

    assert len(enrichment.cleaned_all(many, DATA)) == enrichment.MAX_VALUES
    assert len(enrichment.cleaned_all([*many[:3], *many[:3]], DATA)) == 3


def test_what_an_errand_holds_is_capped_and_comes_back_cleaned(details):
    wordy = tuple(api.OfferedValue("acronym", f"{n:03d}" + "x" * 190, "o" * 110) for n in range(40))
    results = [enrichment.Result(DATA, values=wordy), enrichment.Result(SITE, error="e" * 900)]

    held = enrichment.held(results, at="2026-11-17T10:00:00+00:00")

    assert len(json.dumps(held)) <= enrichment.MAX_HELD_BYTES
    assert held["sources"][1]["error"] == "e" * 500
    held["sources"][0]["values"].append(value("website", "javascript:alert(1)"))
    held["sources"].append({"key": "a-source-since-removed", "values": [value("name", "x")]})
    back = enrichment.from_held(held)
    assert [result.source.key for result in back] == ["test-data", "test-site"]
    assert all(one.field == "acronym" for one in back[0].values)


# ------------------------------------------------- nothing is asked without a press


def test_with_no_source_installed_nothing_is_drawn_and_the_address_is_not_there(client, user, web):
    company = acme(user, website="https://blackmesa.test")
    client.force_login(user)

    for name in ("jobs:company_detail", "jobs:company_update"):
        html = client.get(reverse(name, args=[company.pk])).content.decode()
        assert "data-find-details" not in html, name
    assert press(client, company, "test-data").status_code == 404
    assert client.get(reverse("jobs:company_enrichment", args=[company.pk])).status_code == 404


def test_the_pages_offer_find_details_and_drawing_them_asks_nobody(client, user, details, web):
    company = acme(user, website="https://blackmesa.test")
    client.force_login(user)

    for name in ("jobs:company_detail", "jobs:company_update"):
        html = client.get(reverse(name, args=[company.pk])).content.decode()
        assert "data-find-details" in html, name
        assert 'name="source" value="test-data" checked' in html
        assert "data.details.test" in html and "the company&#x27;s Wikidata identifier" in html
        assert "blackmesa.test" in html
    assert client.get(reverse("jobs:company_enrichment", args=[company.pk])).status_code == 302
    assert web["seen"] == [] and web["read"] == []
    assert not Errand.objects.exists()


def test_a_source_the_company_cannot_start_is_listed_and_cannot_be_ticked(
    client, user, details, web
):
    company = acme(user, qid="", website="https://blackmesa.test")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert 'name="source" value="test-data" disabled' in html
    assert "a Wikidata identifier" in html
    assert press(client, company, "test-data").status_code == 302
    assert web["seen"] == [], "a hand-written press cannot ask it either"
    assert not Errand.objects.exists()


def test_a_company_no_source_can_start_from_has_no_button(client, user, details):
    company = acme(user, qid="")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "data-find-details" not in html


def test_switched_off_there_is_no_button_and_no_address(client, user, details, web):
    company = acme(user)
    PluginPolicy.objects.create(plugin="test-details", state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "data-find-details" not in html
    assert press(client, company, "test-data").status_code == 404
    assert web["seen"] == []


def test_switched_off_for_one_person_it_is_theirs_alone_that_goes(
    client, user, other_user, details
):
    PluginPolicy.objects.create(
        plugin="test-details", person=user, state=PluginPolicy.State.FORCED_OFF
    )
    forget_decisions()

    assert enrichment.sources(user) == {}
    assert set(enrichment.sources(other_user)) == {"test-data", "test-site"}


# ------------------------------------------------------------------- the press


def test_a_press_asks_the_ticked_sources_and_keeps_nothing(client, user, details, web):
    company = acme(user, website="https://blackmesa.test")
    says(web, value("careers_url", "https://blackmesa.test/jobs", "P9999", url=ITEM))
    site_says(web, value("email", "jobs@blackmesa.test", 'schema.org Organization, "email"'))
    client.force_login(user)

    response, errand = review_of(client, company, "test-data", "test-site")

    assert errand.kind == "enrichment" and errand.state == ErrandState.DONE
    assert errand.subject == company and errand.owner == user
    assert web["seen"] == [ITEM] and web["read"] == ["https://blackmesa.test"]
    html = response.content.decode()
    assert "https://blackmesa.test/jobs" in html and "jobs@blackmesa.test" in html
    assert "P9999" in html and "schema.org Organization, &quot;email&quot;" in html
    assert "Test Data Office" in html and 'rel="noopener noreferrer external license"' in html
    assert "No licence is stated; the name and the mark are the company" in html
    company.refresh_from_db()
    assert (company.careers_url, company.email) == ("", "")


def test_only_a_ticked_source_is_asked(client, user, details, web):
    company = acme(user, website="https://blackmesa.test")
    says(web, value("careers_url", "https://blackmesa.test/jobs"))
    client.force_login(user)

    review_of(client, company, "test-data")

    assert web["seen"] == [ITEM] and web["read"] == []


def test_a_press_with_nothing_ticked_asks_nobody(client, user, details, web):
    company = acme(user)
    client.force_login(user)

    response = press(client, company)

    assert response.status_code == 302 and response.url == company.get_absolute_url()
    assert web["seen"] == [] and not Errand.objects.exists()


def test_the_press_spends_the_allowance_and_is_refused_once_it_is_spent(
    client, user, details, web, settings
):
    settings.POSTULO_ENRICHMENT_RATE = "1/h"
    company = acme(user)
    says(web, value("careers_url", "https://blackmesa.test/jobs"))
    client.force_login(user)

    review_of(client, company, "test-data")
    second = press(client, company, "test-data")

    assert second.status_code == 302 and second.url == company.get_absolute_url()
    assert web["seen"] == [ITEM], "the second press asked nobody"
    assert Errand.objects.count() == 1


def test_a_source_that_reads_the_site_spends_the_fetch_allowance(
    client, user, details, web, settings
):
    settings.POSTULO_FETCH_RATE = "1/h"
    settings.POSTULO_ENRICHMENT_RATE = "0/h"
    company = acme(user, website="https://blackmesa.test")
    site_says(web, value("email", "jobs@blackmesa.test"))
    client.force_login(user)

    review_of(client, company, "test-site")
    press(client, company, "test-site")

    assert web["read"] == ["https://blackmesa.test"]


def test_with_a_worker_the_press_lands_on_the_page_that_watches_it(
    client, user, details, web, settings
):
    settings.POSTULO_BACKGROUND_WORK = True
    company = acme(user)
    client.force_login(user)

    response = press(client, company, "test-data")

    errand = Errand.objects.get()
    assert response.url == reverse("core:errand", args=[errand.pk])
    assert errand.state == ErrandState.WAITING and web["seen"] == []
    waiting = client.get(
        f"{reverse('jobs:company_enrichment', args=[company.pk])}?errand={errand.pk}"
    )
    assert waiting.url == reverse("core:errand", args=[errand.pk])


def test_a_press_where_every_source_fails_says_why_and_holds_nothing(client, user, details, web):
    company = acme(user)
    web["answers"][ITEM] = httpx.Response(429)
    client.force_login(user)

    response = press(client, company, "test-data")

    errand = Errand.objects.get()
    assert response.url == reverse("core:errand", args=[errand.pk])
    assert errand.state == ErrandState.FAILED and "fewer requests" in errand.error
    assert errand.outcome == {}


def test_one_source_failing_is_a_line_on_the_review(client, user, details, web):
    company = acme(user, website="https://blackmesa.test")
    says(web, value("careers_url", "https://blackmesa.test/jobs"))
    client.force_login(user)

    response, _errand = review_of(client, company, "test-data", "test-site")

    html = response.content.decode()
    assert "data-enrichment-problem" in html
    assert "Its own website could not be read." in html and "(404)" in html
    assert "https://blackmesa.test/jobs" in html


# ------------------------------------------------------------------ the review


def everything(web):
    says(
        web,
        value("careers_url", "https://blackmesa.test/jobs", "P9999"),
        value("email", "jobs@blackmesa.test", "P968"),
        value("acronym", "BMRF", "P1813"),
        value("identifier", ["lei", "529900T8BM49AURSDO55"], "P1278"),
        value("address", {"street": "1 Main St", "municipality": "Mesa", "country": "US"}, "P159"),
        value("industry", "72", "P452"),
        value("legal_state", "dissolved", "P576", as_of="2001-05-16"),
        value("founded", "1958", "P571"),
        value("size", [1000, 4999], "P1128", as_of="1998"),
        value("name", "Black Mesa Research Facility", "label"),
        value("logo", "https://data.details.test/files/lambda.svg", "P154", credit="Valve"),
    )


def test_the_review_ticks_nothing(client, user, details, web):
    company = acme(user)
    everything(web)
    client.force_login(user)

    response, _errand = review_of(client, company, "test-data")

    html = response.content.decode()
    found = response.context["review"]
    assert [group.field for group in found.groups] == [
        "careers_url",
        "email",
        "acronym",
        "identifier",
        "address",
        "industry",
        "legal_state",
        "logo",
    ]
    assert html.count('name="take"') == 7
    assert not re.search(r'<input[^>]*name="take"[^>]*checked', html)
    assert not re.search(r'<input[^>]*name="choose-legal_state" value="[^"]+"[^>]*checked', html)
    assert '<input type="radio" class="input" name="choose-legal_state" value="" checked>' in html
    assert [row.label for row in found.also] == ["Founded", "People employed"]
    assert "1000 to 4999" in html and "As of 1998." in html
    assert [(row.label, row.current) for row in found.said] == [("Name", "Black Mesa")]
    assert "Valve" in html
    assert "<img" not in html.split("data-enrichment-offers")[1], "an offered logo is not drawn"


def test_only_what_is_ticked_is_kept_and_the_history_names_the_source(client, user, details, web):
    company = acme(user)
    everything(web)
    client.force_login(user)
    response, errand = review_of(client, company, "test-data")

    kept = keep(client, company, errand, tick_for(response, "email"), tick_for(response, "acronym"))

    assert kept.status_code == 302 and kept.url == company.get_absolute_url()
    company.refresh_from_db()
    assert company.email == "jobs@blackmesa.test"
    assert [row.name for row in company.acronyms()] == ["BMRF"]
    assert company.careers_url == "" and company.legal_state == "active"
    assert company.postal_addresses.count() == 0 and company.industries.count() == 0
    assert not company.identifiers.filter(scheme="lei").exists()
    changes = RecordChange.objects.filter(object_id=company.pk).order_by("pk")
    assert [(change.via, change.fields) for change in changes] == [
        ("Test Data", ["email"]),
        ("Test Data", ["names"]),
    ]


def test_every_kind_of_value_is_kept_where_it_belongs(client, user, details, web, monkeypatch):
    from postulo.jobs import logos

    company = acme(user)
    everything(web)
    monkeypatch.setattr(logos, "download", lambda url: b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    client.force_login(user)
    response, errand = review_of(client, company, "test-data")
    ticks = [choice.id for choice in response.context["review"].choices()]
    state = tick_for(response, "legal_state")

    keep(client, company, errand, *ticks, **{"choose-legal_state": state})

    company.refresh_from_db()
    assert company.careers_url == "https://blackmesa.test/jobs"
    assert company.identifiers.get(scheme="lei").value == "529900T8BM49AURSDO55"
    (address,) = company.postal_addresses.all()
    assert (address.street, address.country, address.label) == ("1 Main St", "US", "Head office")
    assert address.is_primary
    assert [industry.code for industry in company.industries.all()] == ["72"]
    assert company.legal_state == "dissolved" and str(company.legal_state_since) == "2001-05-16"
    assert company.has_logo and company.logo_source == "url"
    assert company.logo_source_url == "https://data.details.test/files/lambda.svg"
    assert company.name == "Black Mesa", "a name is said, never kept"


def test_a_hand_written_post_cannot_keep_what_the_page_did_not_offer(client, user, details, web):
    company = acme(user, careers_url="https://blackmesa.test/careers")
    everything(web)
    client.force_login(user)
    _response, errand = review_of(client, company, "test-data")

    client.post(
        reverse("jobs:company_enrichment", args=[company.pk]),
        {
            "errand": errand.pk,
            "keep": "1",
            "take": ["careers_url-0", "name-0", "founded-0", "website-https://evil.test/"],
            "choose-legal_state": "acquired",
            "careers_url": "https://evil.test/",
            "name": "Evil",
        },
    )

    company.refresh_from_db()
    assert company.careers_url == "https://blackmesa.test/careers"
    assert (company.name, company.website, company.legal_state) == ("Black Mesa", "", "active")
    assert not RecordChange.objects.filter(object_id=company.pk).exists()


def test_a_filled_field_that_holds_one_value_is_said_and_never_offered(client, user, details, web):
    company = acme(user, careers_url="https://blackmesa.test/careers", email="hr@blackmesa.test")
    says(
        web,
        value("careers_url", "https://blackmesa.test/jobs", "P9999"),
        value("email", "HR@blackmesa.test", "P968"),
    )
    client.force_login(user)

    response, errand = review_of(client, company, "test-data")

    found = response.context["review"]
    assert found.groups == [], "nothing to tick"
    assert [(row.text, row.current) for row in found.said] == [
        ("https://blackmesa.test/jobs", "https://blackmesa.test/careers")
    ], "and the address it already has, spelt another way, is not even said"
    html = response.content.decode()
    assert "The company already has" in html and 'name="keep"' not in html
    keep(client, company, errand, "careers_url-0")
    company.refresh_from_db()
    assert company.careers_url == "https://blackmesa.test/careers"


def test_what_the_company_already_holds_is_not_offered_again(client, user, details, web):
    company = acme(user)
    company.add_acronym("BMRF", "typed")
    CompanyIdentifier.objects.create(
        owner=user, company=company, scheme="isni", value="0000 0001 2103 2683"
    )
    says(
        web,
        value("acronym", "bmrf", "P1813"),
        value("identifier", ["wikidata", "Q95"], "itself"),
        value("identifier", ["isni", "0000 0001 2103 2683"], "P213"),
        value("legal_state", "active", "P576"),
    )
    client.force_login(user)

    response, _errand = review_of(client, company, "test-data")

    found = response.context["review"]
    assert found.groups == [] and found.said == [] and found.also == []


def test_two_sources_that_say_the_same_thing_are_one_line_naming_both(client, user, details, web):
    company = acme(user, website="https://blackmesa.test")
    says(web, value("email", "jobs@blackmesa.test", "P968"))
    site_says(web, value("email", "Jobs@BlackMesa.test", "a mailto link"))
    client.force_login(user)

    response, errand = review_of(client, company, "test-data", "test-site")

    (group,) = response.context["review"].groups
    (choice,) = group.choices
    assert [origin.source for origin in choice.origins] == ["Test Data", "Its own website"]
    assert not group.radio
    keep(client, company, errand, choice.id)
    assert RecordChange.objects.get(object_id=company.pk).via == "Test Data, Its own website"


def test_sources_that_disagree_about_one_value_are_a_choice_that_starts_on_neither(
    client, user, details, web
):
    company = acme(user, website="https://blackmesa.test")
    says(web, value("email", "jobs@blackmesa.test", "P968"))
    site_says(web, value("email", "info@blackmesa.test", "a mailto link"))
    client.force_login(user)

    response, errand = review_of(client, company, "test-data", "test-site")

    (group,) = response.context["review"].groups
    assert group.radio and len(group.choices) == 2
    html = response.content.decode()
    assert '<input type="radio" class="input" name="choose-email" value="" checked>' in html
    assert 'name="take"' not in html
    # Both sent by hand: a field that holds one value keeps one.
    keep(client, company, errand, **{"choose-email": group.choices[1].id})
    company.refresh_from_db()
    assert company.email == "info@blackmesa.test"


def test_a_number_and_a_profile_are_added_beside_the_ones_it_has(client, user, details, web):
    from postulo.core.models import PhoneNumber

    company = acme(user)
    PhoneNumber.objects.create(
        owner=user, holder=company, number="+33 1 23 45 67 89", is_primary=True
    )
    says(
        web,
        value("phone", "+33 1 98 76 54 32", "P1329"),
        value("phone", "+33123456789", "P1329"),
        value("phone", "12", "P1329"),
        value("social", "https://www.linkedin.com/company/black-mesa", "P4264"),
        value("social", "https://unknown-network.test/blackmesa", "sameAs"),
    )
    client.force_login(user)

    response, errand = review_of(client, company, "test-data")

    found = response.context["review"]
    assert [group.field for group in found.groups] == ["phone", "social"]
    assert [len(group.choices) for group in found.groups] == [1, 1]
    assert {row.label for row in found.said} == {"Telephone number", "Social profile"}
    keep(client, company, errand, *[choice.id for choice in found.choices()])
    assert company.phone_numbers.count() == 2
    assert company.phone_numbers.get(is_primary=True).normalised == "+33123456789"
    (link,) = company.web_links.all()
    assert (link.kind, link.service) == ("social", "linkedin")


# ------------------------------------------------- nothing is kept without the person


def test_what_was_held_is_gone_after_keep_and_after_cancel(client, user, details, web):
    company = acme(user)
    everything(web)
    client.force_login(user)
    url = reverse("jobs:company_enrichment", args=[company.pk])

    response, errand = review_of(client, company, "test-data")
    assert "held" in errand.outcome
    keep(client, company, errand, tick_for(response, "email"))
    errand.refresh_from_db()
    assert "held" not in errand.outcome and "BMRF" not in json.dumps(errand.outcome)
    assert client.get(f"{url}?errand={errand.pk}").status_code == 302, "nothing left to review"
    assert keep(client, company, errand, "acronym-0").status_code == 302

    caches["ratelimit"].clear()
    _response, second = review_of(client, company, "test-data")
    cancelled = client.post(url, {"errand": second.pk, "cancel": "1"})
    second.refresh_from_db()
    assert cancelled.status_code == 302 and "held" not in second.outcome
    company.refresh_from_db()
    assert [row.name for row in company.acronyms()] == []


def test_a_review_opened_after_the_company_is_gone_forgets_what_was_held(
    client, user, details, web
):
    company = acme(user)
    everything(web)
    client.force_login(user)
    _response, errand = review_of(client, company, "test-data")
    url = f"{reverse('jobs:company_enrichment', args=[company.pk])}?errand={errand.pk}"
    company.delete()

    assert client.get(url).status_code == 404
    errand.refresh_from_db()
    assert "held" not in errand.outcome


def test_a_source_switched_off_after_the_press_is_not_offered_from(client, user, details, web):
    company = acme(user, website="https://blackmesa.test")
    everything(web)
    client.force_login(user)
    _response, errand = review_of(client, company, "test-data")
    registry.unregister_builtin("company-enrichment", Details)

    class OnlyTheSite:
        name = "test-details"
        kind = "company-enrichment"
        sources = (SITE,)

    with installed(OnlyTheSite):
        response = client.get(
            f"{reverse('jobs:company_enrichment', args=[company.pk])}?errand={errand.pk}"
        )
    registry.register_builtin("company-enrichment", Details)

    assert response.context["review"].groups == []


# ------------------------------------------------------------ whose it is (#896)


def test_another_persons_errand_and_another_persons_company_are_not_found(
    client, user, other_user, details, web
):
    theirs = acme(other_user)
    mine = acme(user, name="Aperture")
    everything(web)
    client.force_login(other_user)
    _response, their_errand = review_of(client, theirs, "test-data")
    client.force_login(user)

    assert press(client, theirs, "test-data").status_code == 404
    url = reverse("jobs:company_enrichment", args=[mine.pk])
    assert client.get(f"{url}?errand={their_errand.pk}").status_code == 404
    assert client.post(url, {"errand": their_errand.pk, "keep": "1"}).status_code == 404
    their_errand.refresh_from_db()
    assert "held" in their_errand.outcome, "and theirs is left as it was"


def test_an_errand_about_one_company_cannot_be_kept_on_another(client, user, details, web):
    first, second = acme(user), acme(user, name="Aperture", qid="")
    everything(web)
    client.force_login(user)
    response, errand = review_of(client, first, "test-data")

    answer = keep(client, second, errand, tick_for(response, "email"))

    assert answer.status_code == 404
    second.refresh_from_db()
    assert second.email == ""


def test_a_read_grant_cannot_press_and_a_write_grant_is_asked_about_the_shared_company(
    client, user, other_user, details, web
):
    company = acme(other_user)
    grant = Share.objects.create(company=company, grantee_user=user, scope=Share.Scope.READ)
    everything(web)
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "data-find-details" not in html
    assert press(client, company, "test-data").status_code == 404
    assert web["seen"] == []

    grant.scope = Share.Scope.WRITE
    grant.save()
    response, errand = review_of(client, company, "test-data")

    assert errand.owner == user and errand.state == ErrandState.DONE, errand.error
    keep(client, company, errand, tick_for(response, "email"))
    company.refresh_from_db()
    assert company.email == "jobs@blackmesa.test"


def test_a_grant_taken_back_while_the_errand_waited_finds_no_company(
    user, other_user, details, web, settings
):
    from postulo.core import errands

    settings.POSTULO_BACKGROUND_WORK = True
    company = acme(other_user)
    grant = Share.objects.create(company=company, grantee_user=user, scope=Share.Scope.WRITE)
    errand = errands.send(
        "enrichment", user, subject=company, company_id=company.pk, sources=["test-data"]
    )
    grant.delete()

    errands.perform(errand.pk)

    errand.refresh_from_db()
    assert errand.state == ErrandState.FAILED and errand.error == "That company is no longer here."
    assert web["seen"] == []


# ------------------------------------------------------------------ paperwork


def test_the_record_of_processing_lists_a_source_only_while_it_is_on(db):
    assert gdpr.record_of_processing()["services"] == []

    with installed():
        services = {row["key"]: row for row in gdpr.record_of_processing()["services"]}
        assert set(services) == {"test-data", "test-site"}
        assert services["test-data"]["hosts"] == ["data.details.test"]
        assert services["test-data"]["sends"] == "the company's Wikidata identifier"
        assert services["test-data"]["licence"] == "CC0"
        assert services["test-site"]["hosts"] == []

        PluginPolicy.objects.create(plugin="test-details", state=PluginPolicy.State.FORCED_OFF)
        forget_decisions()
        assert gdpr.record_of_processing()["services"] == []


def test_the_rate_is_a_setting_with_a_default():
    from postulo.core import checks, configuration

    assert "POSTULO_ENRICHMENT_RATE" in checks.rate_settings()
    assert configuration.VARIABLES["POSTULO_ENRICHMENT_RATE"].default == "30/h"


# ----------------------------------------------------------- the published check


SUBJECT = api.EnrichmentSubject(identifiers={"wikidata": QID})
RECORDED = [
    api.EnrichmentAnswer(
        url=ITEM,
        status=200,
        media_type="application/json",
        json={
            "values": [
                value("website", "https://blackmesa.test/", "P856", url=ITEM),
                value("founded", "1958", "P571"),
                value("website", "javascript:alert(1)", "P856"),
            ]
        },
    )
]


def test_a_well_formed_plugin_passes_the_published_check_and_gets_its_values_back(db):
    found = assert_is_a_company_enrichment_source(
        Details(), subject=SUBJECT, answers=RECORDED, key="test-data"
    )

    assert [(one.field, one.value) for one in found] == [
        ("website", "https://blackmesa.test/"),
        ("founded", "1958"),
    ], "cleaned as Postulo would hold them"


def a_plugin(*sources, kind="company-enrichment"):
    class Plugin:
        name = "under-test"

    Plugin.kind = kind
    Plugin.sources = tuple(sources)
    return Plugin()


def dials(subject):
    import socket

    socket.getaddrinfo("data.details.test", 443)
    return [ITEM]


@pytest.mark.parametrize(
    "plugin,complaint",
    [
        (object(), "does not provide"),
        (a_plugin(DATA, kind="feature"), "its kind is"),
        (a_plugin(), "contributes no source"),
        (a_plugin(DATA, SITE), "say which source"),
        (a_plugin(rogue(key="Not A Key")), "key must be"),
        (a_plugin(rogue(hosts=())), "exactly one of hosts and own_site"),
        (a_plugin(rogue(offers=("shoe_size",))), "offers must be"),
        (a_plugin(rogue(addresses=lambda s: ["https://elsewhere.test/"])), "asks for"),
        (a_plugin(rogue(addresses=lambda s: ["http://rogue.test/"])), "asks for"),
        (a_plugin(rogue(addresses=lambda s: [])), "names no address"),
        (
            a_plugin(rogue(addresses=lambda s: [f"https://rogue.test/{n}" for n in range(5)])),
            "one press allows",
        ),
        (a_plugin(rogue(follow=lambda a, s: ["https://elsewhere.test/"])), "asks for"),
        (a_plugin(rogue(read=lambda a, s: ["a string"])), "read returned"),
        (
            a_plugin(rogue(read=lambda a, s: [api.OfferedValue("email", "a@b.test", "here")])),
            "does not offer",
        ),
        (
            a_plugin(rogue(read=lambda a, s: [api.OfferedValue("website", "https://x.test/", "")])),
            "does not say where",
        ),
        (a_plugin(rogue(addresses=dials)), "reached for the network"),
    ],
)
def test_the_published_check_fails_each_way_a_plugin_can_break_the_contract(db, plugin, complaint):
    with pytest.raises(AssertionError, match=complaint):
        assert_is_a_company_enrichment_source(
            plugin, subject=api.EnrichmentSubject(name="Black Mesa"), answers=RECORDED
        )


def test_the_offers_module_knows_every_field_the_contract_names():
    sorted_somewhere = (
        *enrichment_offers.SINGLE,
        *enrichment_offers.REPLACED,
        *enrichment_offers.SEVERAL,
        *enrichment_offers.ONLY_SAID,
    )

    assert sorted(sorted_somewhere) == sorted(enrichment.FIELDS)
    assert set(enrichment_offers.LABELS) == set(enrichment.FIELDS)
    assert set(enrichment_offers.TAKERS) == set(enrichment.FIELDS) - set(
        enrichment_offers.ONLY_SAID
    )
