"""The slow work this app asks for: fetching somebody else's page, and finding a logo (#247).

Both dial an address the server does not control, and both were doing it inside the request
that asked. `fetching` allows ten seconds for a page and five more for `robots.txt`; *Find
logo* reads a company's site and then tries up to six images, one round trip each. On a small
machine those were seconds a gunicorn worker could do nothing else with.

What stayed in the view is as important as what moved. The **rate limit** is counted there,
before an errand exists, because an allowance spent making the server fetch a page has been
spent whatever happens next -- counting it in the worker would let one permit queue a
thousand fetches. The **duplicate check** stayed too: it is a question asked of the person,
and a question asked after the work is a question asked too late.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.core.errands import Refused, handler


@handler("capture", working=_("Reading the page"))
def fetch_a_capture(errand) -> dict:
    """Fetch a posting's page, read it, and leave a capture waiting for review."""
    from django.db import transaction
    from django.urls import reverse

    from postulo.plugins.base import CaptureError
    from postulo.plugins.fetching import fetch_page
    from postulo.plugins.registry import parse_page

    from .models import Capture

    url = errand.payload.get("url", "")
    supplied = errand.payload.get("html", "")

    if supplied:
        # Nothing is fetched: the page came from a browser that was already allowed to see
        # it. Still an errand, because the parse is work somebody asked for, and because one
        # path through this is easier to trust than two.
        page_url, page_html = url, supplied
    else:
        try:
            fetched = fetch_page(url)
        except CaptureError as exc:
            # Refusals here are explanations -- that site says no, that address is private,
            # that page was too big -- and the person reads them exactly as they would have
            # read them from the form.
            raise Refused(str(exc)) from exc
        page_url, page_html = fetched.url, fetched.html

    result = parse_page(page_url, page_html)
    if result is None:
        raise Refused(_("Nothing resembling a job posting was found on that page."))

    data, source = result
    # A transaction of a single statement, as it was in the view: nothing slow is inside
    # it, which is the rule #220 set for every writer on the SQLite file.
    with transaction.atomic():
        capture = Capture.objects.create(
            owner=errand.owner,
            url=page_url[:500],
            source_name=source.name,
            source_version=getattr(source, "version", ""),
            origin="web",
            data=data.model_dump(mode="json"),
        )
    return {
        "message": str(_("Read it. Check what was found before it becomes a listing.")),
        "url": reverse("jobs:capture_review", args=[capture.pk]),
        "capture_id": capture.pk,
    }


@handler("logo", working=_("Looking for a logo"))
def find_a_logo(errand) -> dict:
    """*Find logo* and *Refresh*: read the company's site, try the images on it."""
    from . import logos
    from .models import Company

    company = Company.objects.filter(pk=errand.payload.get("company_id")).first()
    if company is None or company.owner_id != errand.owner_id:
        # Deleted while it waited, which is a thing that can now happen. Nothing to put a
        # logo on and nobody wronged: said plainly rather than raised.
        raise Refused(_("That company is no longer here."))

    action = errand.payload.get("action", "")
    try:
        if action == "website":
            found = logos.find_on_website(company)
            message = _("Found a logo at %(url)s.") % {"url": found}
        elif action == "refresh":
            if not company.logo_source_url:
                raise Refused(_("There is no address to fetch it from again."))
            logos.from_url(company, company.logo_source_url)
            message = _("Fetched again.")
        else:
            raise Refused(_("Postulo does not know how to do that to a logo."))
    except logos.UnusableLogo as error:
        raise Refused(str(error)) from error

    return {"message": str(message), "url": company.get_absolute_url()}
