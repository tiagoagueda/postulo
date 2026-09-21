"""What somebody has already typed, offered back to them the next time (#261).

Adding an application asks for a company by name and the box is empty every time. Type
``Acme`` today and ``Acme Ltd`` next month and there are two employers, two sets of
postings, two rows in the companies table, and a funnel that quietly counts them apart.

`applications.services.get_or_create_company` matches on ``name__iexact``, which catches
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


def companies(user) -> list[str]:
    """Every employer this person has recorded, commonest first."""
    from .models import Company, JobPosting

    counted = (
        JobPosting.objects.for_user(user)
        .values("company__name")
        .annotate(uses=Count("company__name"))
        .order_by("-uses", "company__name")[:AT_MOST]
    )
    names = [row["company__name"] for row in counted if row["company__name"]]
    if len(names) < AT_MOST:
        # A company recorded with no posting against it yet is still one to offer: it is
        # there because somebody typed it, which is the whole signal this is built on.
        known = set(names)
        for name in Company.objects.for_user(user).order_by("name").values_list("name", flat=True):
            if name and name not in known:
                names.append(name)
                known.add(name)
                if len(names) >= AT_MOST:
                    break
    return names


def locations(user) -> list[str]:
    from .models import JobPosting

    return _by_use(JobPosting.objects.for_user(user), "location")


def sources(user) -> list[str]:
    """Where postings were found. A short list for most people, and typed every time."""
    from .models import JobPosting

    return _by_use(JobPosting.objects.for_user(user), "source")
