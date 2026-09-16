"""What a list costs, what shape it comes back in, and how to ask only for what changed.

Three promises the API was not keeping (#230): that a page is a page's worth of work, that
every list is paginated, and that there is some way to catch up on a search without reading
all of it again.
"""

import datetime as dt
from urllib.parse import quote

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.documents.models import CV, CoverLetter
from postulo.jobs.models import Capture, CaptureStatus, Company, JobPosting

pytestmark = pytest.mark.django_db


def issue(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def cursor(moment: str) -> str:
    """A moment, safe in a query string — the `+` of an offset is a space otherwise."""
    return quote(moment)


def moment(stamp: str) -> dt.datetime:
    """One of the API's own `updated_at` values, read back. Never compare these as text:
    a moment that lands on a whole second is written without a fraction, and sorts after
    one that is not."""
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


@pytest.fixture
def applications(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    made = []
    for number in range(12):
        posting = JobPosting.objects.create(owner=user, company=company, title=f"Role {number}")
        made.append(Application.objects.create(owner=user, posting=posting, status=Status.APPLIED))
    return made


# ------------------------------------------- a page is built from a page, not from everything


def test_a_page_of_cvs_costs_the_page_and_not_the_shelf(client, user):
    """Every CV used to be shaped before the page was cut, and each shaping counted its items.

    Ten CVs meant ten counting queries to hand back two of them, and a hundred meant a
    hundred. The view hands over the queryset now and only the rows that survive the cut
    are shaped, so the counting follows the page.
    """
    for number in range(10):
        CV.objects.create(owner=user, name=f"CV {number}")
    bearer = issue(user, "read")

    with CaptureQueriesContext(connection) as queries:
        body = client.get("/api/v1/cvs?limit=2", **bearer).json()

    counting = [q for q in queries.captured_queries if "documents_cvitem" in q["sql"]]
    assert len(counting) == 2, "one per CV on the page, not one per CV on the shelf"
    assert [cv["name"] for cv in body["items"]] == ["CV 0", "CV 1"]
    assert body["count"] == 10, "the count is still of the whole shelf"


def test_a_page_of_applications_is_the_page_asked_for(client, user, applications):
    bearer = issue(user, "read")

    first = client.get("/api/v1/applications?limit=5", **bearer).json()
    second = client.get("/api/v1/applications?limit=5&offset=5", **bearer).json()

    assert first["count"] == second["count"] == 12
    assert len(first["items"]) == len(second["items"]) == 5
    assert not {a["id"] for a in first["items"]} & {a["id"] for a in second["items"]}


def test_the_whole_list_can_be_walked_without_a_row_being_seen_twice(client, user, applications):
    bearer = issue(user, "read")
    seen = []
    offset = 0
    while True:
        page = client.get(f"/api/v1/applications?limit=5&offset={offset}", **bearer).json()
        if not page["items"]:
            break
        seen += [a["id"] for a in page["items"]]
        offset += 5

    assert sorted(seen) == sorted(a.pk for a in applications)
    assert len(seen) == len(set(seen)), "a stable order, so nothing is handed over twice"


# --------------------------------------------------------- every list says it is a list


def openapi() -> dict:
    from postulo.api.api import api

    return api.get_openapi_schema()


#: The two collections that are not lists of the owner's records. Search answers with the
#: same handful of groups the search page shows, each already bounded by its own `limit`;
#: `captures/known` answers once per posting it was asked about, and the asking is capped
#: at a hundred in the payload. Paging either would be paging the question, not an archive.
NOT_A_LIST = {("/api/v1/search", "get"), ("/api/v1/captures/known", "post")}


def answered_with(operation: dict) -> dict:
    """The schema of a call's happy answer. django-ninja keys its responses by number."""
    responses = operation.get("responses", {})
    answer = responses.get(200) or responses.get("200") or {}
    return answer.get("content", {}).get("application/json", {}).get("schema") or {}


def collection_answers(schema: dict):
    """(path, method, operation) for every call whose answer is a collection of some kind."""
    definitions = schema.get("components", {}).get("schemas", {})

    def resolve(node):
        while isinstance(node, dict) and "$ref" in node:
            node = definitions[node["$ref"].rsplit("/", 1)[-1]]
        return node or {}

    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            body = resolve(answered_with(operation))
            properties = body.get("properties", {})
            if body.get("type") == "array" or {"items", "count"} <= set(properties):
                yield path, method, operation


def test_the_reader_of_the_schema_finds_the_lists():
    """Both tests below would pass on a schema this reader could not read."""
    found = {(path, method) for path, method, _ in collection_answers(openapi())}
    assert ("/api/v1/applications", "get") in found
    assert ("/api/v1/captures", "get") in found
    assert len(found) >= 8


def test_every_list_the_api_offers_is_paginated():
    """*The capture API* promises `limit`, `offset` and `{"items", "count"}` for lists.

    `GET /captures` was a bare array cut at fifty, in no order anybody had asked for, and
    nothing here noticed: the wiki test reads paths and scopes, not shapes (#230).
    """
    unpaginated = []
    for path, method, operation in collection_answers(openapi()):
        if (path, method) in NOT_A_LIST:
            continue
        parameters = {p["name"] for p in operation.get("parameters", [])}
        if "$ref" not in answered_with(operation) or not {"limit", "offset"} <= parameters:
            unpaginated.append(f"{method.upper()} {path}")
    assert not unpaginated, f"lists that hand back everything at once: {unpaginated}"


def test_the_collections_that_are_not_lists_are_still_there():
    """So that the exemptions above cannot outlive the calls they were written for."""
    found = {(path, method) for path, method, _ in collection_answers(openapi())}
    assert NOT_A_LIST <= found


def test_captures_are_paginated_and_newest_first(client, user):
    bearer = issue(user, "captures")
    for number in range(4):
        Capture.objects.create(
            owner=user,
            url=f"https://example.org/j/{number}",
            data={"title": f"Role {number}"},
            status=CaptureStatus.PENDING,
        )
    Capture.objects.create(
        owner=user,
        url="https://example.org/done",
        data={"title": "Seen"},
        status=CaptureStatus.ACCEPTED,
    )

    body = client.get("/api/v1/captures?limit=2", **bearer).json()

    assert body["count"] == 4, "what is waiting, not what has been dealt with"
    assert [c["title"] for c in body["items"]] == ["Role 3", "Role 2"]


# ------------------------------------------------------------------ catching up


def test_a_list_can_be_asked_only_for_what_changed(client, user, applications):
    bearer = issue(user, "read")
    everything = client.get("/api/v1/applications?limit=100", **bearer).json()
    assert everything["count"] == 12
    latest = max(everything["items"], key=lambda a: moment(a["updated_at"]))["updated_at"]

    asked = f"/api/v1/applications?updated_since={cursor(latest)}"
    assert client.get(asked, **bearer).json()["count"] <= 1

    moved = applications[3]
    moved.status = Status.INTERVIEWING
    moved.save(update_fields=["status", "updated_at"])

    caught_up = client.get(asked, **bearer).json()
    assert moved.pk in [a["id"] for a in caught_up["items"]]


def test_catching_up_reads_forward_so_a_cursor_can_advance(client, user, applications):
    """Oldest change first: a client takes the last row it saw and asks again from there.

    Both halves of it, `updated_at` *and* `id` (#245). A timestamp alone cannot get past a
    run of rows saved in the same moment, which is what a loop like the fixture's makes on
    a machine quick enough -- and what an import or a bulk edit makes on any machine.
    """
    bearer = issue(user, "read")
    start = (timezone.now() - dt.timedelta(minutes=5)).isoformat()
    for application in applications[:6]:
        application.save(update_fields=["updated_at"])

    seen = []
    at, last = start, None
    for _page in range(10):
        asked = f"/api/v1/applications?updated_since={cursor(at)}&limit=4"
        if last is not None:
            asked += f"&after_id={last}"
        body = client.get(asked, **bearer).json()
        fresh = [a for a in body["items"] if a["id"] not in seen]
        if not fresh:
            break
        seen += [a["id"] for a in fresh]
        at, last = fresh[-1]["updated_at"], fresh[-1]["id"]

    assert len(seen) == 12, "walking the cursor forward reaches all of them"
    whole = client.get(
        f"/api/v1/applications?updated_since={cursor(start)}&limit=100", **bearer
    ).json()
    stamps = [moment(a["updated_at"]) for a in whole["items"]]
    assert stamps == sorted(stamps), "oldest change first"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/applications",
        "/api/v1/listings?state=all",
        "/api/v1/companies",
        "/api/v1/reminders",
        "/api/v1/interviews?state=all",
        "/api/v1/cvs",
        "/api/v1/letters",
        "/api/v1/documents",
    ],
)
def test_every_list_takes_the_same_cursor_and_hands_one_back(client, user, path):
    """A client should not have to learn which lists can be caught up on and which cannot."""
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    Reminder.objects.create(owner=user, summary="Chase", due_at=timezone.now())
    CV.objects.create(owner=user, name="Main CV")
    CoverLetter.objects.create(owner=user, name="Letter", body="Dear team")
    bearer = issue(user, "read")

    long_ago = (timezone.now() - dt.timedelta(days=1)).isoformat()
    joiner = "&" if "?" in path else "?"
    body = client.get(f"{path}{joiner}updated_since={cursor(long_ago)}", **bearer).json()

    assert "items" in body and "count" in body
    for row in body["items"]:
        assert row["updated_at"], f"{path} hands back rows with no cursor on them"

    ahead = (timezone.now() + dt.timedelta(days=1)).isoformat()
    assert (
        client.get(f"{path}{joiner}updated_since={cursor(ahead)}", **bearer).json()["items"] == []
    )


