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

    from . import remembered
    from .models import Capture

    url = errand.payload.get("url", "")
    supplied = errand.payload.get("html", "")

    if supplied:
        # A pasted page is carried here in the errand's own row, which is kept for a week
        # so that the page watching it has something to read. That made the queue a place
        # where a page's source was kept whatever anybody had decided about keeping
        # sources, so it is taken out of the row as soon as it has been read (#256): what
        # is kept of a page is what `pages` keeps, and nothing else.
        from postulo.core.models import Errand

        Errand.objects.filter(pk=errand.pk).update(payload={**errand.payload, "html": ""})

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

    # Read with the places the owner's own corrections showed on this site, where there are
    # any (#267): below the site's own statements, above what the page says about itself.
    result = remembered.read_page(errand.owner, page_url, page_html)
    if result is None:
        raise Refused(_("Nothing resembling a job posting was found on that page."))

    data, source, handed = result
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

    # What was parsed, kept beside what it was read as, where the instance and the person
    # have both said so (#256). After the capture and never instead of it: whatever goes
    # wrong here is a sentence on the page, and the capture is already made.
    from . import pages

    _kept, note = pages.keep_source_quietly(capture, page_html)
    # And what its review will learn from: which remembered places filled what, and -- only
    # where the source was not just kept -- the page's places, as digests (#267).
    remembered.after_capture(capture, handed, page_html)
    message = str(_("Read it. Check what was found before it becomes a listing."))
    return {
        "message": f"{message} {note}".strip(),
        "url": reverse("jobs:capture_review", args=[capture.pk]),
        "capture_id": capture.pk,
    }


@handler("page_rendering", working=_("Drawing the page"))
def draw_a_page(errand) -> dict:
    """Draw a capture's kept source as a PDF, with nothing of it allowed to run (#256).

    Slow for the reason a CV is: it is a renderer, started for one document. It is asked
    for with a button rather than done at every capture, because what it draws is the kept
    source and nothing else -- so it is the same whenever it is drawn, and nobody waits for
    one they did not want.
    """
    from django.urls import reverse

    from . import pages
    from .models import Capture

    capture = Capture.objects.filter(
        pk=errand.payload.get("capture_id"), owner=errand.owner
    ).first()
    if capture is None:
        raise Refused(_("That capture is no longer here."))
    try:
        pages.draw_rendering(capture)
    except pages.NotKept as refusal:
        # An explanation, as a refused fetch is: the renderer is not installed, the page
        # took too long, the account has no room left.
        raise Refused(str(refusal)) from refusal
    return {
        "message": str(_("Drawn from the kept source, with scripts and the network off.")),
        "url": reverse("jobs:capture_page", args=[capture.pk]),
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
