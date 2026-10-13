"""Views for companies, contacts and postings."""

from __future__ import annotations

from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from postulo.core import deletions, identifier_order, slugs, tables, throttle
from postulo.core.cells import EditableCellView
from postulo.core.files import serve_stored_picture
from postulo.core.mixins import (
    ConfirmDeleteMixin,
    GdprNoticeMixin,
    MessagingHandlesMixin,
    OwnedObjectMixin,
    OwnerFormMixin,
    PageOrFragmentMixin,
    PhoneNumbersMixin,
    PostalAddressesMixin,
    WebLinksMixin,
)
from postulo.core.params import as_pk
from postulo.core.redirects import safe_next
from postulo.core.search import clean_query, company_match

from . import (
    duplicates,
    identifier_removals,
    identifiers,
    industries,
    logos,
    mapping,
    merging,
    outline,
    tiles,
)
from .forms import CompanyForm, CompanyIdentifierFormSet, ContactForm, IndustryForm, JobPostingForm
from .models import (
    Company,
    CompanyIdentifier,
    CompanyLogo,
    Contact,
    DiscardReason,
    Industry,
    JobPosting,
)
from .tables import CompaniesTable


class UserFormKwargsMixin:
    """Hand the signed-in user to the form, so it can scope its own choices."""

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


# ------------------------------------------------------------------- companies


class CompanyListView(PageOrFragmentMixin, OwnedObjectMixin, ListView):
    """The table of employers: sortable, narrowable, and laid out as the person likes."""

    model = Company
    template_name = "jobs/company_list.html"
    context_object_name = "companies"

    @cached_property
    def table(self) -> CompaniesTable:
        return CompaniesTable(
            self.request, tables.settings_for(self.request.user, CompaniesTable.name)
        )

    def get_paginate_by(self, queryset) -> int:
        return self.table.page_size

    def get(self, request, *args, **kwargs):
        # A bare address opens as the person's default view, when they have kept one (#259).
        # Only a bare one: anything with a parameter is a question they asked. Not for an
        # htmx swap either -- a swap is the page updating itself, not somebody arriving.
        opening = self.table.opening_url
        if opening and not getattr(request, "htmx", None):
            from django.shortcuts import redirect

            return redirect(opening)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        # The table's ordering always ends in the key, so pagination over the aggregated
        # rows never repeats or skips one between pages.
        queryset = (
            super()
            .get_queryset()
            .with_table_data()
            .select_related("parent")
            .prefetch_related("industries", "identifiers", "postal_addresses")
        )
        # Through `clean_query`, as the search over everything reads its own: the words
        # with their spaces tidied, and no more than two hundred characters of them. An
        # address is not a form, anything can be typed into it, and SQLite refuses a
        # pattern of sixty thousand characters with an error, which was a 500 (#313).
        search = clean_query(self.request.GET.get("q", ""))
        if search:
            queryset = queryset.filter(company_match(search))
        queryset = self._within_group(queryset)
        # The companies only the career added are not in the table until a filter asks (#683).
        if not self.table.given("from_career"):
            queryset = queryset.filter(from_career=False)
        queryset = self.table.apply(queryset)
        # The search matches industries and identifiers through `Exists`, so it cannot
        # repeat a company. Only the Industries column joins a many-valued relation, where
        # a company in two matching industries is still one row; the plain table skips the
        # de-duplication, which made every count and page dearer (#552).
        if self.table.given("industry") or self.table.given("sector"):
            queryset = queryset.distinct()
        return queryset

    def _within_group(self, queryset):
        """Narrow to one ownership tree, when `?group=` names a company in one.

        A walk rather than a join: an ownership chain is arbitrarily deep and the depth cap
        is ten, so expressing "anywhere in this group" as a lookup would be ten outer joins
        on every row of every page. Two small queries instead, and the answer is exact.

        An unknown name narrows to nothing rather than to everything, because a filter that
        silently does not apply is worse than one that shows an empty page.
        """
        from . import structure

        wanted = self.request.GET.get("group", "").strip()
        if not wanted or not structure.structure_allowed(self.request.user):
            return queryset
        tops = Company.objects.for_user(self.request.user).filter(name_key=slugs.name_key(wanted))
        members: set[int] = set()
        for top in tops.select_related("parent"):
            members.update(member.pk for member in top.group_members())
        return queryset.filter(pk__in=members)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["search"] = self.request.GET.get("q", "")
        context["table"] = self.table
        context["page_sizes"] = tables.PAGE_SIZES
        # The bar is offered only where there is something to apply: no fields of activity
        # means no additive action, and an action bar with an empty select is a promise the
        # page cannot keep (#134).
        context["bulk_industries"] = Industry.objects.for_user(self.request.user)
        context["group"] = self.request.GET.get("group", "").strip()
        context["map_offered"] = mapping.map_offered(self.request.user)
        return context


def pinned_addresses(person) -> list[dict]:
    """Every address the map draws for ``person``, as text first (#702).

    A company's addresses, every one (a company only the career added waits until it has a
    posting, #683); a contact's and the person's own only where they asked for it, because a
    home is somewhere somebody can be found. Each says whose it is and what kind, and where:
    its town and country, never its street. A point placed while the map was switched off
    for the words it has now is worked out here, without writing anything.
    """
    from django.contrib.contenttypes.models import ContentType

    from postulo.accounts.models import Profile
    from postulo.core import phones
    from postulo.core.models import PostalAddress

    from . import placing
    from .models import Contact

    types = ContentType.objects.get_for_models(Company, Contact, Profile)
    company_type, contact_type, profile_type = (types[m].pk for m in (Company, Contact, Profile))
    rows = PostalAddress.objects.for_user(person).filter(
        Q(content_type_id=company_type)
        | Q(content_type_id__in=[contact_type, profile_type], on_map=True)
    )
    companies = {
        company.pk: company
        for company in Company.objects.for_user(person).filter(from_career=False)
    }
    contacts = {contact.pk: contact for contact in Contact.objects.for_user(person)}
    found = []
    for row in rows.order_by("municipality", "pk"):
        if row.content_type_id == company_type:
            company = companies.get(row.object_id)
            if company is None:
                continue
            name, url, is_company = (
                company.name,
                reverse("jobs:company_detail", args=[company.pk]),
                True,
            )
        elif row.content_type_id == contact_type:
            contact = contacts.get(row.object_id)
            if contact is None:
                continue
            name, url, is_company = (
                contact.name,
                reverse("jobs:contact_update", args=[contact.pk]),
                False,
            )
        else:
            name, url, is_company = str(_("You")), reverse("accounts:profile"), False
        lat, lon = row.latitude, row.longitude
        if row.placed_by not in placing.KEPT and row.place_key() != row.placed_from:
            placed = places_data_place(row)
            lat, lon = (placed["lat"], placed["lon"]) if placed else (None, None)
        place = ", ".join(
            part for part in (row.municipality.strip(), phones.country_name(row.country)) if part
        ) or str(_("No town given"))
        what = row.label if row.kind == PostalAddress.Kind.OTHER else row.get_kind_display()
        found.append(
            {
                "place": place,
                "name": name,
                "url": url,
                "what": what,
                "company": is_company,
                "key": f"{row.content_type_id}:{row.object_id}",
                "primary": row.is_primary,
                "lat": lat,
                "lon": lon,
            }
        )
    return found


def places_data_place(row) -> dict | None:
    from . import places

    answer = places.place(row.postcode, row.municipality, row.country)
    return answer[1] if answer else None


