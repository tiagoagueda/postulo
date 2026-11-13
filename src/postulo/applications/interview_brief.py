"""An interview's own page, and the same page as a PDF to read on the way there (#1091).

Before an interview a person opened four or five pages to remember what they had applied for:
the listing, the company, the contacts, the CV and the letter that were sent, the timeline.
This gathers them under the interview, for the page and for the document.

**One set of plain values for both.** `as_context` builds strings, numbers, booleans, dates and
moments, in lists and mappings, and the page and a theme's template are both handed it -- never
a model -- so the page and the print cannot say different things, and a theme from a plugin
reads the same names the page does (written in the wiki's *Writing a plugin*, as the report's,
a list's and the calendar's are, #340-#342).

**What each part is.**

- *Who you are meeting*: the contacts attending that the reader may open (#896), each with how
  to reach them and the reader's own note on them, which nobody else's is.
- *The listing*: its words, as it was recorded.
- *The company in brief*: what it is doing about hiring, and the reader's own note on it.
- *What you sent*: the frozen documents of this application (`RenderedDocument`), never the
  CV as it is today, which may have moved since (#236).
- *The timeline so far* and *earlier interviews* of the same application, with how each went.
- *Preparation notes*: the interview's own `notes`, the field the form already had.

**A download, never filed**, as a list's and the calendar's are (#341, #342): a page printed
for one's own desk is not something an employer received.

**No model is asked for anything.** The questions to expect are the person's own to write.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import EventKind, Interview, InterviewOutcome

#: How many timeline entries the page and the print carry: the last ones before the meeting,
#: since an application with a long history is read for what just happened.
TIMELINE_LIMIT = 25


def offered_languages() -> list[tuple[str, str]]:
    """The languages the document may be written in: the instance's own, as for a list."""
    from postulo.core import list_export

    return list_export.offered_languages()


def language_for(user, asked: str = "") -> str:
    """The language it is written in: the one asked for where it is offered, else the one a
    document with no language of its own is written in, as for a list (#341)."""
    from postulo.core import list_export

    return list_export.language_for(user, asked)


def theme_for(user, asked: str = "") -> str:
    """The theme it is set in: the one asked for where it sets interviews, else the one this
    person last chose for an interview (`themes.remembered`), else `plain`."""
    from postulo.documents import themes

    theme = themes.find(asked) if asked else None
    if theme is not None and theme.sets(themes.Kind.INTERVIEW):
        return theme.name
    return themes.remembered(getattr(user, "profile", None), themes.Kind.INTERVIEW)


def paper_for(theme: str, user, asked: str = ""):
    """The paper it is drawn on: the one asked for, which raises `CannotRender` where the
    theme does not print on it, else the person's own default (#344)."""
    from postulo.documents import paper_choice, themes

    return paper_choice.for_document(theme, themes.Kind.INTERVIEW, user, asked)


# ------------------------------------------------------------------ the pieces


def _contacts(interview: Interview, user) -> list[dict]:
    """The people attending whom the reader may open, each with how to reach them.

    A contact somebody shared and has since stopped sharing is not drawn, as on the
    application page, where such a name stays a bare name (#1097): here there is nothing to
    say about them, so they are left out rather than their details read.
    """
    from postulo.core import phone_numbers, phones
    from postulo.jobs.models import Contact

    openable = {
        contact.pk: contact
        # What `shared_scope(.., READ)` is: a contact the reader may open, a superuser's
        # reach over every contact included, as on the application page (#1097).
        for contact in Contact.objects.visible_to(user, write=False, everyone=True)
        .filter(interviews=interview)
        .select_related("company", "department")
        .prefetch_related("phone_numbers")
    }
    people = []
    for attending in interview.contacts.all():
        contact = openable.get(attending.pk)
        if contact is None:
            people.append(
                {
                    "name": attending.name,
                    "role": "",
                    "company": "",
                    "department": "",
                    "email": "",
                    "phones": [],
                    "note": "",
                    "url": "",
                    "known": False,
                }
            )
            continue
        people.append(
            {
                "name": contact.name,
                "role": contact.role,
                "company": contact.company.name if contact.company else "",
                "department": contact.department.name if contact.department else "",
                "email": contact.email,
                "phones": [
                    phones.readable(number.number)
                    for number in phone_numbers.numbers_for(contact, user)
                ],
                "note": contact.note_for(user),
                "url": contact.get_absolute_url(),
                "known": True,
            }
        )
    return people


def _company(application, user) -> dict:
    from postulo.jobs import status, structure

    company = application.posting.company
    state = status.for_company(company, user)
    parent = structure.parent_of(company, user)
    return {
        "name": company.name,
        "website": company.website,
        "kind": company.get_kind_display(),
        "parent": parent.name if parent is not None else "",
        "industries": [industry.name for industry in company.industries.all()],
        "state": state.state_label if state.shows_state else "",
        "state_since": state.since if state.shows_state else None,
        "hiring": state.signal_label,
        "hiring_as_of": state.signal.noted_on if state.signal else None,
        "hiring_is_old": state.stale,
        "warning": status.warning(company, signals=state.history),
        "note": company.note_for(user),
        "url": company.get_absolute_url(),
    }


