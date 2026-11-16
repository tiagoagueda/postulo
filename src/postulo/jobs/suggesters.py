"""What the suggestion boxes of the jobs forms offer (#1251).

Registered with `core.suggest`, which names the contract. Each answers from what this person
recorded -- the account is the only thing read -- and the job title adds what the enabled
suggestion sources offer (the ESCO unit groups) after them, in the language read, which
belong to nobody.
"""

from __future__ import annotations

from itertools import chain

from postulo.core import suggest, suggestion_sources

from . import recall


def job_title(user, typed: str):
    offered = suggestion_sources.offered("job_title", user=user)
    return chain(recall.titles(user), (text for text, _group in offered))


suggest.register("company", lambda user, typed: recall.companies(user))
suggest.register("employer", lambda user, typed: recall.companies(user, including_career=True))
suggest.register("job-title", job_title)
suggest.register("location", lambda user, typed: recall.locations(user))
suggest.register("source", lambda user, typed: recall.sources(user))
suggest.register("contact-role", lambda user, typed: recall.contact_roles(user))
