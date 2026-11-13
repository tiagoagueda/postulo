"""Message templates: the starters, and a message filled in for an application (#1094).

A letter is a document that is printed; a message is not, so it has a model of its own
(`MessageTemplate`) and shares only the placeholders: a regular expression over a fixed set of
names (`rendering.fill_placeholders`), never a template engine, because a message may carry
text pasted from an advert.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.core import languages

from .models import MessagePurpose, MessageTemplate, document_language
from .rendering import fill_placeholders, letter_values

#: The messages a person can start from: a name, what it is for, a subject and a body, each
#: written in whichever language they are added in. Editable once added, like a letter's
#: starter, and never added without being asked.
STARTERS = (
    (
        MessagePurpose.FOLLOW_UP,
        _("Follow-up after a week of silence"),
        _("Following up on {{ role }}"),
        _(
            "Dear {{ contact }},\n\n"
            "I applied for the {{ role }} position at {{ company }} and wanted to ask whether "
            "there is any news. I remain very interested, and I am happy to send anything that "
            "would help.\n\n"
            "Kind regards,\n{{ name }}"
        ),
    ),
    (
        MessagePurpose.THANK_YOU,
        _("Thank you after an interview"),
        _("Thank you, {{ role }}"),
        _(
            "Dear {{ contact }},\n\n"
            "Thank you for taking the time to talk with me about the {{ role }} position at "
            "{{ company }}. I enjoyed our conversation and I am more convinced than before "
            "that it is work I would like to do.\n\n"
            "Kind regards,\n{{ name }}"
        ),
    ),
    (
        MessagePurpose.WITHDRAWAL,
        _("Withdrawing an application"),
        _("Withdrawing my application for {{ role }}"),
        _(
            "Dear {{ contact }},\n\n"
            "Thank you for your time and consideration. I am writing to withdraw my "
            "application for the {{ role }} position at {{ company }}.\n\n"
            "I wish you every success with the search.\n\n"
            "Kind regards,\n{{ name }}"
        ),
    ),
    (
        MessagePurpose.AVAILABILITY,
        _("Replying with my availability"),
        _("My availability for {{ role }}"),
        _(
            "Dear {{ contact }},\n\n"
            "Thank you for getting in touch about the {{ role }} position. I can meet at "
            "any of these times:\n\n"
            "[Your times here.]\n\n"
            "Please tell me which suits you, or suggest another.\n\n"
            "Kind regards,\n{{ name }}"
        ),
    ),
)


def add_starters(user) -> list[MessageTemplate]:
    """Add the starter messages that ``user`` has no message of that purpose for.

    Written in the person's own language for documents, which is what the message is most
    likely to be sent in. A purpose they already have a message for is left alone, so
    pressing the button twice adds nothing.
    """
    have = set(MessageTemplate.objects.for_user(user).values_list("purpose", flat=True))
    profile = getattr(user, "profile", None)
    language = document_language(MessageTemplate(owner=user))
    added = []
    with languages.override(language):
        for purpose, name, subject, body in STARTERS:
            if purpose in have:
                continue
            added.append(
                MessageTemplate.objects.create(
                    owner=user,
                    purpose=purpose,
                    name=str(name),
                    subject=str(subject),
                    body=str(body),
                    language=getattr(profile, "language", "") or "",
                )
            )
    return added


def written_for(template: MessageTemplate, application=None) -> tuple[str, str]:
    """The template's subject and body with the application's details in them.

    Filled in the message's own language, so a date or a company's name in that language
    reads as the person writing it would write it. A placeholder with nothing behind it is
    left as the empty text it will be: the person edits the text before it goes, and sees
    the gap there.
    """
    with languages.override(document_language(template)):
        values = letter_values(template, application)
        subject = fill_placeholders(template.subject, values)
        body = fill_placeholders(template.body, values)
    return subject.strip(), body.strip()


def recipient_for(application, interview=None) -> str:
    """Who a message about this application goes to: one address, never a list.

    The person met at the interview, when it was one and they have an address; otherwise
    the application's contact; otherwise the company's general mailbox (jobs@, info@), which
    is the nearest a listing comes to an address of its own.
    """
    if interview is not None:
        for contact in interview.contacts.all():
            if contact.email:
                return contact.email
    if application.contact_id and application.contact.email:
        return application.contact.email
    return application.posting.company.email or ""


def starter_for(user, purpose: str) -> MessageTemplate | None:
    """The first of this person's messages for ``purpose``, or none."""
    if purpose not in MessagePurpose.values:
        return None
    return MessageTemplate.objects.for_user(user).filter(purpose=purpose).first()
