"""LinkedIn.

Read off a live advert, September 2026. LinkedIn publishes **no structured data at all** on
a job page -- no JSON-LD, no microdata -- and states everything worth having in class names:

    <h1 class="... topcard__title">Medical Physicist (EU- Remote)</h1>
    <h4 class="top-card-layout__second-subline">
      <div class="topcard__flavor-row">
        <span class="topcard__flavor">
          <a class="topcard__org-name-link ...">Spectrum Dynamics Medical</a>
        </span>
        <span class="topcard__flavor topcard__flavor--bullet">France</span>
      </div>
      <div class="topcard__flavor-row">
        <span class="posted-time-ago__text topcard__flavor--metadata">2 months ago</span>
        <span class="num-applicants__caption topcard__flavor--metadata">188 applicants</span>

``topcard__flavor--bullet`` is on the place *and* on the applicant count, so the count is
excluded by the ``--metadata`` it also carries. Without that the location reads "188
applicants" on every advert, which is the kind of quiet wrongness a recipe has to be written
against rather than around.

Not read, deliberately:

``posted_at``
    "2 months ago" is a relative phrase in the reader's language. Turning it into a date
    means parsing 39 languages' worth of "month" and guessing at what it was relative to.
    A date somebody can see on the page and type is better than a date Postulo invented.

``salary``
    Shown as free text in the poster's language when it is shown at all, and nothing maps
    onto an amount and a period without guessing at both.
"""

from __future__ import annotations

from ..htmlutil import Element, by_class, first_by_class, text_of
from ..vocabulary import employment_type

#: Every LinkedIn country host is the same page: fr.linkedin.com, ch.linkedin.com, ...
HOSTS = ("linkedin.com",)


def read(body: Element, url: str) -> dict:
    """What a LinkedIn advert states about itself, and nothing more."""
    found: dict = {}

    title = first_by_class(body, "topcard__title")
    if title is not None:
        found["title"] = text_of(title)

    company = first_by_class(body, "topcard__org-name-link")
    if company is not None:
        found["company_name"] = text_of(company)

    # The place, and not the applicant count that shares its class.
    place = first_by_class(body, "topcard__flavor--bullet", without="topcard__flavor--metadata")
    if place is not None:
        found["location"] = text_of(place)

    # The advert, without the topcard above it or the link farms below.
    description = first_by_class(body, "description__text") or first_by_class(
        body, "show-more-less-html"
    )
    if description is not None:
        found["description"] = text_of(description)

    # The criteria list holds the employment type, under a heading in the reader's
    # language. Rather than keep a table of that heading in 39 languages, every value is
    # offered to the vocabulary and the one it recognises wins: "Full-time" is read,
    # "Mid-Senior level" and "Health Care Provider" are not, and a page in a language whose
    # words Postulo does not know leaves the field for a person. Nothing is guessed either
    # way, and the field is left empty rather than filled from the wrong row.
    for criterion in by_class(body, "description__job-criteria-text"):
        known = employment_type(text_of(criterion))
        if known:
            found["employment_type"] = known
            break

    return found
