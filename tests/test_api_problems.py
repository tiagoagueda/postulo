"""Every refusal the API makes is an RFC 9457 problem document (#296).

Not "most of them": the point of putting this in handlers on the `NinjaAPI` rather than at
each of the twenty-odd raise sites is that a call added tomorrow refuses in the right shape
without anybody remembering. So the sweep below walks the routers and makes every call
without a token, rather than listing the ones somebody thought of.
"""

from __future__ import annotations

import json

import pytest
from django.utils import timezone

from postulo.api import problems
from postulo.api.models import ApiToken
from postulo.applications.models import Application, Status
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

PROBLEM = "application/problem+json"

#: Enough of a posting for a capture to succeed without the test touching the network.
PAGE = """<html><head><title>Job</title>
<script type="application/ld+json">{"@context":"https://schema.org/","@type":"JobPosting",
"title":"Research Engineer","hiringOrganization":{"@type":"Organization","name":"Black Mesa"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Lyon"}}}</script></head></html>"""

#: The five members RFC 9457 §3.1 defines. Every document carries all five, even where a
#: member is empty: a client that has to test for a key's presence before reading it is a
#: client doing the work the shape was meant to save it.
MEMBERS = {"type", "title", "status", "detail", "instance"}


def issue(user, *scopes, **kwargs):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",), **kwargs)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


def assert_is_a_problem(response, status: int):
    """What every refusal holds, whatever it is refusing."""
    assert response.status_code == status
    assert response["Content-Type"] == PROBLEM, "a refusal is not the media type of an answer"
    body = response.json()
    assert MEMBERS <= set(body), f"missing {sorted(MEMBERS - set(body))}"
    assert body["status"] == status, "the status is repeated in the body, as the RFC asks"
    assert isinstance(body["detail"], str), "RFC 9457 §3.1.4: detail is a string"
    assert body["instance"].startswith("/api/v1/")
    return body


# ------------------------------------------------------------------ the shape, everywhere


def test_every_call_refuses_without_a_token_in_the_same_shape(client):
    """The sweep. Taken from the routers, so a call added tomorrow is checked tomorrow.

    A GET is enough: authentication runs before anything looks at what was sent, so the
    refusal under test is the same one every method would give.
    """
    from postulo.api.api import api

    addresses = sorted(
        {
            problems._address(prefix, path)
            for prefix, router in api._routers
            for path, view in router.path_operations.items()
            for operation in view.operations
            if "GET" in operation.methods
        }
    )
    assert len(addresses) > 20, "the sweep found nothing to sweep"

    for address in addresses:
        response = client.get(address.replace("{pk}", "1").replace("{source}", "upload"))
        body = assert_is_a_problem(response, 401)
        assert body["title"] == "Unauthorized", address


def test_the_schema_is_refused_in_the_same_shape_as_a_call(client):
    """`openapi.json` is a plain Django view, guarded by hand, so nothing else would notice.

    It is the one refusal in the API that no handler of the API's runs over, which is
    exactly how it would drift out of shape.
    """
    schema = client.get("/api/v1/openapi.json")
    call = client.get("/api/v1/applications")

    assert_is_a_problem(schema, 401)
    assert schema.json() == {**call.json(), "instance": "/api/v1/openapi.json"}


# -------------------------------------------------------------------- the types by name


def test_a_refusal_the_status_code_explains_needs_no_type(client, user):
    """RFC 9457 §4.1: `about:blank` where the status code already says everything.

    A type URI beside a 404 would be ceremony -- there is nothing a client would do with it
    that the code does not already tell it.
    """
    body = assert_is_a_problem(client.get("/api/v1/applications/999999", **issue(user)), 404)

    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"


def test_a_missing_scope_says_which_one(client, user):
    """A client refused here has something to do about it, and should not parse a sentence."""
    body = assert_is_a_problem(client.get("/api/v1/applications", **issue(user, "captures")), 403)

    assert body["type"].endswith("#insufficient-scope")
    assert body["scope"] == "read"
    assert "'read' scope" in body["detail"], "and the sentence a person reads is unchanged"


def test_a_refused_body_puts_the_field_errors_beside_a_sentence(client, user):
    """The one shape that changed: `detail` is a string and the list is `errors` (#296)."""
    response = post(client, "/api/v1/captures", {"url": "not-a-url"}, **issue(user, "captures"))
    body = assert_is_a_problem(response, 422)

    assert body["type"].endswith("#validation-failed")
    assert "url" in body["detail"], "the sentence names what was refused"
    assert isinstance(body["errors"], list) and body["errors"], "and the list is still there"
    assert body["errors"][0]["loc"][-1] == "url"
    assert "msg" in body["errors"][0], "unchanged in shape from what `detail` used to hold"


def test_a_spent_allowance_says_how_long_in_the_body_and_the_header(client, user, settings):
    """The wait is a member as well as a header.

    The header is what an HTTP client obeys without being taught to; the member is what a
    client reading the document finds without reaching back out to the headers.
    """
    settings.POSTULO_API_RATE = "1/m"
    headers = issue(user)
    assert client.get("/api/v1/me", **headers).status_code == 200

    response = client.get("/api/v1/me", **headers)
    body = assert_is_a_problem(response, 429)

    assert body["type"].endswith("#rate-limited")
    assert body["retry_after"] > 0
    assert response["Retry-After"] == str(body["retry_after"]), "the two agree"


