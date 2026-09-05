"""CVs, letters and files. Files themselves travel only under ``documents:read``."""

from ninja import Query, Router, Status
from ninja.errors import HttpError
from ninja.pagination import paginate

from postulo.core.files import serve_private_file
from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument

from ..auth import scope
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
    }
    if detail:
        data["body"] = letter.body
    return data


@router.get("/cvs", response=list[CVOut], summary="List CVs")
@paginate
def list_cvs(request):
    return [_cv_out(cv) for cv in owned(request, CV.objects).order_by("name")]


@router.get("/cvs/{int:pk}", response=CVDetailOut, summary="One CV, with what it includes")
def get_cv(request, pk: int):
    return _cv_out(owned_or_404(request, CV.objects, pk), detail=True)


@router.get("/letters", response=list[LetterOut], summary="List cover letters")
@paginate
def list_letters(request):
    return [_letter_out(letter) for letter in owned(request, CoverLetter.objects).order_by("name")]


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
@paginate
def list_documents(
    request, source: str | None = Query(None, description="upload or rendered; both by default")
):
    rows = []
    if source in (None, "upload"):
        rows += [
            document_out(request, d, source="upload")
            for d in owned(request, UploadedDocument.objects).order_by("-created_at")
        ]
    if source in (None, "rendered"):
        rows += [
            document_out(request, d, source="rendered")
            for d in owned(request, RenderedDocument.objects).order_by("-created_at")
        ]
    if source not in (None, "upload", "rendered"):
        raise HttpError(422, "'source' must be upload or rendered.")
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
            request, document.file, download_name=f"{document.title}.pdf", as_attachment=True
        )
    if source == "rendered":
        document = owned_or_404(request, RenderedDocument.objects, pk)
        return serve_private_file(
            request, document.file, download_name=f"{document.title}.pdf", as_attachment=True
        )
    raise HttpError(404, "No such kind of document.")
