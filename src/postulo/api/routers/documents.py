"""CVs, letters and files. Files themselves travel only under ``documents:read``."""

import datetime as dt

from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.core.files import serve_private_file
from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Page, changed_since
from ..schemas import (
    CVDetailOut,
    CVOut,
    DocumentOut,
    LetterDetailOut,
    LetterIn,
    LetterOut,
    document_out,
)
from .common import owned, owned_or_404

router = Router(tags=["documents"], auth=scope("read"))


def _cv_out(cv: CV, *, detail: bool = False) -> dict:
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
    return data


def _letter_out(letter: CoverLetter, *, detail: bool = False) -> dict:
    data = {
        "id": letter.pk,
        "name": letter.name,
        "subject": letter.subject,
        "is_template": letter.is_template,
        "theme": letter.theme,
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
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    return changed_since(owned(request, CV.objects).order_by("name"), updated_since, after_id)


@router.get("/cvs/{int:pk}", response=CVDetailOut, summary="One CV, with what it includes")
def get_cv(request, pk: int):
    return _cv_out(owned_or_404(request, CV.objects, pk), detail=True)


@router.get("/letters", response=list[LetterOut], summary="List cover letters")
@paginate(Page, row=lambda request, letter: _letter_out(letter))
def list_letters(
    request,
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
    after_id: int | None = Query(None, description=AFTER_ID),
):
    return changed_since(
        owned(request, CoverLetter.objects).order_by("name"), updated_since, after_id
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
    updated_since: dt.datetime | None = Query(None, description=UPDATED_SINCE),
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
        raise HttpError(422, "'source' must be upload or rendered.")
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
    raise HttpError(404, "No such kind of document.")