def test_both_ways_of_being_rate_limited_answer_alike(client, user, settings):
    """One path raised a bare 429 and the other carried the wait, before #296.

    Whether a client was told *when* depended on which layer refused it, which is the kind
    of difference nobody notices until a client is retrying blind.
    """
    settings.POSTULO_API_RATE = "1/m"
    headers = issue(user, "captures")
    post(
        client,
        "/api/v1/captures",
        {"url": "https://example.org/j/1", "html": PAGE},
        **headers,
    )

    refused = client.get("/api/v1/me", **headers)
    body = assert_is_a_problem(refused, 429)
    assert body["type"].endswith("#rate-limited")
    assert refused["Retry-After"]


def test_a_reused_idempotency_key_is_named_rather_than_described(client, user):
    """A client that sent one key for two postings has a bug, and the type says which."""
    headers = {**issue(user, "captures"), "HTTP_IDEMPOTENCY_KEY": "the-same-key"}
    first = post(
        client,
        "/api/v1/captures",
        {"url": "https://example.org/j/1", "html": PAGE},
        **headers,
    )
    assert first.status_code == 201, first.content

    response = post(
        client,
        "/api/v1/captures",
        {"url": "https://example.org/j/2", "html": PAGE},
        **headers,
    )
    body = assert_is_a_problem(response, 422)

    assert body["type"].endswith("#idempotency-key-reused")


# --------------------------------------------------------------- and the description of it


def test_the_description_carries_the_problem_schema():
    """A client generated from the schema needs a type for the refusal, not only for the answer."""
    from postulo.api.api import api

    schema = api.get_openapi_schema()
    problem = schema["components"]["schemas"]["Problem"]

    assert MEMBERS <= set(problem["properties"])
    assert problem["additionalProperties"] is True, "a type may carry its own members"


def test_every_call_says_it_can_refuse_without_a_token_or_over_its_allowance():
    """Described for every call at once, so the call added tomorrow is described too."""
    from postulo.api.api import api

    schema = api.get_openapi_schema()
    undescribed = [
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        if not {401, 429} <= set(operation.get("responses", {}))
    ]

    assert not undescribed, undescribed


def test_a_call_is_not_described_as_refusing_in_ways_it_cannot():
    """A description that lies is worse than one that is silent.

    A client generated from it writes a branch that never runs, and trusts the absence of
    the branch it needed. `/me` takes nothing and names no record, so it can neither be
    refused for its shape nor fail to find anything; the detail routes can do both.
    """
    from postulo.api.api import api

    schema = api.get_openapi_schema()

    me = schema["paths"]["/api/v1/me"]["get"]["responses"]
    assert 404 not in me and 422 not in me, "it takes nothing and names no record"
    assert 403 not in me, "any live token may ask, whatever its scopes"

    detail = schema["paths"]["/api/v1/applications/{pk}"]["get"]["responses"]
    assert {401, 403, 404, 422, 429} <= set(detail)


def test_the_problem_schema_is_what_a_refusal_actually_answers_with(client, user):
    """The description and the behaviour, checked against each other rather than separately.

    Two tests that each pass alone are how a schema comes to describe a shape nothing
    sends.
    """
    from postulo.api.api import api

    described = set(api.get_openapi_schema()["components"]["schemas"]["Problem"]["properties"])
    sent = set(client.get("/api/v1/applications/999999", **issue(user)).json())

    assert described == sent, "the description names the members a refusal carries, exactly"


def test_a_named_type_is_a_place_a_reader_can_go():
    """A `type` that 404s teaches nobody anything.

    Not fetched here -- a test that needs the network is a test that fails on a train --
    but held to the page the wiki tests already keep current, with the slug as a fragment.
    """
    for kind, title in problems.TITLES.items():
        assert problems.uri(kind) == f"{problems._GUIDE}#{kind}"
        assert title and not title.endswith("."), "a label, not a sentence"


def test_an_unnamed_refusal_falls_back_to_the_status_codes_own_phrase():
    assert problems.uri(None) == "about:blank"
    assert problems.title_of(None, 404) == "Not Found"
    assert problems.title_of(None, 599) == "Error", "a code nothing raises still says something"


def test_the_title_does_not_move_with_the_language(client, user):
    """`title` labels the type; `detail` is what a person reads. Only one is translated.

    RFC 9457 §3.1.2 says `title` SHOULD be the same for every occurrence of a type. A
    client that switched on it would otherwise break for a reader whose Postulo is in
    Portuguese, which is precisely the bug `type` exists to prevent.
    """
    user.profile.language = "pt-PT"
    user.profile.save(update_fields=["language"])

    body = client.get(
        "/api/v1/applications", **issue(user, "captures"), HTTP_ACCEPT_LANGUAGE="pt-pt"
    ).json()

    assert body["title"] == problems.TITLES["insufficient-scope"]


def test_a_token_that_expired_is_refused_like_one_that_never_existed(client, user):
    """The shape does not leak which it was; #230's promise, kept in the new envelope."""
    expired = issue(user, "read", expires_at=timezone.now() - __import__("datetime").timedelta(1))

    gone = client.get("/api/v1/applications", **expired)
    never = client.get("/api/v1/applications", HTTP_AUTHORIZATION="Bearer nonsense")

    assert assert_is_a_problem(gone, 401) == assert_is_a_problem(never, 401)
