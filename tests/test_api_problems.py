"""Every refusal the API makes is an RFC 9457 problem document (#296).

Not "most of them": the point of putting this in handlers on the `NinjaAPI` rather than at
each of the twenty-odd raise sites is that a call added tomorrow refuses in the right shape
without anybody remembering. So the sweep below walks the routers and makes every call
without a token, rather than listing the ones somebody thought of.
"""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest
from django.utils import timezone

from postulo.api import problems
from postulo.api.models import ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.core.models import SiteSettings
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
    # The id that finds this refusal's lines in the log, in the part a person copies (#393).
    assert body["request_id"] and body["request_id"] == response["X-Request-ID"]
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
    same = {"X-Request-ID": "one-and-the-same"}
    schema = client.get("/api/v1/openapi.json", headers=same)
    call = client.get("/api/v1/applications", headers=same)

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


def test_a_refusal_raised_in_the_body_of_a_call_is_described_too():
    """A client cannot type an answer the description never mentions (#431).

    The extension that sends a whole page to `POST /captures` is the one most likely to meet
    a 413, and a retried capture the 409; an `application_id` that is not the caller's is a
    404 on two creates whose address names no record.
    """
    from postulo.api.api import api

    paths = api.get_openapi_schema()["paths"]

    def answers(path, method="post"):
        return set(paths[path][method]["responses"])

    assert {409, 413} <= answers("/api/v1/captures")
    assert {404, 413} <= answers("/api/v1/interviews")
    assert {404, 413} <= answers("/api/v1/reminders")
    assert 413 not in answers("/api/v1/me", "get"), "it takes no body"
    assert 404 not in answers("/api/v1/captures", "get"), "a listing names no record"


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
    """`title` labels the type; `detail` is what a person reads. Only one is translated,
    and that one is.

    RFC 9457 §3.1.2 says `title` SHOULD be the same for every occurrence of a type. A
    client that switched on it would otherwise break for a reader whose Postulo is in
    Portuguese, which is precisely the bug `type` exists to prevent.
    """
    user.profile.language = "pt-PT"
    user.profile.save(update_fields=["language"])

    response = client.get(
        "/api/v1/applications", **issue(user, "captures"), HTTP_ACCEPT_LANGUAGE="pt-PT"
    )
    body = response.json()

    assert body["title"] == problems.TITLES["insufficient-scope"]
    assert body["detail"] != NO_READ_SCOPE and "'read'" in body["detail"]
    assert response["Content-Language"] == "pt-PT"


def test_a_token_that_expired_is_refused_like_one_that_never_existed(client, user):
    """The shape does not leak which it was; #230's promise, kept in the new envelope."""
    expired = issue(user, "read", expires_at=timezone.now() - __import__("datetime").timedelta(1))

    # Given one id, since the id of the request is all that would tell two requests apart.
    same = {"X-Request-ID": "one-and-the-same"}
    gone = client.get("/api/v1/applications", **expired, headers=same)
    never = client.get("/api/v1/applications", HTTP_AUTHORIZATION="Bearer nonsense", headers=same)

    assert assert_is_a_problem(gone, 401) == assert_is_a_problem(never, 401)


# ---------------------------------------------- whose language, and which request (#393)
#
# The docstring and the wiki said `detail` is translated into the account's language. About
# one refusal in five went through gettext at all, and none was in the account's language on
# a call made with a token: the token's owner is not signed in, so nothing ever looked at
# their profile and the client's `Accept-Language` stood.

NO_READ_SCOPE = "This token does not have the 'read' scope."
NO_WRITE_SCOPE = "This token does not have the 'write' scope."
ENDS_BEFORE_IT_STARTS = "'ends_at' must be after 'starts_at'."


def speaking(user, language: str, time_zone: str = "") -> None:
    user.profile.language = language
    user.profile.time_zone = time_zone
    user.profile.save(update_fields=["language", "time_zone"])


