"""Help where the question is asked: what a card's question mark opens (#302).

A card whose help belongs to the card, and not to one of its fields, has a question mark in
its corner. Resting on it shows one sentence; pressing it opens a drawer holding the card's
full help; and with scripts off it is a link to a page of its own holding the same text.
This module is the list of what there is to open: a **topic** per card.

**The text is a template in the application**, under ``templates/help/``, translated through
the catalogues like every other string. Nothing is fetched from the wiki when a page is
drawn and nothing is bundled from it: the wiki is a separate repository, written in English
and not part of an installed instance, so help taken from it would be help nobody could
translate, and a self-hosted server calling somebody else's Forgejo to explain its own
form. The wiki's pages point at the topics instead.

**The drawer and the page draw the same template**, with the same context, so the two cannot
say different things. A topic that states a number the code enforces -- the size a picture
may be -- reads it from the code that enforces it, through ``context``, and never writes it
out: ``tests/test_help_topics.py`` holds the picture's to that.

A topic is written for the person filling the card in: what the card is for, what each
control does, what is refused and why, and what happens to what is there already. A card
with nothing more to say than its one sentence gets no question mark and no topic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Topic:
    """One card's help: where it lives, what it is called, and the one sentence."""

    #: What the address ends in: ``/help/telephone-numbers/``.
    slug: str
    #: The name of what it explains, as the card's own title says it.
    title: str
    #: The one sentence: what the question mark shows when it is rested on or focused, and
    #: with scripts off the sentence under the card's title.
    summary: str
    #: The template that holds the help itself, drawn by the drawer and by the page.
    template: str
    #: The page the card is on, for the way back when the address was opened on its own:
    #: a URL name, and what the link back to it says. A whole sentence, because a page's
    #: name pasted after "Back to" is right in English and in few other languages.
    home: str = "accounts:profile"
    back: str = _("Back to Your details")
    #: What the template reads besides the request: a function, called when it is drawn.
    context: Callable[[], dict] | None = None

    def variables(self) -> dict:
        return self.context() if self.context else {}


def _picture_limits() -> dict:
    """What an uploaded picture may be, from the code that refuses the rest."""
    from postulo.accounts import avatars

    return avatars.limits()


def _pasted_whole() -> dict:
    """Which registers' addresses can be pasted whole, from the schemes that lift an
    identifier out of one."""
    from postulo.accounts import identifiers

    return {"pasted": identifiers.pasted_whole()}


TOPICS: dict[str, Topic] = {
    topic.slug: topic
    for topic in (
        Topic(
            slug="your-picture",
            title=_("Your picture"),
            summary=_(
                "The picture that stands for you on these pages: one you upload, your "
                "Gravatar, or your initials."
            ),
            template="help/your_picture.html",
            context=_picture_limits,
        ),
        Topic(
            slug="your-name",
            title=_("Your name"),
            summary=_(
                "A form of address is written before your name, and your pronouns say how "
                "to refer to you. Both are optional, and nothing is assumed when they are "
                "blank."
            ),
            template="help/your_name.html",
        ),
        Topic(
            slug="contact-block",
            title=_("Contact block"),
            summary=_(
                "What your documents print under your name: a headline, where you are, and "
                "how to reach you."
            ),
            template="help/contact_block.html",
        ),
        Topic(
            slug="telephone-numbers",
            title=_("Telephone numbers"),
            summary=_(
                "A mobile, a desk line, a switchboard — as many as are worth keeping, with "
                "one of them marked as the one to use. Documents print that one."
            ),
            template="help/telephone_numbers.html",
        ),
        Topic(
            slug="links",
            title=_("Social profiles, code repositories and websites"),
            summary=_(
                "Your addresses on the web, a card for each sort, with one of each marked "
                "as the one to show."
            ),
            template="help/links.html",
        ),
        Topic(
            slug="postal-addresses",
            title=_("Postal addresses"),
            summary=_(
                "Wherever post reaches you, with one of them marked as the one to use. "
                "Documents never print a street: they print that one's town and country, "
                "unless “Location” says otherwise."
            ),
            template="help/postal_addresses.html",
        ),
        Topic(
            slug="identifiers",
            title=_("Identifiers"),
            summary=_("An ORCID, or another public id that says which researcher you are."),
            template="help/identifiers.html",
            context=_pasted_whole,
        ),
    )
}


def topic(slug: str) -> Topic:
    """The topic at this address. ``KeyError`` for one that does not exist."""
    return TOPICS[slug]