def wikidata_offered(person) -> bool:
    """Whether *Fetch from Wikidata* is offered to ``person``: the feature's answer (#673)."""
    from postulo.plugins.policy import decide
    from postulo.plugins.wikidata import WIKIDATA

    return bool(decide(WIKIDATA, person).on)


def _primary_address(company):
    rows = list(company.postal_addresses.all())
    return next((row for row in rows if row.is_primary), rows[0] if rows else None)


class CompanyWikidataView(LoginRequiredMixin, View):
    """*Fetch from Wikidata*, what came back, and keeping it (#673).

    A POST with nothing more fetches: one request for the company's own item, charged to the
    person's allowance, and the answer is held in their session, not stored. The GET shows
    it. A POST of ``keep`` stores it: each name in its language, replacing a typed one there,
    and a name an earlier fetch gave that this one did not is taken off.
    """

    template_name = "jobs/company_wikidata.html"

    def _company(self, request, pk: int) -> Company:
        if not wikidata_offered(request.user):
            raise Http404
        return get_object_or_404(
            Company.objects.for_user(request.user).prefetch_related(
                "identifiers", "names", "postal_addresses"
            ),
            pk=pk,
        )

    @staticmethod
    def _held_key(company) -> str:
        return f"wikidata-{company.pk}"

    def get(self, request, pk: int):
        from postulo.core import languages

        company = self._company(request, pk)
        held = request.session.get(self._held_key(company))
        if not held:
            return redirect("jobs:company_detail", pk=company.pk)
        names = sorted(
            (
                (languages.native_name(tag, tag), tag, name)
                for tag, name in (held.get("names") or {}).items()
            ),
            key=lambda row: row[0].casefold(),
        )
        address = _primary_address(company)
        return render(
            request,
            self.template_name,
            {
                "company": company,
                "held": held,
                "names": names,
                # Where the point would go, and whether a hand-placed one is in its way (#701).
                "address": address,
                "by_hand": address is not None and address.placed_by == address.Placed.MANUAL,
            },
        )

    def post(self, request, pk: int):
        from postulo.core import throttle

        from . import wikidata

        company = self._company(request, pk)
        key = self._held_key(company)
        if request.POST.get("keep"):
            held = request.session.pop(key, None)
            if held:
                self._keep(company, held)
                messages.success(request, _("Kept what Wikidata said."))
            return redirect("jobs:company_detail", pk=company.pk)
        qid = company.wikidata_id()
        if not qid:
            messages.error(request, _("Give the company its Wikidata identifier first."))
            return redirect("jobs:company_detail", pk=company.pk)
        try:
            throttle.wikidata(request.user)
            item = wikidata.fetch(qid)
        except throttle.TooOften as too_often:
            messages.error(request, str(too_often))
            return redirect("jobs:company_detail", pk=company.pk)
        except wikidata.WikidataRefused as refused:
            messages.error(request, str(refused))
            return redirect("jobs:company_detail", pk=company.pk)
        request.session[key] = {
            "qid": item.qid,
            "names": item.names,
            "point": list(item.point) if item.point else None,
            "at": timezone.now().isoformat(),
        }
        return redirect("jobs:company_wikidata", pk=company.pk)

    def _keep(self, company, held) -> None:
        from .models import CompanyName

        now = timezone.now()
        names = {tag: name for tag, name in (held.get("names") or {}).items() if tag and name}
        with transaction.atomic():
            company.names.filter(source=CompanyName.Source.WIKIDATA).exclude(
                language__in=list(names)
            ).delete()
            for tag, name in names.items():
                CompanyName.objects.update_or_create(
                    owner=company.owner,
                    company=company,
                    language=tag,
                    defaults={
                        "name": name[:200],
                        "source": CompanyName.Source.WIKIDATA,
                        "fetched_at": now,
                    },
                )
            self.keep_point(company, held)

    def keep_point(self, company, held) -> None:
        """Where it is, from the same answer, on the primary address (#701).

        A point a person placed by hand beats Wikidata's, which beats one placed from the
        address's postcode or town; a company with no address keeps no point.
        """
        point = held.get("point")
        address = point and _primary_address(company)
        if not address or address.placed_by == address.Placed.MANUAL:
            return
        address.latitude, address.longitude = float(point[0]), float(point[1])
        address.placed_by = address.Placed.WIKIDATA
        address.placed_at = timezone.now()
        address.save(
            update_fields=["latitude", "longitude", "placed_by", "placed_at", "updated_at"]
        )


class CompanyNamesView(LoginRequiredMixin, View):
    """Type a company's name for a language Wikidata gave none for, or take one off (#673)."""

    def post(self, request, pk: int):
        from postulo.core import languages

        from .models import CompanyName

        company = get_object_or_404(Company.objects.for_user(request.user), pk=pk)
        back = reverse("jobs:company_detail", args=[company.pk])
        if request.POST.get("remove"):
            company.names.filter(pk=as_pk(request.POST.get("remove"))).delete()
            return redirect(back)
        tag = languages.tag(request.POST.get("language", ""))
        name = " ".join((request.POST.get("name") or "").split())[:200]
        if not name or not languages.well_formed(tag) or tag not in languages.NATIVE_NAMES:
            messages.error(request, _("Choose a language and type the name."))
            return redirect(back)
        held = company.names.filter(language=tag).first()
        if held is not None and held.source == CompanyName.Source.WIKIDATA:
            messages.error(
                request,
                _(
                    "Wikidata gives its name in that language. Fetch again to change it, or "
                    "take it off first."
                ),
            )
            return redirect(back)
        CompanyName.objects.update_or_create(
            owner=company.owner,
            company=company,
            language=tag,
            defaults={"name": name, "source": CompanyName.Source.TYPED, "fetched_at": None},
        )
        return redirect(back)


class CompanyPlacesView(LoginRequiredMixin, TemplateView):
    """The companies' places as GeoJSON: the file, and points read in (#880).

    Two steps on one address, as the vCard import is: a file is read and what was read is
    held in the session, the page says what each point would do with **none ticked**, and
    nothing is placed until somebody ticks a point and presses the button.
    """

    template_name = "jobs/company_places.html"

    def get(self, request, *args, **kwargs):
        if request.GET.get("download"):
            import json as json_module

            from . import geojson

            response = HttpResponse(
                json_module.dumps(geojson.export(request.user), ensure_ascii=False, indent=1),
                content_type="application/geo+json; charset=utf-8",
            )
            response["Content-Disposition"] = 'attachment; filename="companies.geojson"'
            return response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        from . import geojson

        context = super().get_context_data(**kwargs)
        points = geojson.held(self.request.session.get(geojson.SESSION_KEY))
        context["entries"] = geojson.review(self.request.user, points) if points else None
        context["limit"] = geojson.MAX_FEATURES
        return context

    def post(self, request, *args, **kwargs):
        from django.utils.translation import ngettext

        from . import geojson

        action = request.POST.get("action")
        if action == "forget":
            request.session.pop(geojson.SESSION_KEY, None)
            return redirect("jobs:company_places")
        if action == "confirm":
            points = geojson.held(request.session.get(geojson.SESSION_KEY))
            chosen = {
                int(value)
                for value in request.POST.getlist("chosen")
                if value.isdigit() and len(value) < 6
            }
            if not chosen:
                messages.info(request, _("No point was ticked, so nothing was placed."))
                return redirect("jobs:company_places")
            placed = geojson.apply(request.user, points, chosen)
            request.session.pop(geojson.SESSION_KEY, None)
            messages.success(
                request,
                ngettext(
                    "%(count)d company placed by hand.",
                    "%(count)d companies placed by hand.",
                    placed,
                )
                % {"count": placed},
            )
            return redirect("jobs:company_map")
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a file first."))
            return redirect("jobs:company_places")
        try:
            points = geojson.read(upload.read(geojson.MAX_BYTES + 1))
        except geojson.GeoJsonRefused as refused:
            messages.error(request, str(refused))
            return redirect("jobs:company_places")
        if not points:
            messages.warning(request, _("That file was read, and there was no point in it."))
            return redirect("jobs:company_places")
        request.session[geojson.SESSION_KEY] = geojson.hold(points)
        return redirect("jobs:company_places")