def test_a_capture_list_can_be_caught_up_on_too(client, user):
    bearer = issue(user, "captures")
    Capture.objects.create(owner=user, url="https://example.org/j/1", data={"title": "One"})
    long_ago = (timezone.now() - dt.timedelta(days=1)).isoformat()

    body = client.get(f"/api/v1/captures?updated_since={cursor(long_ago)}", **bearer).json()

    assert [c["title"] for c in body["items"]] == ["One"]
    assert body["items"][0]["updated_at"]


def test_a_run_of_rows_saved_in_one_moment_can_still_be_walked(client, user, applications):
    """The fault #245 is about, made on purpose rather than waited for.

    Every row is given one identical `updated_at`, so the whole account is a single tie
    group far larger than the page. With a timestamp alone the second page is the first
    page again, for ever.
    """
    bearer = issue(user, "read")
    moment_they_all_share = timezone.now() - dt.timedelta(minutes=1)
    Application.objects.filter(owner=user).update(updated_at=moment_they_all_share)
    start = (moment_they_all_share - dt.timedelta(minutes=1)).isoformat()

    seen = []
    at, last = start, None
    for _page in range(20):
        asked = f"/api/v1/applications?updated_since={cursor(at)}&limit=4"
        if last is not None:
            asked += f"&after_id={last}"
        items = client.get(asked, **bearer).json()["items"]
        if not items:
            break
        seen += [a["id"] for a in items]
        at, last = items[-1]["updated_at"], items[-1]["id"]

    assert seen == sorted(seen), "in one order, each row once"
    assert len(seen) == 12, "a tie group bigger than the page is still walked to the end"


def test_without_the_id_the_cursor_answers_as_it_always_did(client, user, applications):
    """`after_id` is optional, so a caller written before it keeps working."""
    bearer = issue(user, "read")
    start = (timezone.now() - dt.timedelta(minutes=5)).isoformat()

    body = client.get(f"/api/v1/applications?updated_since={cursor(start)}&limit=100", **bearer)

    assert body.json()["count"] == 12
