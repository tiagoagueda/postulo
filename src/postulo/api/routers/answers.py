"""Answers kept for application forms, read only (#1092).

What an extension or an agent needs to offer an answer where a form asks the question: the
reusable answers, and what each application was told. Nothing is written here: an answer is
made and edited on the pages, where it is read before it is kept.
"""

from ninja import Query, Router
from ninja.pagination import paginate

from postulo.applications.models import ApplicationAnswer, SavedAnswer

from ..auth import scope
from ..paging import AFTER_ID, UPDATED_SINCE, Moment, Page, RowId, changed_since
from ..schemas import AnswerGivenOut, SavedAnswerOut, answer_given_out, saved_answer_out
from .common import owned, owned_or_404

router = Router(tags=["answers"], auth=scope("read"))


def _saved(request):
    return owned(request, SavedAnswer.objects).prefetch_related("tags")


def _given(request):
    return owned(request, ApplicationAnswer.objects)


@router.get("", response=list[SavedAnswerOut], summary="List the saved answers")
@paginate(Page, row=lambda request, saved: saved_answer_out(saved))
def list_saved_answers(
    request,
    tag: str | None = Query(None, description="Only answers with the tag of this slug"),
    language: str | None = Query(None, description="Only answers in this language tag"),
    updated_since: Moment | None = Query(None, description=UPDATED_SINCE),
    after_id: RowId | None = Query(None, description=AFTER_ID),
):
    answers = _saved(request).order_by("question", "pk")
    if tag:
        answers = answers.filter(tags__slug=tag)
    if language:
        answers = answers.filter(language=language)
    return changed_since(answers, updated_since, after_id)


@router.get("/given", response=list[AnswerGivenOut], summary="List the answers applications gave")
@paginate(Page, row=lambda request, given: answer_given_out(given))
def list_answers_given(
    request,
    application: RowId | None = Query(None, description="Only this application's"),
    updated_since: Moment | None = Query(None, description=UPDATED_SINCE),
    after_id: RowId | None = Query(None, description=AFTER_ID),
):
    """Copies, as they were given: what each application was told, whatever the saved answer
    it began as says now."""
    answers = _given(request).order_by("created_at", "pk")
    if application:
        answers = answers.filter(application_id=application)
    return changed_since(answers, updated_since, after_id)


@router.get("/given/{int:pk}", response=AnswerGivenOut, summary="One answer an application gave")
def get_answer_given(request, pk: int):
    return answer_given_out(owned_or_404(request, _given(request), pk))


@router.get("/{int:pk}", response=SavedAnswerOut, summary="One saved answer")
def get_saved_answer(request, pk: int):
    return saved_answer_out(owned_or_404(request, _saved(request), pk))
