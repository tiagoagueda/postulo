"""Answers to application forms: the saved ones, and the copy an application keeps (#1092).

An application form asks the same questions again and again. A saved answer is written once;
using it on an application makes a *copy* the application keeps, so that what was sent does
not change when the saved answer is edited, and can be edited on its own without touching it.

The copy has its placeholders filled in (`{{ company }}` and the rest of a letter's, from the
application it is used on), in the answer's own language where it has one. A placeholder with
nothing to fill it is left as it was written, so that the person sees it in the copy and
writes the word in, rather than a sentence that quietly lost a name.
"""

from __future__ import annotations

from django.utils import formats, timezone

from postulo.core import languages
from postulo.documents.models import CoverLetter, document_language
from postulo.documents.rendering import fill_placeholders

from .models import Application, ApplicationAnswer, SavedAnswer

#: The placeholders an answer may use: the letter's own, so that a person learns one list.
PLACEHOLDERS = CoverLetter.PLACEHOLDERS


def values_for(application: Application, language: str) -> dict[str, str]:
    """What each placeholder is for this application, leaving out what there is none for."""
    owner = application.owner
    posting = application.posting
    values = {
        "name": owner.get_full_name() or owner.display_name,
        "date": formats.date_format(timezone.localdate(), "DATE_FORMAT"),
        "company": posting.company.name_in(language),
        "role": posting.title,
        "location": posting.location,
        "contact": application.contact.name if application.contact_id else "",
    }
    return {key: value for key, value in values.items() if value}


def filled(saved: SavedAnswer, application: Application) -> str:
    """The saved answer's text, with the application's details in the places that name them."""
    language = document_language(saved)
    with languages.override(language):
        return fill_placeholders(saved.answer, values_for(application, language))


def initial_from(saved: SavedAnswer, application: Application) -> dict[str, str]:
    """What the form for an answer given starts with when it begins as a saved one."""
    return {
        "question": saved.question,
        "answer": filled(saved, application),
        "language": saved.language,
    }


def give(
    application: Application, *, question: str, answer: str, language: str, saved=None
) -> ApplicationAnswer:
    """Keep one answer on an application, noting the saved answer it began as."""
    return ApplicationAnswer.objects.create(
        owner=application.owner,
        application=application,
        question=question,
        answer=answer,
        language=language,
        saved_answer=saved,
    )
