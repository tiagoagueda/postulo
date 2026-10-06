"""What somebody has already typed, offered back to them the next time (#261).

Adding an application asks for a company by name and the box is empty every time. Type
``Acme`` today and ``Acme Ltd`` next month and there are two employers, two sets of
postings, two rows in the companies table, and a funnel that quietly counts them apart.

`applications.services.get_or_create_company` matches on ``name_key``, which catches
the variation it was written for — *Acme*, *acme*, *ACME* — and nothing else. A list of
what this person has already recorded is the other half of that defence and the better
half, because it works **before** the record exists rather than reconciling two afterwards.

**Everything here is scoped to one person.** A suggestion list built from the instance is
the classic shape of an isolation leak: type ``a`` and learn every employer everybody on
the instance is applying to. `tests/test_suggestions.py` says so in a test rather than
leaving it to be remembered.

**Offering is not requiring.** These feed a `<datalist>`, which a browser shows as
suggestions beside a text box somebody may ignore entirely — and a company nobody has
recorded is exactly what a new application usually is. Nothing here validates anything.
"""

from __future__ import annotations

from django.db.models import Count

#: The most a datalist carries. A few hundred names is a few kilobytes, which is why this
#: is rendered into the page rather than fetched; past this it stops being sensible, and
#: the answer then is an endpoint rather than a bigger list. Ordered by how often each has
#: been used, so the cut takes the ones least likely to be typed again (#261).
AT_MOST = 300


def _by_use(queryset, field: str) -> list[str]:
    """The distinct values of ``field``, commonest first, blanks dropped."""
    rows = (
        queryset.exclude(**{field: ""})
        .values(field)
        .annotate(uses=Count(field))
        .order_by("-uses", field)[:AT_MOST]
    )
    return [row[field] for row in rows]


def _everyone(used: list[str], pool) -> list[str]:
    """The names with postings, then every other company of the pool by name."""
    known = set(used)
    return used + [
        name
        for name in pool.order_by("name").values_list("name", flat=True)
        if name and name not in known
    ]


def _after(first: list[str], rest: list[str], limit: int) -> list[str]:
    """``first``, then the others in their own order, each name once, at most ``limit``."""
    seen = set(first)
    return (first + [name for name in rest if name not in seen])[:limit]


def companies(user, *, including_career: bool = False, role: str = "") -> list[str]:
    """Every employer this person has recorded, commonest first.

    Without the companies only the career added (#683): a new application is not made
    to a former employer. The career form asks for them too. With a ``role`` (`jobs.roles`),
    the companies that play it come first, by name, and every other company follows below
    them: the role puts likely names first and leaves none out (#686).
    """
    from .models import Company, JobPosting

    counted = (
        JobPosting.objects.for_user(user)
        .values("company__name")
        .annotate(uses=Count("company__name"))
        .order_by("-uses", "company__name")[:AT_MOST]
    )
    names = [row["company__name"] for row in counted if row["company__name"]]
    if role:
        # Reordered after the cut is made, so the set offered is the same with or without a
        # role; only its order changes. A company past the cut is reached by typing it.
        pool = Company.objects.for_user(user)
        if not including_career:
            pool = pool.offered()
        first = list(pool.qualifying(role).order_by("name").values_list("name", flat=True))
        names = _after(first, _everyone(names, pool), AT_MOST)
        return names
    if len(names) < AT_MOST:
        # A company recorded with no posting against it yet is still one to offer: it is
        # there because somebody typed it, which is the whole signal this is built on.
        known = set(names)
        pool = Company.objects.for_user(user)
        if not including_career:
            pool = pool.offered()
        for name in pool.order_by("name").values_list("name", flat=True):
            if name and name not in known:
                names.append(name)
                known.add(name)
                if len(names) >= AT_MOST:
                    break
    return names


def schools(user) -> list[str]:
    """The person's companies for an education entry: the places of learning first (#685).

    Those whose industries include NACE division 85, by name, then every other company
    below them -- a typed box hides nothing, and no company carries that industry until
    somebody gives it one.
    """
    from . import roles
    from .models import Company

    first = list(
        Company.objects.for_user(user)
        .qualifying(roles.PLACE_OF_LEARNING)
        .order_by("name")
        .values_list("name", flat=True)[:AT_MOST]
    )
    taken = set(first)
    rest = [name for name in companies(user, including_career=True) if name not in taken]
    return (first + rest)[:AT_MOST]


def locations(user) -> list[str]:
    from .models import JobPosting

    return _by_use(JobPosting.objects.for_user(user), "location")


def sources(user) -> list[str]:
    """Where postings were found. A short list for most people, and typed every time."""
    from .models import JobPosting

    return _by_use(JobPosting.objects.for_user(user), "source")
