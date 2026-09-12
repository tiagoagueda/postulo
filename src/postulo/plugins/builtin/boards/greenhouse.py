"""Greenhouse.

Read off a live board, September 2026. Greenhouse hosts a very large share of the adverts
people actually apply through and publishes **no structured data on a job page**:

    <a href="https://boards.greenhouse.io/gitlab" class="logo">
      <img src="..." alt="GitLab Logo">
    <div class="job__header">
      <div class="job__title">
        <h1 class="section-header ...">AI Engineer</h1>
        <div class="job__location"><svg .../>Remote, Bangalore</div>
    <div class="job__description body"> ... the advert ...

The company is only ever in two places: the logo's alt text, and the page title as "Job
Application for <job> at <company>". The alt text is taken, with a trailing "Logo" removed
-- that is Greenhouse's own convention for it, and unlike the page title it is not a
sentence that changes with the board's language.

`job__location` holds an icon before its text, which `text_of` drops with every other
inline graphic, so the place comes out clean.

Not read, deliberately: dates, salary and employment type, none of which a Greenhouse job
page states anywhere a reader could find them.
"""

from __future__ import annotations

import re

from ..htmlutil import Element, find, first_by_class, text_of

HOSTS = ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io")

#: Greenhouse writes the logo's alt text as "<Company> Logo".
LOGO_SUFFIX = re.compile(r"\s*logo\s*$", re.IGNORECASE)


def read(body: Element, url: str) -> dict:
    """What a Greenhouse advert states about itself, and nothing more."""
    found: dict = {}

    # `job__title` wraps the heading *and* `job__location`, so take the heading itself or
    # the title comes out as "AI Engineer\n\nRemote, Bangalore".
    header = first_by_class(body, "job__title")
    if header is not None:
        headings = find(header, "h1")
        found["title"] = text_of(headings[0] if headings else header)

    place = first_by_class(body, "job__location")
    if place is not None:
        found["location"] = text_of(place)

    description = first_by_class(body, "job__description")
    if description is not None:
        found["description"] = text_of(description)

    logo = first_by_class(body, "logo")
    if logo is not None:
        for image in logo.iter():
            alt = image.get("alt").strip() if image.tag == "img" else ""
            name = LOGO_SUFFIX.sub("", alt).strip() if alt else ""
            if name:
                found["company_name"] = name
                break

    return found