class AddressPlaceView(LoginRequiredMixin, View):
    """Put one address on the map by hand, or take a hand-placed point off (#702).

    The one way an address is placed at a street's precision: a person drops the pin where
    it is, on a map drawn over the operator's tiles, or types the two numbers. Nothing is
    looked up anywhere; the point is stored on the instance and never leaves it. Taken off,
    the address is placed from its postcode or town again as it would be on a save.
    """

    template_name = "jobs/address_place.html"

    def _address(self, request, pk: int):
        from postulo.core.models import PostalAddress

        if not mapping.map_offered(request.user):
            raise Http404
        return get_object_or_404(PostalAddress.objects.for_user(request.user), pk=pk)

    def _back(self, request, address) -> str:
        from postulo.accounts.models import Profile

        from .models import Contact

        holder = address.holder
        if isinstance(holder, Company):
            fallback = reverse("jobs:company_update", args=[holder.pk])
        elif isinstance(holder, Contact):
            fallback = reverse("jobs:contact_update", args=[holder.pk])
        elif isinstance(holder, Profile):
            fallback = reverse("accounts:profile")
        else:
            fallback = reverse("jobs:company_map")
        return safe_next(request, fallback)

    def get(self, request, pk: int):
        address = self._address(request, pk)
        current = tiles.current()
        context = {
            "address": address,
            "back": self._back(request, address),
            "tiles": current,
            "outline": [] if current.fetched else outline.rings(),
        }
        response = render(request, self.template_name, context)
        response._csp_config = tiles.policy_for(current)
        return response

    def post(self, request, pk: int):
        from . import placing

        address = self._address(request, pk)
        if request.POST.get("take_off"):
            placing.take_off_by_hand(address)
            messages.success(request, _("Taken off by hand: it is placed from its address again."))
            return redirect(self._back(request, address))
        try:
            lat = float(request.POST.get("latitude", ""))
            lon = float(request.POST.get("longitude", ""))
        except ValueError:
            lat = lon = None
        if lat is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            messages.error(
                request,
                _(
                    "Drop the pin on the map, or give a latitude between -90 and 90 and a "
                    "longitude between -180 and 180."
                ),
            )
            return redirect(request.path)
        placing.place_by_hand(address, round(lat, 6), round(lon, 6))
        messages.success(request, _("Placed by hand. Changing the address will not move it."))
        return redirect(self._back(request, address))