def an_interview(application, **changes) -> dict:
    starts = (timezone.now() + dt.timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0, tzinfo=dt.UTC
    )
    return {
        "application_id": application.pk,
        "kind": "panel",
        "starts_at": starts.isoformat(),
        **changes,
    }


def backwards(application) -> dict:
    """An interview that ends an hour before it starts."""
    payload = an_interview(application)
    starts = dt.datetime.fromisoformat(payload["starts_at"])
    return {**payload, "ends_at": (starts - dt.timedelta(hours=1)).isoformat()}


def test_the_words_have_not_changed_for_somebody_who_reads_english(client, user, application):
    """Translating a sentence must not have reworded it where it was already read."""
    assert (
        client.get("/api/v1/applications", **issue(user, "captures")).json()["detail"]
        == NO_READ_SCOPE
    )
    refused = post(client, "/api/v1/interviews", backwards(application), **issue(user, "write"))
    assert assert_is_a_problem(refused, 422)["detail"] == ENDS_BEFORE_IT_STARTS


def test_a_router_s_refusals_are_translated(client, user, application):
    """Two that were English literals, asked for in French by an account that set no
    language of its own: which line refused must not decide what language the answer is in."""
    french = {"HTTP_ACCEPT_LANGUAGE": "fr"}

    scope = client.get("/api/v1/interviews", **issue(user, "captures"), **french)
    dates = post(
        client, "/api/v1/interviews", backwards(application), **issue(user, "write"), **french
    )

    assert assert_is_a_problem(scope, 403)["detail"] != NO_READ_SCOPE
    said = assert_is_a_problem(dates, 422)["detail"]
    assert said != ENDS_BEFORE_IT_STARTS
    assert "'ends_at'" in said and "'starts_at'" in said, "the wire's own names are not translated"


def test_the_sentence_about_a_refused_body_is_translated_too(client, user):
    response = post(
        client,
        "/api/v1/captures",
        {"url": "not-a-url"},
        **issue(user, "captures"),
        HTTP_ACCEPT_LANGUAGE="fr",
    )
    said = assert_is_a_problem(response, 422)["detail"]

    assert "url" in said and "`errors`" in said
    assert not said.startswith("Refused:")


def test_a_refusal_is_in_the_language_of_whoever_owns_the_token(
    client, user, other_user, application, settings
):
    """The account's, whatever the client asked for: a script sends no `Accept-Language`
    and a browser extension sends the browser's, and neither is a choice anybody made about
    Postulo. A 403, a 422 and a 429, which are refused at three different depths."""
    speaking(user, "pt-PT")
    german = {"HTTP_ACCEPT_LANGUAGE": "de"}

    scope = client.get("/api/v1/applications", **issue(user, "captures"), **german)
    dates = post(
        client, "/api/v1/interviews", backwards(application), **issue(user, "write"), **german
    )

    assert assert_is_a_problem(scope, 403)["detail"] != NO_READ_SCOPE
    assert assert_is_a_problem(dates, 422)["detail"] != ENDS_BEFORE_IT_STARTS
    assert scope["Content-Language"] == dates["Content-Language"] == "pt-PT"

    # The allowance is counted before the call is made, by the guard itself.
    settings.POSTULO_API_RATE = "1/m"
    theirs, in_english = issue(user), issue(other_user)
    for headers in (theirs, in_english):
        assert client.get("/api/v1/me", **headers).status_code == 200
    spent = client.get("/api/v1/me", **theirs, **german)
    spent_in_english = client.get("/api/v1/me", **in_english)

    def without_numbers(text: str) -> str:
        return re.sub(r"\d+", "N", text)

    assert spent["Content-Language"] == "pt-PT"
    assert without_numbers(assert_is_a_problem(spent, 429)["detail"]) != without_numbers(
        assert_is_a_problem(spent_in_english, 429)["detail"]
    )


