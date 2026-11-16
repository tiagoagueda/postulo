"""What the suggestion boxes of the jobs forms offer (#1251).

Registered with `core.suggest`, which names the contract. Each answers from what this person
recorded -- the account is the only thing read -- and the job title adds what the enabled
suggestion sources offer (the ESCO unit groups) after them, in the language read, which
belong to nobody.
"""

from __future__ import annotations

from itertools import chain

from postulo.core import suggest, suggestion_sources

from . import recall, roles


def job_title(user, typed: str):
    offered = suggestion_sources.offered("job_title", user=user)
    return chain(recall.titles(user), (text for text, _group in offered))


def city(user, typed: str):
    """The towns this person kept, then the cities of every enabled places source (#1252).

    A source is asked what was typed, since it searches a table; with none enabled the box
    suggests only what the person typed before, and is otherwise a plain box.
    """
    return chain(
        recall.cities(user),
        (text for text, _group in suggestion_sources.offered("city", user=user, typed=typed)),
    )


suggest.register("company", lambda user, typed: recall.companies(user), fills=recall.company_facts)
suggest.register(
    "employer",
    lambda user, typed: recall.companies(user, including_career=True),
    fills=recall.company_facts,
)
suggest.register("job-title", job_title, fills=recall.title_facts)
suggest.register("location", lambda user, typed: recall.locations(user))
suggest.register("city", city)
suggest.register("source", lambda user, typed: recall.sources(user))
suggest.register("contact-role", lambda user, typed: recall.contact_roles(user))
suggest.register("school", lambda user, typed: recall.schools(user))
suggest.register(
    "awarding-body",
    lambda user, typed: recall.companies(user, including_career=True, role=roles.AWARDING_BODY),
)
suggest.register(
    "place-of-learning",
    lambda user, typed: recall.companies(user, including_career=True, role=roles.PLACE_OF_LEARNING),
)
suggest.register(
    "membership-organisation",
    lambda user, typed: recall.companies(
        user, including_career=True, role=roles.MEMBERSHIP_ORGANISATION
    ),
)
suggest.register("contact-name", lambda user, typed: recall.contact_names(user))


def municipality(user, typed: str):
    """The towns of the addresses this person recorded, then the cities of every enabled
    places source (#1252), so the address row has both and choosing a recorded one fills its
    postcode and country (#1251)."""
    return chain(
        recall.municipalities(user),
        (text for text, _group in suggestion_sources.offered("city", user=user, typed=typed)),
    )


suggest.register("municipality", municipality, fills=recall.municipality_facts)