def _listing(application) -> dict:
    posting = application.posting
    return {
        "title": posting.title,
        "location": posting.location,
        "url": posting.url,
        "text": posting.description_text,
        "page": reverse("jobs:posting_detail", args=[posting.pk]),
    }


def _sent(application) -> list[dict]:
    from postulo.documents import kinds

    return [
        {
            "title": document.title,
            "kind": kinds.label_for(document.kind),
            "sent_on": document.rendered_at,
            "language": document.language,
            "download": reverse("documents:rendered_download", args=[document.pk]),
            "pk": document.pk,
        }
        for document in application.rendered_documents.all()
    ]


def _timeline(interview: Interview) -> list[dict]:
    """What had been recorded about the application by the time of the meeting, newest last.

    An interview still ahead has the whole of it; one that has been and gone is read as it
    stood when it began, so a page opened a month later does not mix in what came after.
    """
    events = interview.application.events.all()
    if interview.starts_at < timezone.now():
        events = events.filter(occurred_at__lte=interview.starts_at)
    rows = list(events.order_by("-occurred_at", "-pk")[:TIMELINE_LIMIT])
    rows.reverse()
    return [
        {
            "kind": event.get_kind_display(),
            "at": event.occurred_at,
            "summary": event.summary,
            "body": event.body,
        }
        for event in rows
    ]


def _earlier(interview: Interview) -> list[dict]:
    """The application's other interviews that came before this one, and how each went."""
    before = (
        Interview.objects.for_user(interview.owner)
        .filter(application=interview.application, starts_at__lt=interview.starts_at)
        .exclude(pk=interview.pk)
        .prefetch_related("events")
        .order_by("starts_at", "pk")
    )
    rows = []
    for other in before:
        # What was said when it was settled; the scheduling entry's body is only its place.
        notes = [
            event.body
            for event in other.events.all()
            if event.body and event.kind != EventKind.INTERVIEW_SCHEDULED
        ]
        rows.append(
            {
                "kind": other.get_kind_display(),
                "starts_at": other.starts_at,
                "outcome": other.get_outcome_display(),
                "settled": other.outcome != InterviewOutcome.SCHEDULED,
                "note": "\n".join(notes),
            }
        )
    return rows


def as_context(user, interview: Interview) -> dict:
    """The interview as the page and a theme's template are handed it: plain values only.

    Built in the language and the zone in force, so every label and every date is already
    written in it; the caller sets that. ``interview`` is one of ``user``'s own.
    """
    application = interview.application
    company = _company(application, user)
    listing = _listing(application)
    return {
        "title": str(
            _("%(kind)s · %(role)s at %(company)s")
            % {
                "kind": interview.get_kind_display(),
                "role": listing["title"],
                "company": company["name"],
            }
        ),
        "person": user.display_name if hasattr(user, "display_name") else str(user),
        "produced_at": timezone.localtime(),
        "kind": interview.get_kind_display(),
        "starts_at": timezone.localtime(interview.starts_at),
        "ends_at": timezone.localtime(interview.ends_at),
        "location": interview.location,
        "location_is_link": interview.is_link,
        "outcome": interview.get_outcome_display(),
        "settled": interview.is_settled,
        "notes": interview.notes,
        "contacts": _contacts(interview, user),
        "listing": listing,
        "company": company,
        "sent": _sent(application),
        "timeline": _timeline(interview),
        "earlier": _earlier(interview),
    }


def title_of(context: dict) -> str:
    """What the document is called, in the language it is written in."""
    return context["title"]


def filename(interview: Interview) -> str:
    """A name that says which meeting it is for without being opened."""
    return (
        f"postulo-interview-{timezone.localtime(interview.starts_at):%Y-%m-%d}-{interview.pk}.pdf"
    )


def draw(request, interview: Interview, *, theme: str = "", language: str = "", paper: str = ""):
    """The interview set as a document: ``(html, theme, language)``, the last two being what
    it was drawn in. Raises `CannotRender` for a paper the theme does not print on."""
    from django.template.loader import render_to_string

    from postulo.core import languages
    from postulo.documents import paper as papers
    from postulo.documents import theme_assets, themes

    user = request.user
    theme = theme_for(user, theme)
    language = language_for(user, language)
    chosen = paper_for(theme, user, paper)
    # A file handed to a renderer in this process, never a page a browser is served, so no
    # policy reads it and its styles need no nonce -- as for a list's and the calendar's.
    nonce = None
    with languages.override(language):
        context = as_context(user, interview)
        html = render_to_string(
            themes.template_for(theme, themes.Kind.INTERVIEW),
            {
                **context,
                "document_language": language,
                "document_direction": languages.direction(language),
                "document_title": title_of(context),
                "csp_nonce": nonce,
                "paper": chosen,
                theme_assets.CONTEXT_KEY: theme_assets.Inliner(themes.find(theme)),
            },
        )
    return papers.with_paper(html, chosen, nonce=nonce), theme, language