def test_a_call_with_no_token_is_answered_in_the_request_s_language(client):
    """There is no owner to ask, so what the client asked for stands."""
    in_french = client.get("/api/v1/applications", HTTP_ACCEPT_LANGUAGE="fr")
    in_english = client.get("/api/v1/applications")

    assert assert_is_a_problem(in_french, 401)["detail"] != in_english.json()["detail"]
    assert in_english.json()["detail"], "and it is a sentence, not a status phrase"
    assert in_english.json()["detail"] != "Unauthorized"


def test_something_that_is_not_there_is_said_without_naming_a_model(client, user):
    """Django's own sentence was passed through: "No Application matches the given query."."""
    said = client.get("/api/v1/applications/999999", **issue(user)).json()["detail"]

    assert said and "Application" not in said and "query" not in said


def test_a_language_the_instance_withdrew_is_not_applied(client, user):
    """The same rule a signed-in page follows: stored, kept, and not used while withdrawn."""
    speaking(user, "fr-FR")
    SiteSettings.objects.filter(pk=SiteSettings.get().pk).update(offered_languages=["de"])

    response = client.get("/api/v1/applications", **issue(user, "captures"))

    assert response.json()["detail"] == NO_READ_SCOPE


def test_an_interview_booked_with_a_token_is_worded_for_its_owner(client, user, application):
    """What a call writes down is written the way its refusals are said.

    The reminder's sentence carries the time. Booked through a token it was worded in the
    client's `Accept-Language` and in the instance's time zone, for a person who reads
    Portuguese in Tokyo.
    """
    speaking(user, "pt-PT", "Asia/Tokyo")

    response = post(
        client,
        "/api/v1/interviews",
        an_interview(application),
        **issue(user, "write"),
        HTTP_ACCEPT_LANGUAGE="de",
    )
    assert response.status_code == 201, response.content

    summary = Reminder.objects.get(pk=response.json()["reminder_id"]).summary
    assert "18:00" in summary and "09:00" not in summary, "nine in the morning UTC, in Tokyo"
    assert not summary.startswith("Interview tomorrow"), "and in their language"


def test_a_refusal_names_the_request_it_was(client, user):
    """`X-Request-ID` is a header, and a person reporting a refusal copies the body."""
    minted = client.get("/api/v1/applications/999999", **issue(user))
    supplied = client.get(
        "/api/v1/applications/999999", **issue(user), headers={"X-Request-ID": "trace-41"}
    )

    body = assert_is_a_problem(minted, 404)
    assert len(body["request_id"]) == 32
    assert body["instance"] == "/api/v1/applications/999999", "the address is still the address"
    assert assert_is_a_problem(supplied, 404)["request_id"] == "trace-41"


class TestWhatNoOperationAnswers:
    """An address no operation owns is refused as a problem document too (#430).

    The handlers in `problems.install` only see what an operation raises. A mistyped
    address, an id that is not a number and a method an address does not take happen before
    one is chosen, and were Django's and ninja's HTML.
    """

    def test_an_address_that_is_nowhere_is_a_404_with_a_token(self, client, user):
        response = client.get("/api/v1/nothing-here", **issue(user))
        assert_is_a_problem(response, 404)

    def test_an_id_that_is_not_a_number_is_a_404_with_a_token(self, client, user):
        response = client.get("/api/v1/applications/abc", **issue(user))
        assert_is_a_problem(response, 404)

    def test_without_a_token_it_is_the_same_401_as_every_other_call(self, client):
        for path in ("/api/v1/nothing-here", "/api/v1/applications/abc"):
            assert_is_a_problem(client.get(path), 401)

    def test_a_method_the_address_does_not_take_is_a_405_that_keeps_allow(self, client, user):
        response = client.delete("/api/v1/applications", **issue(user))
        assert_is_a_problem(response, 405)
        assert "GET" in response["Allow"]
        assert response.json()["title"] == "Method Not Allowed"
