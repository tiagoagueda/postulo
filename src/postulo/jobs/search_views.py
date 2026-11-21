"""*Search a job service* under Listings, and *Review* on an advert it found (#1253).

Modelled on *Look up* in a company register (#1021): nothing is asked on a GET, which draws the
form and nothing else; a POST of *Search* asks the chosen service for one page of adverts,
charged to the person's allowance, and draws them -- they are shown, never stored, and an
advert the person already holds says so (`jobs.known`); a POST of *Review* asks the service
again for that one advert by its number and makes a `Capture` from the answer, which goes to
the review every capture goes through, where it is saved as a listing, as an application, into
an existing listing's history, or discarded.

**Outside a transaction (#220).** Both POSTs wait on somebody else's server, up to fifteen
seconds; under `ATOMIC_REQUESTS` on SQLite that would hold the write lock for all of it. The
only row written is the capture, in a transaction of one statement, and the allowance is
counted before the request and left spent whatever the service does, as capture's is.

**What the search was is the person's.** It travels in the body of a POST, never in the page's
address, is never logged, and is not kept: *Next page* sends it again from hidden fields.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy
from django.views import View

from postulo.core import throttle

from . import listing_search
from .known import known
from .models import Capture

#: The *published in the last* choices, in days: any time, then four spans a person picks
#: between without counting.
SINCE_DAYS = (1, 3, 7, 30)


def offered(person) -> list:
    """The services ``person`` may search, or none while the feature is off for them."""
    return listing_search.offered_to(person)


def _country_name(code: str) -> str:
    from postulo.core.place_display import country_name

    return country_name(code)


def service_choices(services) -> list:
    """The services as a select's choices, grouped by the country their adverts are in.

    A service for several countries, or none, is under *Several countries*; a group with one
    service still says its country, which is what the person chooses by.
    """
    groups: dict[str, list] = {}
    for search in services:
        if len(search.countries) == 1:
            heading = _country_name(search.countries[0])
        else:
            heading = str(_("Several countries"))
        groups.setdefault(heading, []).append((search.key, str(search.label)))
    return [(heading, groups[heading]) for heading in sorted(groups, key=str.casefold)]


class ListingSearchForm(forms.Form):
    """What to search for, and where. Every criterion a service may not honour is said so
    under the results, never silently dropped."""

    service = forms.ChoiceField(label=_("Job service"))
    words = forms.CharField(
        label=_("Words"),
        required=False,
        max_length=listing_search.MAX_WORDS,
        help_text=_("A job title, a skill or an employer, as you would type it on the service."),
    )
    place = forms.CharField(
        label=_("Place"),
        required=False,
        max_length=listing_search.MAX_PLACE,
        help_text=_("A town or a region."),
    )
    remote = forms.BooleanField(label=_("Remote work only"), required=False)
    since = forms.TypedChoiceField(
        label=_("Published"),
        required=False,
        coerce=int,
        empty_value=None,
    )
    page = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=listing_search.MAX_PAGE,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, services=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.services = {search.key: search for search in services}
        self.fields["service"].choices = service_choices(services)
        self.fields["since"].choices = [
            ("", _("At any time")),
            *(
                (
                    days,
                    ngettext_lazy("In the last day", "In the last %(count)d days", days)
                    % {"count": days},
                )
                for days in SINCE_DAYS
            ),
        ]
        honoured = set().union(*(search.supports for search in services)) if services else set()
        # A criterion no offered service honours is not drawn at all.
        for name, field_name in (
            (listing_search.WORDS, "words"),
            (listing_search.PLACE, "place"),
            (listing_search.REMOTE, "remote"),
            (listing_search.SINCE, "since"),
        ):
            if name not in honoured:
                del self.fields[field_name]

    def clean(self):
        cleaned = super().clean()
        if not self.errors and not (
            cleaned.get("words") or cleaned.get("place") or cleaned.get("remote")
        ):
            raise forms.ValidationError(_("Say what to search for: some words, or a place."))
        return cleaned

    @property
    def chosen(self):
        return self.services.get(self.cleaned_data.get("service", ""))

    def criteria(self) -> listing_search.Criteria:
        data = self.cleaned_data
        return listing_search.Criteria(
            words=data.get("words") or "",
            place=data.get("place") or "",
            remote=bool(data.get("remote")),
            since_days=data.get("since"),
            page=data.get("page") or 1,
        )


#: What each criterion is called where the results say a service did not honour it.
CRITERIA_LABELS = {
    listing_search.WORDS: _("the words"),
    listing_search.PLACE: _("the place"),
    listing_search.REMOTE: _("remote work only"),
    listing_search.SINCE: _("when it was published"),
}


def marked(person, hits) -> list[dict]:
    """Each hit, and what the person already holds of it: a listing at its address, or a
    capture waiting, or a listing of the same title at the same company (#178)."""
    rows = []
    for hit in hits:
        posting = hit.posting
        seen = known(person, posting.url, posting.title, posting.company_name)
        rows.append({"key": hit.key, "posting": posting, "known": seen})
    return rows


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ListingSearchView(LoginRequiredMixin, View):
    """The search page: the form on a GET, the results on *Search*, a capture on *Review*."""

    template_name = "jobs/listing_search.html"

    def _services(self, request):
        services = offered(request.user)
        if not services:
            raise Http404
        return services

    def _render(self, request, form, services, **extra) -> HttpResponse:
        chosen = form.chosen if form.is_bound and not form.errors else None
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "services": services,
                "service": chosen or (services[0] if len(services) == 1 else None),
                **extra,
            },
        )

    def get(self, request: HttpRequest) -> HttpResponse:
        services = self._services(request)
        return self._render(request, ListingSearchForm(services=services), services)

    def post(self, request: HttpRequest) -> HttpResponse:
        services = self._services(request)
        if "review" in request.POST:
            return self._review(request, services)
        form = ListingSearchForm(request.POST, services=services)
        if not form.is_valid():
            return self._render(request, form, services)
        service = form.chosen
        criteria = form.criteria()
        try:
            throttle.listing_search(request.user)
            results = listing_search.search(service, criteria)
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
            return self._render(request, form, services)
        except listing_search.SearchRefused as refused:
            messages.error(request, str(refused))
            return self._render(request, form, services)
        honoured = listing_search.honoured(service, criteria)
        return self._render(
            request,
            form,
            services,
            results=results,
            rows=marked(request.user, results.hits),
            page=honoured.page,
            next_page=honoured.page + 1 if results.more else None,
            left_out=[CRITERIA_LABELS[name] for name in listing_search.left_out(service, criteria)],
            language=service.languages[0] if service.languages else "",
        )

    def _review(self, request, services) -> HttpResponse:
        """Ask the service for the chosen advert again, and make a capture of its answer.

        The service is looked up among those offered to the person and the advert's number
        held to the shape a service's own number has; nothing else the form sent is read.
        """
        service = next(
            (search for search in services if search.key == request.POST.get("service", "")),
            None,
        )
        key = request.POST.get("advert", "")
        if service is None:
            messages.error(request, _("That job service is not offered here."))
            return redirect("listings:search")
        try:
            throttle.listing_search(request.user)
            posting = listing_search.advert(service, key)
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
            return redirect("listings:search")
        except listing_search.SearchRefused as refused:
            messages.error(request, str(refused))
            return redirect("listings:search")
        with transaction.atomic():
            capture = Capture.objects.create(
                owner=request.user,
                url=posting.url[:500],
                source_name=service.key,
                origin="search",
                data=posting.model_dump(mode="json"),
            )
        messages.success(request, _("Asked for the advert. Check it before it becomes a listing."))
        return redirect(capture.get_absolute_url())