class CompanyMapView(LoginRequiredMixin, TemplateView):
    """The places the person's companies are in, and the map that draws them (#108, #898).

    The list is the page and the map is beside it: a map is the classic screen-reader
    failure, so every place, every company and every count is text before any of it is
    drawn, and the page works with scripts off. Leaflet draws the same places over the
    basemap the operator chose (`postulo.jobs.tiles`): OpenStreetMap's tiles by default, a
    tile server of their own, or the world's outline with nothing fetched.

    A place is a company's location text as recorded; its dot, when one is drawn, is the
    coordinate one of the companies there carries, and its size is the number of companies
    at the place -- the question the map answers is where the search is spread, not where
    any single office sits.
    """

    template_name = "jobs/company_map.html"

    def dispatch(self, request, *args, **kwargs):
        # Off, there is no map: the page is not found rather than empty (#700). A person
        # who is not signed in is still sent to sign in first, as everywhere else.
        if request.user.is_authenticated and not mapping.map_offered(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        from . import places as places_data

        context = super().get_context_data(**kwargs)
        pinned = pinned_addresses(self.request.user)
        # A place is where an address resolves to, not how its town was spelt (#703): two
        # spellings of one town are one dot. An address the tables cannot place is a place of
        # its own words, listed and not drawn.
        places: dict[tuple, dict] = {}
        where_each: dict[str, set] = {}
        # Which address is the primary is worth saying only of a company with more than one.
        addresses_of: dict[str, int] = {}
        for entry in pinned:
            addresses_of[entry["key"]] = addresses_of.get(entry["key"], 0) + 1
        for entry in pinned:
            if entry["lat"] is not None:
                key = ("at", round(entry["lat"], 4), round(entry["lon"], 4))
            else:
                key = ("text", entry["place"].casefold())
            place = places.setdefault(
                key,
                {
                    "label": entry["place"],
                    "count": 0,
                    "companies": [],
                    "people": [],
                    "lat": entry["lat"],
                    "lon": entry["lon"],
                    "_seen": set(),
                },
            )
            if entry["company"]:
                place["companies"].append(
                    {
                        "name": entry["name"],
                        "url": entry["url"],
                        "what": entry["what"],
                        "primary": entry["primary"] and addresses_of[entry["key"]] > 1,
                    }
                )
                # A place counts its companies, not their addresses: two offices of one
                # company in one town are one company there.
                if entry["key"] not in place["_seen"]:
                    place["_seen"].add(entry["key"])
                    place["count"] += 1
                where_each.setdefault(entry["key"], set()).add(key)
            else:
                place["people"].append((entry["name"], entry["url"], entry["what"]))
        for place in places.values():
            del place["_seen"]
        context["places"] = sorted(
            places.values(), key=lambda entry: (-entry["count"], entry["label"].casefold())
        )
        # Said only when it is true: a company at several places is counted at each (#703).
        context["counted_at_several"] = any(len(keys) > 1 for keys in where_each.values())
        # What Leaflet draws: one marker per address, as data, each saying whose it is, so
        # the clusters count companies and a company never reaches the page as markup.
        context["drawn"] = [
            {
                "lat": entry["lat"],
                "lon": entry["lon"],
                "name": entry["name"],
                "url": entry["url"],
                "what": entry["what"],
                "place": entry["place"],
                "company": entry["key"] if entry["company"] else "",
            }
            for entry in pinned
            if entry["lat"] is not None
        ]
        context["dataset_available"] = places_data.available()
        context["postcodes_available"] = places_data.postcodes_available()
        context["tiles"] = tiles.current()
        context["outline"] = [] if context["tiles"].fetched else outline.rings()
        return context

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        # The one page whose images may come from the tile server, and only from it (#898).
        response._csp_config = tiles.policy_for(context["tiles"])
        # The one page that may ask where the browser is, and only when *Near me* is pressed
        # (#850); every other response forbids it (`core.middleware`).
        response["Permissions-Policy"] = "geolocation=(self)"
        return response


class CompanyCellView(LoginRequiredMixin, EditableCellView):
    """One cell of the companies table, changed where it sits (#135).

    Three attributes and the machinery does the rest, which is the measure of whether the
    second table to want this is a view or a paragraph.
    """

    model = Company
    form_class = CompanyForm
    columns = CompaniesTable.columns
    form_url_name = "jobs:company_update"


class CompanyBulkView(LoginRequiredMixin, View):
    """Put several companies in a field of activity at once (#134).

    Additive only, and more pointedly here than for applications: deleting a company cascades
    to every posting under it, which the interface says plainly when it is one company and
    would be saying about an unseen number when it is forty.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from django.contrib import messages

        from postulo.core import bulk

        rows = bulk.chosen_rows(request, Company)
        if not rows.exists():
            messages.info(request, bulk.nothing_chosen())
            return redirect(self._back(request))

        if request.POST.get(bulk.ACTION, "") != "industry":
            messages.error(request, _("That is not something Postulo can do to several at once."))
            return redirect(self._back(request))

        count = self._industry(request, rows)
        messages.success(request, bulk.changed(count, CompaniesTable))
        return redirect(self._back(request))

    def _industry(self, request: HttpRequest, rows) -> int:
        try:
            wanted = int(request.POST.get("industry") or 0)
        except (TypeError, ValueError):
            return 0
        from postulo.core import bulk

        industry = Industry.objects.for_user(request.user).filter(pk=wanted).first()
        if industry is None:
            return 0
        return bulk.link_all(list(rows), "industries", industry)

    @staticmethod
    def _back(request: HttpRequest) -> str:
        from postulo.core.redirects import safe_next

        return safe_next(request, reverse("jobs:company_list"))


class CompanyDetailView(OwnedObjectMixin, DetailView):
    model = Company
    template_name = "jobs/company_detail.html"
    context_object_name = "company"

    def get_queryset(self):
        # `parent` is named in the header, so it is fetched with the company rather than
        # by a second query while rendering.
        return (
            super()
            .get_queryset()
            .select_related("parent")
            .prefetch_related(
                "industries", "identifiers", "names", "phone_numbers", "postal_addresses"
            )
        )

    def get_context_data(self, **kwargs) -> dict:
        from postulo.core import messaging_handles, phone_numbers, web_links

        from . import structure

        context = super().get_context_data(**kwargs)
        person = self.request.user
        # The summary of identifiers in the person's order, with the schemes they switched
        # off counted rather than drawn (#672). All of them are still in the edit form.
        shown, hidden = identifier_order.shown_and_hidden(
            self.object.identifiers.all(), getattr(person, "profile", None)
        )
        context["identifier_rows"] = shown
        # How each reads: the mark or icon, the label, or both (#855).
        context["identifier_style"] = getattr(
            getattr(person, "profile", None), "identifier_style", "both"
        )
        # What deleting it would take with it, for the dialog on this page (#854).
        context["deletion"] = deletions.facts_for(CompanyDeleteView, self.request, self.object)
        context["identifiers_hidden"] = len(hidden)
        # Which of the company's identifiers do not fit together, worked out as the page is
        # drawn and told, never refused (#675). Hidden ones count: a warning concerns them too.
        context["identifier_findings"] = identifiers.findings(self.object.identifiers.all())
        # The data-subject export beside each contact, while the feature offers it (#297).
        from postulo.core import gdpr

        context["gdpr_offered"] = gdpr.is_offered(person)
        # A hierarchy that silently keeps counting leaves is a hierarchy that changes
        # nothing, so the page asks which reading it is showing -- and defaults to the one
        # every figure in Postulo has always meant, this company alone (#138).
        across = self.request.GET.get("across", "")
        counted = structure.companies_counted(self.object, person, across=across)
        context["across"] = across
        context["across_group"] = structure.ACROSS_GROUP
        context["showing_group"] = len(counted) > 1
        context["in_a_group"] = structure.in_a_group(self.object, person)
        context["group"] = structure.group_of(self.object, person)
        context["parent"] = structure.parent_of(self.object, person)
        context["children"] = structure.children_of(self.object, person)
        # The entries of the career that name this company, newest first (#683).
        context["career_entries"] = self.object.career_entries.order_by("-start_date", "-pk")
        # And the education entries that name it as their institution (#685).
        context["education_entries"] = self.object.education_entries.order_by("-end_date", "-pk")
        # And the certifications it issued, as the person recorded them (#686).
        context["certifications"] = self.object.certifications.order_by("-issued_on", "-pk")
        # And the honours it gave and the memberships it holds (#693).
        context["honours"] = self.object.honours.order_by("-awarded_on", "-pk")
        context["memberships"] = self.object.memberships.order_by("-start_date", "-pk")
        # And the courses it gave (#695).
        context["courses"] = self.object.courses.order_by("-end_date", "-pk")
        # The meetings with an adviser, for a company that is a public employment service (#888).
        context["appointments"] = (
            self.object.appointments.select_related("contact").order_by("-starts_at", "-pk")
            if self.object.is_employment_service
            else []
        )
        context["contacts"] = self.object.contacts.select_related("department").prefetch_related(
            "phone_numbers", "web_links", "messaging_handles"
        )
        context["postings"] = (
            JobPosting.objects.for_user(person)
            .filter(company__in=counted)
            .select_related("company")
            .prefetch_related("applications")
        )
        # Decided once for the page rather than per contact: it is one answer about one
        # person, and asking it per row would be a query per row for the same answer.
        context["several_numbers"] = phone_numbers.several_allowed(person)
        # Its names in other languages, and whether Wikidata may be asked for them (#673).
        from postulo.core import languages

        context["company_names"] = [
            (languages.native_name(row.language, row.language), row)
            for row in self.object.names.all()
        ]
        context["wikidata_offered"] = wikidata_offered(person)
        context["name_languages"] = sorted(
            languages.NATIVE_NAMES.items(), key=lambda pair: pair[1].casefold()
        )
        # The company's own numbers and addresses (#329), on the same terms as a contact's.
        context["company_numbers"] = phone_numbers.numbers_for(self.object, person)
        context["company_addresses"] = list(self.object.postal_addresses.all())
        # The kinds of link this person is offered several of; a contact's other rows of
        # a kind that is off stay unlisted, exactly as the numbers do (#189).
        context["several_links"] = web_links.offered_kinds(person)
        # A contact's handles are listed while the feature is on, and not at all when off (#682).
        context["messaging_offered"] = messaging_handles.several_allowed(person)
        context["structure_on"] = structure.structure_allowed(person)
        # Worked out as the page is drawn, from the record as it is now (#239): which of
        # this person's other companies look like this one, and which of the people here
        # look like somebody recorded elsewhere. Told, and nothing done about it.
        merge_url = reverse("jobs:company_merge", args=[self.object.pk])
        context["duplicates"] = [
            (candidate, f"{merge_url}?with={candidate.record.pk}")
            for candidate in duplicates.for_company(self.object)
        ]
        context["people_alike"] = duplicates.contacts_with_any(person, context["contacts"])
        return context


class CompanyIdentifiersMixin:
    """The identifiers block on the company form: an inline formset saved with the company.

    The formset validates against the person's other companies, so it needs the user
    before the company exists; on create it is bound to the unsaved instance and told
    the owner explicitly.
    """

    def get_identifiers(self) -> CompanyIdentifierFormSet:
        instance = getattr(self, "object", None) or Company(owner=self.request.user)
        kwargs = {"instance": instance, "prefix": "identifiers"}
        # A form posted without the block (an older client, a script) means no change to
        # the identifiers, not an error about a missing management form.
        if self.request.method == "POST" and "identifiers-TOTAL_FORMS" in self.request.POST:
            kwargs["data"] = self.request.POST
        formset = CompanyIdentifierFormSet(**kwargs)
        formset.user = self.request.user
        return formset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.setdefault("identifiers", self.get_identifiers())
        context["identifier_schemes"] = identifiers.schemes().values()
        # A saved identifier goes at once, after asking, as on *Your details* (#857).
        context["identifier_removals"] = identifier_removals.offer(context["identifiers"])
        return context

    def refuse_bad_identifiers(self, form):
        """The page again, if an identifier row is wrong; None when the save may go on.

        Asked first by the outermost ``form_valid``, so that the success message and the
        logo fields are reached only when the company really is saved.
        """
        formset = self.get_identifiers()
        if formset.is_bound and not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, identifiers=formset))
        return None

    def form_valid(self, form):
        formset = self.get_identifiers()
        with transaction.atomic():
            response = super().form_valid(form)
            if formset.is_bound:
                formset.instance = self.object
                for row in formset.forms:
                    row.instance.company = self.object
                formset.save()
            # Its numbers, addresses (#329) and social profiles (#906), with the company or
            # not at all.
            numbers, addresses, links = getattr(self, "held_rows", (None, None, None))
            self.save_phone_numbers(numbers, self.object)
            self.save_addresses(addresses, self.object)
            if links is not None:
                self.save_web_links(links, self.object)
        return response

    def refuse_bad_rows(self, form):
        """The page again if a number or an address is wrong; None when the save may go on.

        Kept on the view for the save, which runs further down the chain (#329).
        """
        numbers = self.get_phone_numbers()
        addresses = self.get_addresses()
        links = self.get_web_links()
        if (
            self.phone_numbers_invalid(numbers)
            or self.addresses_invalid(addresses)
            or self.web_links_invalid(links)
        ):
            return self.render_to_response(
                self.get_context_data(form=form, numbers=numbers, addresses=addresses, links=links)
            )
        self.held_rows = (numbers, addresses, links)
        return None


class CompanyIdentifierRemoveView(OwnedObjectMixin, View):
    """Take one identifier off a company, there and then, after the dialog's question (#857).

    The row is looked up among this person's own (a foreign id and a made-up one both answer
    404, so nothing is learnt about other accounts, #759). A GET removes nothing and leads
    back to the company's form: it is where somebody lands who confirmed after their session
    ended and signed in again. The answer to the script is JSON, the same shape
    ``accounts.RemoveRowView`` gives, so the page's own script takes the row off.
    """

    def _row(self, request, pk: int):
        return get_object_or_404(
            CompanyIdentifier.objects.for_user(request.user).select_related("company"), pk=pk
        )

    @staticmethod
    def _form(row) -> str:
        return f"{reverse('jobs:company_update', args=[row.company_id])}#section-identifiers"

    def get(self, request, pk: int):
        return redirect(self._form(self._row(request, pk)))

    def post(self, request, pk: int):
        row = self._row(request, pk)
        said = str(identifier_removals.said(identifier_removals.words(row)))
        company = row.company
        with transaction.atomic():
            row.delete()
        if request.htmx:
            return JsonResponse(
                {"removed": True, "said": said, "count": company.identifiers.count()}
            )
        messages.success(request, said)
        return redirect(self._form(row))


class LogoFormMixin:
    """Whatever the logo fields asked for, done after the company is saved.

    After, because a logo needs the company's primary key to be filed under; and never
    fatally, because a picture that would not come is no reason to lose everything else
    the person typed. The problem is shown and the company is saved.

    The views that use it are out of the request's transaction (#357): the company and its
    identifiers are written in the one `atomic` of `CompanyIdentifiersMixin`, `logos.store`
    writes in its own, and the download between them holds no lock.
    """

    def get_context_data(self, **kwargs):
        from postulo.jobs.forms import CompanyLogoForm

        context = super().get_context_data(**kwargs)
        context["logo_form"] = CompanyLogoForm()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        problem = form.apply_logo(self.object)
        if problem:
            messages.warning(
                self.request, _("The logo was not changed: %(problem)s") % {"problem": problem}
            )
        return response


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CompanyCreateView(
    LogoFormMixin,
    OwnedObjectMixin,
    UserFormKwargsMixin,
    OwnerFormMixin,
    CompanyIdentifiersMixin,
    PhoneNumbersMixin,
    PostalAddressesMixin,
    WebLinksMixin,
    CreateView,
):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"
    addresses_pinnable = False
    links_for_company = True

    def form_valid(self, form):
        if (refused := self.refuse_bad_identifiers(form)) is not None:
            return refused
        if (refused := self.refuse_bad_rows(form)) is not None:
            return refused
        messages.success(self.request, _("Company added."))
        return super().form_valid(form)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CompanyUpdateView(
    LogoFormMixin,
    OwnedObjectMixin,
    UserFormKwargsMixin,
    CompanyIdentifiersMixin,
    PhoneNumbersMixin,
    PostalAddressesMixin,
    WebLinksMixin,
    UpdateView,
):
    model = Company
    form_class = CompanyForm
    template_name = "jobs/company_form.html"
    addresses_pinnable = False
    links_for_company = True

    def form_valid(self, form):
        if (refused := self.refuse_bad_identifiers(form)) is not None:
            return refused
        if (refused := self.refuse_bad_rows(form)) is not None:
            return refused
        messages.success(self.request, _("Company updated."))
        return super().form_valid(form)


class CompanyLogoView(OwnedObjectMixin, View):
    """Serve a company's logo — from this instance, never from anybody else's server.

    It is a row beside the company (#662) and comes out only through here, which is what
    lets the content security policy keep saying ``img-src 'self'``. The address carries
    the moment it was fetched, so a new logo is never hidden behind an old cache entry.
    """

    def get_queryset(self):
        return Company.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        company = get_object_or_404(self.get_queryset(), pk=pk)
        logo = CompanyLogo.objects.filter(company=company).first()
        if logo is None:
            raise Http404
        # The name follows what is stored, which is not always a PNG (#264): the type is the
        # one written beside the bytes, and an SVG is a vector with its own extension.
        suffix = ".svg" if logo.media_type == "image/svg+xml" else ".png"
        response = serve_stored_picture(logo, download_name=f"logo{suffix}")
        response["Cache-Control"] = "private, max-age=86400"
        return response


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CompanyLogoActionView(OwnedObjectMixin, View):
    """*Find logo* and *Refresh*: one request each, when a person presses the button.

    **Outside a transaction of its own (#220).** *Find logo* reads the company's site and
    then tries up to six images from it, one network round trip each, and under
    `ATOMIC_REQUESTS` on SQLite every one of those seconds was a second nothing else could
    write. What has to be atomic is putting the picture on the company, and `logos.store`
    and `logos.clear` each do that in a transaction of one statement.
    """

    def get_queryset(self):
        return Company.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int, action: str) -> HttpResponse:
        from postulo.core import errands

        company = get_object_or_404(self.get_queryset(), pk=pk)
        # Removing a logo is a database write and nothing else, so it stays here: sending
        # an instant act off to a worker would be a spinner shown for the sake of symmetry.
        if action == "remove":
            logos.clear(company)
            messages.success(request, _("The logo is gone."))
            return redirect(safe_next(request, company.get_absolute_url()))
        if action == "set":
            return self._set(request, company)
        if action not in ("website", "refresh"):
            raise Http404
        # The other two read the company's site and then try up to six images, one round
        # trip each, which is not a thing to make somebody sit through (#247). One use of
        # the fetch allowance for the press, spent before the errand is sent (#407).
        try:
            throttle.fetch(request.user)
        except throttle.TooOften as too_often:
            if request.htmx:
                # In the dialog: said where the answer would have been, not as a redirect
                # that would draw the whole page inside it.
                return render(request, "jobs/partials/logo_refused.html", {"said": str(too_often)})
            messages.error(request, str(too_often))
            return redirect(safe_next(request, company.get_absolute_url()))
        errand = errands.send(
            "logo", request.user, subject=company, company_id=company.pk, action=action
        )
        if request.htmx:
            # The dialog on the company's page (#674): the same errand, shown where it was
            # asked for. A plain post -- no script -- still lands on the errand's own page.
            from postulo.core.views_errands import state_context

            return render(
                request,
                "core/partials/errand_state.html",
                state_context(errand, in_dialog=True, first=True),
            )
        return redirect("core:errand", pk=errand.pk)

    def _set(self, request: HttpRequest, company: Company) -> HttpResponse:
        """An address or an upload, from the logo dialog on the edit page (#853).

        Its own form, so that putting a logo on a company saves nothing else of what is on
        the page. An address is a fetch, so it spends one use of the fetch allowance like
        *Find logo* does; a file read from the request spends none.
        """
        from postulo.jobs.forms import CompanyLogoForm

        back = safe_next(request, reverse("jobs:company_update", args=[company.pk]))
        form = CompanyLogoForm(request.POST, request.FILES)
        if not form.is_valid():
            for problems in form.errors.values():
                for problem in problems:
                    messages.error(request, problem)
            return redirect(back)
        if form.cleaned_data.get("logo_url") and not form.cleaned_data.get("logo_upload"):
            try:
                throttle.fetch(request.user)
            except throttle.TooOften as too_often:
                messages.error(request, str(too_often))
                return redirect(back)
        problem = form.apply_logo(company)
        if problem:
            messages.warning(
                request, _("The logo was not changed: %(problem)s") % {"problem": problem}
            )
        else:
            messages.success(request, _("The logo was changed."))
        return redirect(back)


class MergeView(LoginRequiredMixin, View):
    """Two records of one thing made into one, behind a page that says what will move (#239).

    One address and two states of it. Bare, it asks *which one is the same as this?* --
    the records `jobs.duplicates` noticed first, then a list of all the others, because
    the person knows things the comparison does not. With ``?with=`` it is the
    confirmation: what moves, what the kept record takes, where the two differ and what
    becomes of the difference. Both are a GET and change nothing.

    **The merge is a POST from the confirmation and from nowhere else.** A link cannot
    merge anything: a page that was only opened, a bookmark, a crawler following addresses
    must never be able to delete a record.

    **Both records are looked up among the person's own**, the one in the address and the
    one in the query alike, so somebody else's is a 404 and never a 403 -- and never a
    merge of one person's company into another's.

    The record in the address is the one that is kept. *Keep the other instead* is the
    same page with the two exchanged, which is a link and needs no script.
    """

    model = None
    template_name = "jobs/merge.html"
    #: `company` or `contact`: which words the page uses, and which address it is at.
    kind = ""
    url_name = ""

    def records(self):
        return self.model.objects.for_user(self.request.user)

    def plan(self, kept, other):
        raise NotImplementedError

    def merge(self, kept, other):
        raise NotImplementedError

    def alike(self, kept) -> list:
        raise NotImplementedError

    def after(self, kept) -> str:
        """Where the page leads once it is done, and where *Cancel* goes."""
        raise NotImplementedError

    def address(self, kept, other=None) -> str:
        url = reverse(self.url_name, args=[kept.pk])
        return f"{url}?with={other.pk}" if other is not None else url

    def other_or_404(self, kept, raw):
        """The record named beside the kept one: another of this person's, or a 404."""
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            raise Http404 from None
        return get_object_or_404(self.records().exclude(pk=kept.pk), pk=pk)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        kept = get_object_or_404(self.records(), pk=pk)
        context = {"kind": self.kind, "kept": kept, "cancel_url": self.after(kept)}
        named = request.GET.get("with", "")
        if named:
            other = self.other_or_404(kept, named)
            context["other"] = other
            context["plan"] = self.plan(kept, other)
            context["swap_url"] = self.address(other, kept)
        else:
            context["candidates"] = [
                (candidate, self.address(kept, candidate.record)) for candidate in self.alike(kept)
            ]
            context["choices"] = self.records().exclude(pk=kept.pk)
            context["merge_url"] = self.address(kept)
        return render(request, self.template_name, context)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        kept = get_object_or_404(self.records(), pk=pk)
        other = self.other_or_404(kept, request.POST.get("with", ""))
        try:
            plan = self.merge(kept, other)
        except merging.CannotMerge as refusal:
            # Undone whole by the time it is caught. The sentence is the merge's own.
            messages.error(request, refusal.why)
            return redirect(self.address(kept, other))
        messages.success(request, merging.done(plan))
        return redirect(self.after(kept))


class CompanyMergeView(MergeView):
    model = Company
    kind = "company"
    url_name = "jobs:company_merge"

    def plan(self, kept, other):
        return merging.plan_companies(kept, other)

    def merge(self, kept, other):
        return merging.merge_companies(kept, other)

    def alike(self, kept) -> list:
        return duplicates.for_company(kept)

    def after(self, kept) -> str:
        return kept.get_absolute_url()


class CompanyDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Company
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")

    def get_context_data(self, **kwargs):
        """Count what goes with the company (#217).

        A company cascades into its postings and those into applications, their timelines,
        interviews and reminders — a year of a job search, behind one button that used to say
        only "anything belonging to it goes too".
        """
        from django.utils.translation import ngettext

        from postulo.applications.models import Application
        from postulo.documents.models import RenderedDocument
        from postulo.resume.models import Reference

        context = super().get_context_data(**kwargs)
        company = self.object
        applications = Application.objects.filter(posting__company=company)
        postings = company.postings.count()
        applied = applications.count()
        contacts = company.contacts.count()
        appointments = company.appointments.count()
        # Each ngettext carries its two texts as literals: the extractor reads call sites.
        context["consequences"] = [
            text % {"count": number}
            for number, text in (
                (postings, ngettext("%(count)d posting", "%(count)d postings", postings)),
                (applied, ngettext("%(count)d application", "%(count)d applications", applied)),
                (contacts, ngettext("%(count)d contact", "%(count)d contacts", contacts)),
                (
                    appointments,
                    ngettext("%(count)d appointment", "%(count)d appointments", appointments),
                ),
            )
            if number
        ]
        sent = RenderedDocument.objects.filter(application__in=applications).count()
        kept = []
        if sent:
            kept.append(
                ngettext(
                    "The %(count)d document you sent is kept, and still says where it went.",
                    "The %(count)d documents you sent are kept, and still say where they went.",
                    sent,
                )
                % {"count": sent}
            )
        # The career is not deleted with a company (#683): an entry keeps its text.
        entries = (
            company.career_entries.count()
            + company.education_entries.count()
            + company.certifications.count()
            + company.honours.count()
            + company.memberships.count()
            + company.courses.count()
        )
        # A contact goes with its company, and so does a referee's entry (#696).
        referees = Reference.objects.filter(contact__company=company).count()
        if referees:
            consequences = context["consequences"]
            consequences.append(
                ngettext(
                    "%(count)d entry among your references",
                    "%(count)d entries among your references",
                    referees,
                )
                % {"count": referees}
            )
        if entries:
            kept.append(
                ngettext(
                    "%(count)d career entry keeps its text and loses the link.",
                    "%(count)d career entries keep their text and lose the link.",
                    entries,
                )
                % {"count": entries}
            )
        if kept:
            context["kept"] = " ".join(kept)
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Company deleted, along with its postings."))
        return super().form_valid(form)


# ------------------------------------------------------------------- industries


class IndustryListView(OwnedObjectMixin, ListView):
    """The person's vocabulary of industries, with how many companies each holds.

    **Searchable, because this list is no longer necessarily short.** A vocabulary seeded
    from a classification of the whole economy can run to dozens of words, and a page that
    can only be scrolled is a page where merging two spellings means finding both by eye
    (#141). The box narrows by name and by NACE code, server-side, like every other filter
    in Postulo.

    A row appears here only once a company has been given that industry -- the classification
    is a list of *suggestions*, not a list of rows -- so this page grows at the speed somebody
    actually uses it, which is the reason it was safe to make the suggestions long.

    **Grouped by sector (#897)**, in NACE's own order, the industries with no sector last:
    the level above is what makes a list of dozens of words readable, and the search
    matches a sector's name as well.
    """

    model = Industry
    template_name = "jobs/industry_list.html"
    context_object_name = "industries"

    def get_queryset(self):
        found = super().get_queryset().annotate(company_count=Count("companies", distinct=True))
        wanted = self.request.GET.get("q", "").strip()
        if wanted:
            named = [
                letter
                for letter, name in industries.sections()
                if wanted.casefold() in name.casefold()
            ]
            found = found.filter(
                Q(name__icontains=wanted) | Q(code__startswith=wanted) | Q(section__in=named)
            )
        return found

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "").strip()
        context["total"] = Industry.objects.for_user(self.request.user).count()
        context["sectors"] = industries_by_sector(context["industries"])
        return context


def industries_by_sector(rows) -> list[dict]:
    """``[{"section", "name", "industries"}]`` in NACE's order, the ones with none last (#897)."""
    grouped: dict[str, list] = {}
    for industry in rows:
        grouped.setdefault(industry.section, []).append(industry)
    order = [letter for letter, _name in industries.sections()]
    return [
        {
            "section": letter,
            "name": industries.section_name(letter) if letter else _("No sector"),
            "industries": grouped[letter],
        }
        for letter in [*order, ""]
        if letter in grouped
    ]


class IndustryCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = Industry
    form_class = IndustryForm
    template_name = "jobs/industry_form.html"
    success_url = reverse_lazy("jobs:industry_list")


class IndustryUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = Industry
    form_class = IndustryForm
    template_name = "jobs/industry_form.html"
    success_url = reverse_lazy("jobs:industry_list")

    def form_valid(self, form):
        merged = form.cleaned_data.get("merge_into") is not None
        response = super().form_valid(form)
        messages.success(self.request, _("Merged.") if merged else _("Industry renamed."))
        return response


class IndustryDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Industry
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:industry_list")


# -------------------------------------------------------------------- contacts


class ComesBackMixin:
    """A contact form that returns to the page which sent the person, when one did (#696).

    The reference form links to a contact's page to add or edit the person; ``next`` is the
    reference form's own address, and `safe_next` refuses anything that is not on this host.
    """

    def return_to(self) -> str:
        return safe_next(self.request, "")

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["return_to"] = self.return_to()
        return context

    def get_success_url(self) -> str:
        if target := self.return_to():
            return target
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")


class ContactCreateView(
    ComesBackMixin,
    OwnedObjectMixin,
    UserFormKwargsMixin,
    OwnerFormMixin,
    GdprNoticeMixin,
    PhoneNumbersMixin,
    PostalAddressesMixin,
    WebLinksMixin,
    MessagingHandlesMixin,
    CreateView,
):
    model = Contact
    form_class = ContactForm
    template_name = "jobs/contact_form.html"

    def get_initial(self) -> dict:
        initial = super().get_initial()
        company_id = as_pk(self.request.GET.get("company"))
        if (
            company_id
            and Company.objects.for_user(self.request.user).filter(pk=company_id).exists()
        ):
            initial["company"] = company_id
        return initial

    def form_valid(self, form):
        numbers = self.get_phone_numbers()
        addresses = self.get_addresses()
        links = self.get_web_links()
        messaging = self.get_messaging()
        if (
            self.phone_numbers_invalid(numbers)
            or self.addresses_invalid(addresses)
            or self.web_links_invalid(links)
            or self.messaging_invalid(messaging)
        ):
            return self.render_to_response(
                self.get_context_data(
                    form=form,
                    numbers=numbers,
                    addresses=addresses,
                    links=links,
                    messaging=messaging,
                )
            )
        with transaction.atomic():
            response = super().form_valid(form)
            self.save_phone_numbers(numbers, self.object)
            self.save_addresses(addresses, self.object)
            self.save_web_links(links, self.object)
            self.save_messaging(messaging, self.object)
        messages.success(self.request, _("Contact added."))
        return response


class ContactUpdateView(
    ComesBackMixin,
    OwnedObjectMixin,
    UserFormKwargsMixin,
    GdprNoticeMixin,
    PhoneNumbersMixin,
    PostalAddressesMixin,
    WebLinksMixin,
    MessagingHandlesMixin,
    UpdateView,
):
    model = Contact
    form_class = ContactForm
    template_name = "jobs/contact_form.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        # The one page a person has, so this is where it is said that they look like
        # somebody recorded twice (#239). Worked out as the page is drawn; told, not done.
        merge_url = reverse("jobs:contact_merge", args=[self.object.pk])
        context["merge_url"] = merge_url
        # The letters this person wrote, and the applications each went with (#666).
        context["reference_letters"] = list(
            self.object.reference_letters.select_related("upload").prefetch_related(
                "upload__applications__posting__company"
            )
        )
        context["duplicates"] = [
            (candidate, f"{merge_url}?with={candidate.record.pk}")
            for candidate in duplicates.for_contact(self.object)
        ]
        return context

    def form_valid(self, form):
        numbers = self.get_phone_numbers()
        addresses = self.get_addresses()
        links = self.get_web_links()
        messaging = self.get_messaging()
        if (
            self.phone_numbers_invalid(numbers)
            or self.addresses_invalid(addresses)
            or self.web_links_invalid(links)
            or self.messaging_invalid(messaging)
        ):
            return self.render_to_response(
                self.get_context_data(
                    form=form,
                    numbers=numbers,
                    addresses=addresses,
                    links=links,
                    messaging=messaging,
                )
            )
        with transaction.atomic():
            response = super().form_valid(form)
            self.save_phone_numbers(numbers, self.object)
            self.save_addresses(addresses, self.object)
            self.save_web_links(links, self.object)
            self.save_messaging(messaging, self.object)
        return response


class ContactMergeView(MergeView):
    model = Contact
    kind = "contact"
    url_name = "jobs:contact_merge"

    def records(self):
        return super().records().select_related("company", "department")

    def plan(self, kept, other):
        return merging.plan_contacts(kept, other)

    def merge(self, kept, other):
        return merging.merge_contacts(kept, other)

    def alike(self, kept) -> list:
        return duplicates.for_contact(kept)

    def after(self, kept) -> str:
        # A person has no page but the one they are edited on.
        return reverse("jobs:contact_update", args=[kept.pk])


class ContactDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = Contact
    template_name = "partials/confirm_delete.html"

    def get_context_data(self, **kwargs):
        """Say what happens to the letters this person wrote, and offer to delete them (#666).

        They are the account holder's files holding the referee's words. The default keeps
        them, without a referee; ticking the box deletes them with the contact.
        """
        from django.utils.translation import ngettext

        context = super().get_context_data(**kwargs)
        number = self.object.reference_letters.count()
        if number:
            context["kept"] = ngettext(
                "%(count)d reference letter they wrote is kept, without a referee.",
                "%(count)d reference letters they wrote are kept, without a referee.",
                number,
            ) % {"count": number}
            context["choice"] = {
                "name": "delete_letters",
                "label": _("Delete the letters they wrote as well"),
            }
        return context

    def form_valid(self, form):
        from postulo.core import gdpr

        # What was asked about their letters is done first, while the link is there to find
        # them by (#666); the contact's erasure then counts what is left as unlinked.
        letters = self.object.reference_letters.select_related("upload")
        delete_letters = bool(self.request.POST.get("delete_letters"))

        # A deletion that does not say what it removed is a guess about its own effect, so
        # while the feature is offered the erasure carries the report and the person who
        # deleted reads it; off, the plain delete is what Postulo has always done (#297).
        if gdpr.is_offered(self.request.user):
            try:
                # One savepoint: a refused erasure leaves the contact, so it must leave their
                # letters too, and the refusal is a normal response that would commit them.
                with transaction.atomic():
                    if delete_letters:
                        for letter in letters:
                            letter.upload.delete()
                    report = gdpr.erase_contact(self.object)
            except gdpr.ErasureRefused as refused:
                # A plugin holds rows about them that it could not remove, so nothing
                # was. Back to the person, who is still there, with the reason (#371).
                messages.error(self.request, str(refused))
                return redirect(reverse("jobs:contact_update", args=[self.object.pk]))
            messages.success(self.request, report.summary())
            return redirect(self.get_success_url())
        if delete_letters:
            for letter in letters:
                letter.upload.delete()
        return super().form_valid(form)

    def get_success_url(self) -> str:
        if self.object.company_id:
            return reverse("jobs:company_detail", args=[self.object.company_id])
        return reverse("jobs:company_list")


class ContactExportView(OwnedObjectMixin, View):
    """What the instance holds on one other person, as one document (#297).

    The answer to "what do you have on me?", handed over as a download rather than a page,
    because the point of a data-subject export is that it leaves the site with the person
    who asked. One `JsonResponse` rather than the archive's zip: a contact holds no files
    of its own, and a zip of one JSON entry would be ceremony.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.core import gdpr

        if not gdpr.is_offered(request.user):
            raise Http404
        contact = get_object_or_404(Contact.objects.for_user(request.user), pk=pk)
        response = JsonResponse(
            gdpr.contact_document(contact),
            json_dumps_params={"ensure_ascii": False},
            content_type="application/json",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="postulo-contact-{contact.pk}.json"'
        )
        return response


# -------------------------------------------------------------------- postings


def listing_page_context(request, posting, *, event_form=None) -> dict:
    """What the listing's page draws beyond the listing: its history, and what it kept.

    Written once for the two views that draw the page -- reading it, and adding to its
    history with a mistake in the form, which draws it again with the errors (#270).
    """
    from .forms import ListingEventForm
    from .history import history_of
    from .models import CapturedPage

    return {
        "posting": posting,
        "object": posting,
        "discard_reasons": DiscardReason.choices,
        # What was kept of the page this listing was captured from, if anything was
        # (#256). Here as well as on the review screen, because this is where somebody
        # looks once the advert has gone: the listing outlives the posting. A capture bound
        # to it later is one of these too (#270).
        "kept_pages": (
            CapturedPage.objects.for_user(request.user)
            .filter(capture__posting=posting)
            .select_related("capture")
        ),
        # What arrived about it, newest first (#270): the same entries the application's
        # page reads through its posting, once there is an application.
        "listing_events": history_of(posting),
        "event_form": event_form or ListingEventForm(user=request.user),
    }


class PostingDetailView(OwnedObjectMixin, DetailView):
    model = JobPosting
    template_name = "jobs/posting_detail.html"
    context_object_name = "posting"

    def get_queryset(self):
        return super().get_queryset().select_related("company").with_application_count()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(listing_page_context(self.request, self.object))
        # What deleting it would take with it, for the dialog on this page (#854).
        context["deletion"] = deletions.facts_for(PostingDeleteView, self.request, self.object)
        return context


class PostingUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = JobPosting
    form_class = JobPostingForm
    template_name = "jobs/posting_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Cancel goes back to the listing the person came from (#628).
        context["cancel_url"] = self.object.get_absolute_url()
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Posting updated."))
        return super().form_valid(form)


class PostingDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = JobPosting
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("jobs:company_list")

    def get_context_data(self, **kwargs):
        """A listing takes its applications with it; say how many before it does (#217)."""
        from django.contrib.contenttypes.models import ContentType
        from django.utils.translation import ngettext

        from postulo.documents.models import RenderedDocument

        from .models import Capture, CapturedPage

        context = super().get_context_data(**kwargs)
        applications = self.object.applications.all()
        number = applications.count()
        consequences = []
        if number:
            consequences.append(
                ngettext(
                    "%(count)d application, with its timeline",
                    "%(count)d applications, with their timelines",
                    number,
                )
                % {"count": number}
            )
        # Its history goes with it, and so do the captures that became it, with what they
        # kept of the page (#664); a file somebody forwarded lives in their documents and
        # is not deleted with a listing (#270), and saying so here is the difference.
        captures = self.object.captures.all()
        if captures.exists():
            consequences.append(
                ngettext(
                    "%(count)d capture that became it",
                    "%(count)d captures that became it",
                    captures.count(),
                )
                % {"count": captures.count()}
            )
            kept_pages = CapturedPage.objects.filter(capture__in=captures).count()
            if kept_pages:
                consequences.append(
                    ngettext(
                        "%(count)d kept page of a capture",
                        "%(count)d kept pages of captures",
                        kept_pages,
                    )
                    % {"count": kept_pages}
                )
        entries = self.object.events.all()
        if entries.exists():
            consequences.append(
                ngettext(
                    "%(count)d entry in its history",
                    "%(count)d entries in its history",
                    entries.count(),
                )
                % {"count": entries.count()}
            )
        if consequences:
            context["consequences"] = consequences
        kept = []
        sent = RenderedDocument.objects.filter(application__in=applications).count()
        if sent:
            kept.append(
                ngettext(
                    "The %(count)d document you sent is kept, and still says where it went.",
                    "The %(count)d documents you sent are kept, and still say where they went.",
                    sent,
                )
                % {"count": sent}
            )
        pointed = (
            entries.filter(artefact_id__isnull=False)
            .exclude(artefact_type=ContentType.objects.get_for_model(Capture))
            .order_by()
            .values("artefact_type", "artefact_id")
            .distinct()
            .count()
        )
        if pointed:
            kept.append(
                ngettext(
                    "The %(count)d file its history points at is kept where it is.",
                    "The %(count)d files its history points at are kept where they are.",
                    pointed,
                )
                % {"count": pointed}
            )
        if kept:
            context["kept"] = " ".join(kept)
        return context
