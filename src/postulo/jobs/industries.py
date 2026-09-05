"""A starter vocabulary of industries, offered as suggestions and never as a closed list.

Broad fields only. A person who works in a niche types the niche, and it joins their own
vocabulary like anything else. These are translated with the interface so the suggestions
read naturally in every language Postulo speaks.
"""

from django.utils.translation import gettext_lazy as _

STARTER_INDUSTRIES = (
    _("Software"),
    _("Information technology"),
    _("Telecommunications"),
    _("Finance"),
    _("Banking"),
    _("Insurance"),
    _("Consulting"),
    _("Health"),
    _("Pharmaceuticals"),
    _("Biotechnology"),
    _("Education"),
    _("Research"),
    _("Public sector"),
    _("Non-profit"),
    _("Energy"),
    _("Utilities"),
    _("Manufacturing"),
    _("Engineering"),
    _("Construction"),
    _("Automotive"),
    _("Aerospace"),
    _("Transport and logistics"),
    _("Retail"),
    _("E-commerce"),
    _("Hospitality and tourism"),
    _("Food and agriculture"),
    _("Media"),
    _("Advertising and marketing"),
    _("Gaming"),
    _("Real estate"),
    _("Legal"),
    _("Defence"),
)


def suggestions(exclude=()) -> list[str]:
    """The starter names, minus any the person already has."""
    taken = {str(name).casefold() for name in exclude}
    return [str(name) for name in STARTER_INDUSTRIES if str(name).casefold() not in taken]
