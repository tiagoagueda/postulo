"""CVs, letters and files. Files themselves travel only under ``documents:read``."""

from django.db import transaction
from django.utils.translation import gettext as _
from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate
from pydantic import AwareDatetime

from postulo.core.files import serve_private_file
from postulo.documents import printing
from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Page, changed_since
from ..schemas import (
    CVDetailOut,
    CVOut,
    CVPatch,
    DocumentOut,
    LetterDetailOut,
    LetterIn,
    LetterOut,
    document_out,
)
from .common import owned, owned_or_404

router = Router(tags=["documents"], auth=scope("read"))


def _rows_out(owner, key: str) -> list[dict]:
    """The caller's own rows of one kind, as a CV may be told to print them."""
    return [
        {
            "id": row.pk,
            "label": printing.name_of(row, key),
            "value": row.value if key == "identifiers" else printing.value_of(row, key),
        }
        for row in printing.offered(owner, key)
    ]


def _prints_out(cv: CV, *, offered: bool = True) -> dict:
    """Which of its owner's details a CV prints (#308): each answer, what it comes to as
    things stand, and the rows that may be chosen instead -- which are the owner's own, so
    a client learns here every id it is allowed to send back.

    **`offered` is reading, and is left out for a caller that may not read.** It is every
    number, confirmed address, link and identifier in the caller's details, whether this CV
    prints it or not: what `read` is for, and nothing a change to a CV changed. The answer
    to a `PATCH` is otherwise the CV as it stands, so without this a token holding `write`
    alone -- refused `GET /cvs/{id}` and `GET /profile` -- was handed all of it by sending
    a `PATCH` with nothing in it. Left out rather than sent empty, because an empty list
    says the person has no such rows, and that is a different statement.
    """
    printed = printing.resolve(cv.owner, cv)
    data: dict = {}
    for detail in printing.DETAILS:
        row = printing.pinned(cv, detail)
        data[detail.key] = {
            "choice": getattr(cv, detail.choice_field),
            # Only an id that is still the owner's to print. One that has gone is said as
            # nothing, with `chosen` beside it, which is how the page says it too.
            "id": row.pk if row is not None else None,
            "printed": getattr(printed, detail.key),
        }
        if offered:
            data[detail.key]["offered"] = _rows_out(cv.owner, detail.key)
    mine = {row.pk for row in printing.offered(cv.owner, "identifiers")}
    data["identifiers"] = {
        "choice": cv.identifiers_choice,
        "ids": [pk for pk in cv.pinned_identifiers.values_list("pk", flat=True) if pk in mine],
        "printed": [f"{row.display_label} {row.value}" for row in printed.identifiers],
    }
    if offered:
        data["identifiers"]["offered"] = _rows_out(cv.owner, "identifiers")
    for name, column in printing.SWITCHES.items():
        data[name] = getattr(cv, column)
    return data


def _cv_out(cv: CV, *, detail: bool = False, offered: bool = True) -> dict:
    data = {
        "id": cv.pk,
        "name": cv.name,
        "headline": cv.headline,
        "summary": cv.summary,
        "theme": cv.theme,
        "language": cv.language,
        "item_count": cv.items.count(),
        "updated_at": cv.updated_at,
    }
    if detail:
        data["items"] = [
            {
                "kind": item.content_type.model,
                "label": str(item),
                "included": item.is_included,
            }
            for item in cv.items.select_related("content_type").order_by("order")
        ]
        data["show_contact_details"] = cv.show_contact_details
        data["prints"] = _prints_out(cv, offered=offered)
    return data


def _letter_out(letter: CoverLetter, *, detail: bool = False) -> dict:
    data = {
        "id": letter.pk,
        "name": letter.name,
        "subject": letter.subject,
        "is_template": letter.is_template,
        "theme": letter.theme,
        "language": letter.language,
        "created_at": letter.created_at,
        "updated_at": letter.updated_at,
    }
    if detail:
        data["body"] = letter.body
    return data


