"""A card's help at an address of its own (#302).

A card's question mark opens its help in a drawer, which a script does. With scripts off
the same question mark is a link, and this is where it leads: the same topic, drawn by the
same template, as a page that can be read on its own, bookmarked, and left by a link back to
the card it was asked from.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest
from django.shortcuts import render
from django.urls import reverse

from . import help as help_topics
from .redirects import safe_next


@login_required
def topic_page(request: HttpRequest, slug: str):
    """One topic. Signed in, like the cards it explains; an unknown one is a 404.

    ``next`` is where the question mark was, card and all, so the way back lands on the
    card. It is a value from the request, so it goes through `safe_next`, and then it is
    followed only where it is the page the topic's cards are on, with a card's anchor or
    none: the way back is back to *those* cards, and a link that said "Back to Your
    details" and led to signing out, or to deleting the account, would be a trap with a
    kind name (#302). Anything else is the page itself.
    """
    try:
        topic = help_topics.topic(slug)
    except KeyError:
        raise Http404(f"No help topic is called {slug!r}") from None
    home = reverse(topic.home)
    asked = urlsplit(safe_next(request, home))
    on_the_page = not asked.scheme and not asked.netloc and not asked.query
    back = asked.geturl() if on_the_page and asked.path == home else home
    return render(request, "help/topic.html", {"topic": topic, "back": back})
