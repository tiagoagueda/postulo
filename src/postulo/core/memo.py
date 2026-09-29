"""What one request remembers, and the two calls that make it forget (#231).

Two memos, each for a question asked many times a request with the same answer every time:
the instance's policy row, which `core.site` reads, and the plugin decisions, which
`plugins.policy` makes. They live here, in a module that imports nothing of Postulo's,
because the rows they memoise have to be able to drop them when they are saved -- and a model
importing the code that reads it back was a cycle held open by an import inside a function
(#248). `core.site` and `plugins.policy` still hand out the forgetting under their old names.
"""

from __future__ import annotations

from asgiref.local import Local

#: The policy row for the request in flight. One row, read by a dozen little questions --
#: is registration open, what language does this instance default to, what is it called --
#: and each of them used to be its own `SELECT`. The middleware asks twice, the `ui` context
#: processor three times, `is_empty()` once more, and an htmx fragment pays the same as a
#: page: about five queries for one row, on every request Postulo answers (#231).
#:
#: A `Local` rather than the cache, because Postulo's default cache is a table in the same
#: database -- caching a query in a place that costs a query is not a saving. It is cleared
#: at the start of every request and whenever the row is saved, so the longest anything can
#: be stale is one request that was already in flight.
site = Local()

#: One request's plugin decisions, keyed on the record's stamp so that installing or removing
#: a plugin throws them away by itself (#231). `policy.decide` reads the record from disk and
#: asks the policy table, and a company's page asks it six times -- once per mark it draws --
#: for six identical answers. Cleared at every request boundary and whenever a policy row is
#: written.
decisions = Local()


def forget_current() -> None:
    """Drop the memoised row.

    Called at the start of every request, by `SiteSettings.save`, and at the top of each
    scheduler pass -- the three places where "the row may have changed since I last looked"
    becomes true. A worker thread lives for thousands of requests and a scheduler loop lives
    for ever; neither may hold yesterday's mail settings.
    """
    try:
        del site.row
    except AttributeError:
        pass


def forget_decisions() -> None:
    """Drop this thread's memoised plugin decisions. See `decisions`."""
    try:
        del decisions.answers
    except AttributeError:
        pass