@router.get("/cvs", response=list[CVOut], summary="List CVs")
@paginate(Page, row=lambda request, cv: _cv_out(cv))
def list_cvs(
    request,
    updated_since: AwareDatetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    return changed_since(owned(request, CV.objects).order_by("name", "pk"), updated_since, after_id)


@router.get("/cvs/{int:pk}", response=CVDetailOut, summary="One CV, with what it includes")
def get_cv(request, pk: int):
    return _cv_out(owned_or_404(request, CV.objects, pk), detail=True)


@router.patch(
    "/cvs/{int:pk}",
    response=CVDetailOut,
    auth=scope("write"),
    summary="Change what a CV prints about you",
    # What makes a key that was not set a key that is not sent: `offered`, for a caller
    # that may not read (`_prints_out`). Everything else in the answer is set every time.
    exclude_unset=True,
)
def patch_cv(request, pk: int, payload: CVPatch):
    """Choose which of your details one CV prints (#308).

    `show_contact_details` is the master switch; `prints` holds an answer per kind of
    detail, and a kind left out is left alone. An answer is `default` (follow your details:
    the primary number, the account's address, the primary link of the kind, every
    identifier), `none`, or `chosen` with the `id` of a row -- `ids` for the identifiers.

    **A row has to be one of yours**: one of the ids the CV's `prints.<kind>.offered`
    lists. Any other id is a 422, in the same words whether the row is somebody else's or
    does not exist, and nothing is stored. So is an id sent beside `default` or `none`.

    Nothing already sent changes: a version is a file, and the choice is read when the
    next one is drawn. Answers with the CV as it now stands, as every `PATCH` here does.

    **`offered` is in the answer only for a token that also holds `read`.** It lists every
    number, confirmed address, link and identifier in your details, which is reading them
    and not seeing what was changed; a token holding `write` alone gets each kind's
    `choice`, `id` (or `ids`) and `printed`, and no `offered`. It may still pin a row whose
    id it was given.
    """
    cv = owned_or_404(request, CV.objects, pk)
    sent = payload.dict(exclude_unset=True)
    answers = {
        name: value for name, value in (sent.get("prints") or {}).items() if value is not None
    }
    changed: list[str] = []
    rows = None
    kind = ""
    try:
        for detail in printing.DETAILS:
            if detail.key in answers:
                kind, answer = detail.key, answers[detail.key]
                printing.choose(cv, kind, answer["choice"], answer.get("id"))
                changed += [detail.choice_field, detail.pin_field]
        if "identifiers" in answers:
            kind, answer = "identifiers", answers["identifiers"]
            rows = printing.choose_identifiers(cv, answer["choice"], answer.get("ids") or [])
            changed.append("identifiers_choice")
    except printing.NotOffered as refused:
        # Nothing has been saved: the answers were being put on an instance, and one that
        # is refused takes the others in the same call with it.
        raise HttpError(422, refused.sentence(f"'prints.{kind}.{refused.field}'")) from refused
    for name, column in printing.SWITCHES.items():
        if name in answers:
            setattr(cv, column, answers[name])
            changed.append(column)
    if sent.get("show_contact_details") is not None:
        cv.show_contact_details = sent["show_contact_details"]
        changed.append("show_contact_details")
    # The answer and the set it names are one change: both, or neither.
    with transaction.atomic():
        if changed:
            cv.save(update_fields=[*changed, "updated_at"])
        if rows is not None:
            cv.pinned_identifiers.set(rows)
    return _cv_out(cv, detail=True, offered=request.auth.has_scope("read"))


@router.get("/letters", response=list[LetterOut], summary="List cover letters")
@paginate(Page, row=lambda request, letter: _letter_out(letter))
def list_letters(
    request,
    updated_since: AwareDatetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    return changed_since(
        owned(request, CoverLetter.objects).order_by("name", "pk"), updated_since, after_id
    )


@router.get("/letters/{int:pk}", response=LetterDetailOut, summary="One letter, with its text")
def get_letter(request, pk: int):
    return _letter_out(owned_or_404(request, CoverLetter.objects, pk), detail=True)


@router.post(
    "/letters", response={201: LetterDetailOut}, auth=scope("write"), summary="Draft a cover letter"
)
def draft_letter(request, payload: LetterIn):
    letter = CoverLetter.objects.create(owner=request.auth.owner, **payload.dict())
    return Status(201, _letter_out(letter, detail=True))


@router.get("/documents", response=list[DocumentOut], summary="List files: uploads and snapshots")
@paginate(Page, row=lambda request, row: document_out(request, row[1], source=row[0]))
def list_documents(
    request,
    source: str | None = Query(None, description="upload or rendered; both by default"),
    updated_since: AwareDatetime | None = Query(None, description=UPDATED_SINCE),
):
    """Uploads and snapshots in one list.

    The one list here that cannot be a queryset: an upload and a render are separate
    tables with separate columns, so they are read and then ordered together in Python.
    Only the rows on the page are shaped, though, which is where the cost was — an
    absolute download address built for every file somebody has ever had (#230).

    It is also the one list that takes no `after_id` (#245). The ids come from two tables,
    so upload 5 and render 5 are different files, and one id used against both would drop
    whichever rows happened to number below it in the table the caller was not walking —
    losing files silently, which is worse than the tie it would fix. A cursor here has to
    name the table as well as the id, and that is a shape of its own.
    """
    if source not in (None, "upload", "rendered"):
        raise HttpError(
            422,
            _("%(field)s must be one of %(choices)s.")
            % {"field": "'source'", "choices": ["upload", "rendered"]},
        )
    rows = []
    if source in (None, "upload"):
        rows += [
            ("upload", d)
            for d in changed_since(owned(request, UploadedDocument.objects), updated_since)
        ]
    if source in (None, "rendered"):
        rows += [
            ("rendered", d)
            for d in changed_since(owned(request, RenderedDocument.objects), updated_since)
        ]
    if updated_since is not None:
        rows.sort(key=lambda row: (row[1].updated_at, row[1].pk))
    else:
        rows.sort(key=lambda row: (row[1].created_at, row[1].pk), reverse=True)
    return rows


@router.get(
    "/documents/{source}/{int:pk}/download",
    auth=scope("documents:read"),
    url_name="document_download",
    summary="Download a file",
)
def download_document(request, source: str, pk: int):
    if source == "upload":
        document = owned_or_404(request, UploadedDocument.objects, pk)
        return serve_private_file(
            request, document.file, download_name=document.download_name, as_attachment=True
        )
    if source == "rendered":
        document = owned_or_404(request, RenderedDocument.objects, pk)
        return serve_private_file(
            request, document.file, download_name=document.download_name, as_attachment=True
        )
    raise HttpError(404, _("No such type of document."))
