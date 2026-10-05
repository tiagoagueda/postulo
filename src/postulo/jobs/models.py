"""Companies, the people inside them, and the postings they advertise.

A posting is a fact about the world: it exists whether or not you do anything about it.
What you do about it is an :class:`~postulo.applications.models.Application`. Keeping
them apart means a posting you decided against still leaves a record, and re-applying to
the same role a year later does not overwrite the first attempt.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import (
    Case,
    Count,
    DecimalField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.translation import gettext_lazy as _

from postulo.core import slugs
from postulo.core.addresses import same_url
from postulo.core.identifiers import COMPANY, MAX_VALUE_LENGTH, KeepsItsScheme, scheme_field
from postulo.core.models import OwnedModel, OwnedQuerySet

from . import esco, identifiers, industries, roles


def _always_placing(person) -> bool:
    return True


#: Whether this person's locations are placed on the map. The answer is the maps feature's,
#: which sits above the models, so `JobsConfig.ready` plugs `mapping.map_offered` in here
#: rather than the model importing upward; until then everything is placed (#700).
placing_offered = _always_placing


class Industry(OwnedModel):
    """A field a company operates in, in the applicant's own words.

    A vocabulary per person rather than a shared list, for the same reason companies are
    per person: two people on one instance may describe the same employer differently,
    and neither should see the other's words. A company may belong to several — a bank
    that is also an insurer and a software house is all three, and counting it under one
    would be a lie the figures repeat. Slugs are unique per owner, so "Fintech" and
    "fintech" are one industry.
    """

    name = models.CharField(_("name"), max_length=160)
    slug = models.SlugField(_("slug"), max_length=160, allow_unicode=True)
    #: The NACE division this name is, where it is one of them. Empty for a word somebody
    #: made up, which is the ordinary case and not a lesser kind of industry -- the code is
    #: what makes a report legible to an employment office that thinks in NACE, and nothing
    #: else depends on it (#140).
    code = models.CharField(_("NACE division"), max_length=4, blank=True)

    class Meta:
        verbose_name = _("industry")
        verbose_name_plural = _("industries")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("owner", "slug"), name="unique_industry_slug_per_owner")
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        # The code follows the name rather than being set beside it. Renaming an industry
        # to a division's name gives it that division; renaming it away takes it back. One
        # invariant, and no way for the two to disagree (#140).
        self.code = industries.code_for(self.name)
        slugs.save_with_slug(self, "industry", args, kwargs, super().save)

    @classmethod
    def named(cls, owner, names) -> list[Industry]:
        """The owner's industries with these names, made if missing, in the order given.

        Matching is by name, so capitals, accents and spacing do not breed duplicates; a
        name already known keeps the spelling it was first entered with (#356).
        """
        return slugs.named(cls, owner, names, fallback="industry")

    @classmethod
    def split(cls, text: str) -> list[str]:
        """Names out of a typed list: commas, semicolons and slashes all separate.

        Except inside a name the input itself offers, such as a NACE division called
        *Manufacture of computer, electronic and optical products*: those are taken out
        whole first, so choosing a suggestion yields the suggestion (#531).
        """
        text = text or ""
        whole: list[str] = []
        for known in industries.names_with_separators():
            pattern = re.compile(rf"(?<![^;,/\s]){re.escape(known)}(?![^;,/\s])", re.IGNORECASE)

            def keep(match: re.Match[str]) -> str:
                whole.append(match.group(0))
                return f"\x00{len(whole) - 1}\x00"

            text = pattern.sub(keep, text)
        parts = (part.strip() for part in re.split(r"[;,/]", text))
        return [
            re.sub(r"\x00(\d+)\x00", lambda m: whole[int(m.group(1))], part)
            for part in parts
            if part
        ]


class CompanyQuerySet(OwnedQuerySet):
    def bulk_create(self, objs, *args, **kwargs):
        """Give each company its `name_key`, which `save` would have: the constraint reads it."""
        objs = list(objs)
        for company in objs:
            company.name_key = slugs.name_key(company.name)
        return super().bulk_create(objs, *args, **kwargs)

    def employers(self) -> CompanyQuerySet:
        """The companies that are employers: everything but the employment service (#202).

        The office a person is registered with is recorded as a company because it has
        contacts, addresses and postings like one, and it is not an employer; wherever
        employers are counted or offered, this is the set.
        """
        return self.filter(kind=CompanyKind.EMPLOYER)

    def qualifying(self, role: str = roles.INTERMEDIARY) -> CompanyQuerySet:
        """The companies that play a role, from what they do (#671).

        Answered from the codes of their industries (`jobs.roles`), plus the kinds that
        qualify on their own -- the public employment service is an intermediary whatever
        industries it has. An unknown role raises. Scope by owner first, as with any
        query: a company's industries are its owner's, so a set that starts from
        ``for_user`` never holds another person's company.
        """
        wanted = roles.role(role)
        coded = self.model.objects.filter(industries__code__in=wanted.divisions).values("pk")
        return self.filter(Q(kind__in=wanted.kinds) | Q(pk__in=coded))

    def with_table_data(self) -> CompanyQuerySet:
        """Annotate the counts, dates and identifiers the companies table can show, sort by
        and narrow on.

        One annotation per identifier scheme, named as the table's column is, so an
        identifier sorts and filters like a column of the company's own rather than being
        the one kind of column that could do neither (#173).

        **A correlated subquery per figure, not four aggregates over one join** (#231). The
        four used to be `Count("postings")`, `Count("postings__applications")`,
        `Count("contacts")` and `Max("postings__applications__events__occurred_at")` in a
        single `GROUP BY`, which means the database builds the cross product of all four
        branches before it counts anything: a company with four postings, five contacts and
        twelve entries per application is two hundred and forty intermediate rows, of which
        one survives. `distinct=True` on each count is what made the answers *right*; it did
        not make them cheap.

        Each figure is its own correlated subquery now -- the shape the identifier columns
        have used since #173, which is why that comment was the one to follow. Four small
        indexed lookups per row beat one cross product, they need no `distinct`, and the
        paginator's `count()` no longer runs the cross product a second time.
        """
        from django.db.models import OuterRef, Subquery

        from . import identifiers

        def counted(model, **link):
            """How many rows of ``model`` point at this company, as a scalar subquery.

            `values(1).annotate(n=Count("*"))` rather than `.count()`: the first is SQL the
            outer query carries, the second is a query per row.
            """
            return Subquery(
                model.objects.filter(**link)
                .order_by()
                .values(placeholder=Value(1))
                .annotate(n=Count("*"))
                .values("n")[:1],
                output_field=IntegerField(),
            )

        # By label rather than by import, as `tab_counts` below does: applications sit above
        # listings, and a listing's model importing theirs was a cycle (#248).
        Application = apps.get_model("applications", "Application")
        ApplicationEvent = apps.get_model("applications", "ApplicationEvent")

        annotations = {
            # `Coalesce` because a company nothing points at has no subquery row at all, and
            # the table sorts and narrows on these -- a null where a zero belongs sorts to
            # the wrong end and answers "fewer than one" with nothing.
            "posting_count": Coalesce(
                counted(JobPosting, company=OuterRef("pk")), Value(0), output_field=IntegerField()
            ),
            "application_count": Coalesce(
                counted(Application, posting__company=OuterRef("pk")),
                Value(0),
                output_field=IntegerField(),
            ),
            "contact_count": Coalesce(
                counted(Contact, company=OuterRef("pk")), Value(0), output_field=IntegerField()
            ),
            "last_activity_at": Subquery(
                ApplicationEvent.objects.filter(application__posting__company=OuterRef("pk"))
                .order_by("-occurred_at")
                .values("occurred_at")[:1]
            ),
        }
        # The columns the table has: Postulo's own schemes, as `tables.py` lists them.
        for key in identifiers.shipped():
            if key == identifiers.OTHER:
                continue
            annotations[f"id_{key}"] = Subquery(
                CompanyIdentifier.objects.filter(company=OuterRef("pk"), scheme=key)
                .order_by("pk")
                .values("value")[:1]
            )
        return self.annotate(**annotations)


class LogoSource(models.TextChoices):
    URL = "url", _("From an address")
    WEBSITE = "website", _("Found on their website")
    UPLOAD = "upload", _("Uploaded")


def logo_upload_to(instance, filename: str) -> str:
    """Under the owner, like every other file: a stray path reaches only one person."""
    return f"logos/{instance.owner_id}/{filename}"


class CompanyKind(models.TextChoices):
    """What a company is to the person recording it (#202).

    An *employer* is what every company was until there were kinds, and what every
    existing row is. A *public employment service* is the office a job seeker is
    registered with -- France Travail, IEFP, the Bundesagentur für Arbeit, Jobcentre Plus
    -- dealt with throughout a search and never applied to. The stored value stays
    ``employment_service`` (it is in the archive, the API and the CSV aliases); only the
    label says *public*, because "employment service" in the interface also names the
    companies in NACE division 78 (`jobs.roles`). A recruitment agency is not a third
    kind: an agency's listings are applied to, it is an ordinary company, and what makes it
    an intermediary is the industry it is in, not a column (#671).
    """

    EMPLOYER = "employer", _("Employer")
    EMPLOYMENT_SERVICE = "employment_service", _("Public employment service")


class LocationSource(models.TextChoices):
    """Where a company's coordinates came from: a geocode is a guess, and the record says so (#108).

    A match from the offline city dataset is a guess a person can correct; a correction
    is a fact they made, and it outlives the next save of the same location. *Unplaced*
    is the deliberate answer — "Remote" is not a place at all, and a company a person
    removed from the map is not one the next save quietly puts back.
    """

    GUESSED = "geonames", _("Matched from the city dataset")
    MANUAL = "manual", _("Set by the person recording it")
    UNPLACED = "unplaced", _("Deliberately not placed")


class Company(OwnedModel):
    """An employer, as recorded by one applicant -- or the employment service they are
    registered with, which has contacts, addresses and postings like an employer and is
    counted as one nowhere (#202).

    Companies are owner-scoped rather than shared. Two people using the same instance
    each keep their own notes on the same employer, and neither can see the other's
    opinion of them.
    """

    name = models.CharField(_("name"), max_length=200)
    #: What the unique constraint reads, kept by `save`: the name in one case and with its
    #: spacing collapsed (`slugs.name_key`), so *Émile* and *ÉMILE* are one employer on
    #: every database (#546). Three times the name's length, which casefolding can reach.
    name_key = models.CharField(max_length=600, blank=True, editable=False)
    #: A column with a default, so the migration invents nothing: every company recorded
    #: before there were kinds is an employer, which is what it was recorded as.
    kind = models.CharField(
        _("type"), max_length=20, choices=CompanyKind, default=CompanyKind.EMPLOYER
    )
    #: The company this one belongs to, if any. A tree rather than a graph — at most one
    #: parent — because that is what an ownership structure is and it keeps every question
    #: answerable in a walk rather than a search. Nothing is inherited: a subsidiary does
    #: not take its parent's industries, logo or notes, because an inheritance rule is a
    #: thing people then have to hold in their heads, and naming the parent on the page
    #: says everything the person needed to know.
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("part of"),
        help_text=_("The company this one belongs to, if it belongs to one."),
    )
    website = models.URLField(_("website"), blank=True)
    careers_url = models.URLField(
        _("careers page"), blank=True, help_text=_("Where this company lists its openings.")
    )
    location = models.CharField(_("location"), max_length=200, blank=True)
    #: Where the location points on the map, when an offline dataset places it (#108).
    #: City level, deliberately: a search at street precision is a map of where the
    #: person will be in the morning, and that is not a column to keep.
    location_lat = models.FloatField(_("latitude"), null=True, blank=True, editable=False)
    location_lon = models.FloatField(_("longitude"), null=True, blank=True, editable=False)
    location_resolved_from = models.CharField(
        _("location resolved from"), max_length=200, blank=True
    )
    location_resolved_at = models.DateTimeField(
        _("location resolved"), null=True, blank=True, editable=False
    )
    location_resolved_by = models.CharField(
        _("location resolved by"), max_length=20, choices=LocationSource, blank=True
    )
    industries = models.ManyToManyField(
        Industry, blank=True, related_name="companies", verbose_name=_("industries")
    )
    notes = models.TextField(_("notes"), blank=True)

    #: The logo, always a file of Postulo's own: fetched from an address, found on the
    #: company's site, or uploaded. Never a URL rendered into a page — that would tell
    #: somebody else's server which companies this person is looking at.
    #: A plain file field, not an ImageField: the bytes were decoded, checked and
    #: re-encoded on the way in, so there is nothing left for Django to verify, and
    #: ImageField would open the file again on every load to measure it.
    logo = models.FileField(_("logo"), upload_to=logo_upload_to, blank=True, max_length=255)
    logo_source = models.CharField(
        _("where the logo came from"), max_length=10, choices=LogoSource, blank=True
    )
    logo_source_url = models.CharField(_("logo address"), max_length=500, blank=True)
    logo_fetched_at = models.DateTimeField(_("logo fetched"), null=True, blank=True)

    objects = CompanyQuerySet.as_manager()

    class Meta:
        verbose_name = _("company")
        verbose_name_plural = _("companies")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "name_key"), name="unique_company_name_per_owner"
            )
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("jobs:company_detail", args=[self.pk])

    def save(self, *args, **kwargs) -> None:
        slugs.keep_name_key(self, kwargs)
        # A correction a person made is not a guess to be made again: the text it was
        # made from is not this location's text, so the comparison below would not
        # keep it either way.
        # Switched off (#700), nothing is placed and nothing already placed is touched:
        # the text stays unresolved, so the first save after it is back on guesses again.
        if (
            self.location_resolved_by != LocationSource.MANUAL
            and self.location != self.location_resolved_from
            and self._map_offered()
        ):
            self.apply_location_guess()
        super().save(*args, **kwargs)

    def _map_offered(self) -> bool:
        return placing_offered(self.owner)

    def apply_location_guess(self) -> None:
        """Put this location on the map from the offline dataset, or nowhere at all (#108).

        Run when the location is saved and never in the background, the rule capture
        already follows: the resolution is a guess made of a table this machine has,
        and it happens the moment its row is written. A location that resolves to
        nothing is not an error — it is a place the map does not draw, and the list
        beside the map still says where the company is. A correction a person made for
        this same text is not re-guessed, because it is not one the location would
        produce: the record keeps the text the coordinates belong to, and the same
        text produces no new guess.
        """
        from . import places

        self.location_resolved_from = self.location
        self.location_resolved_at = timezone.now()
        answer = places.resolve(self.location)
        if answer is None:
            self.location_lat = None
            self.location_lon = None
            self.location_resolved_by = ""
        else:
            self.location_lat = answer["lat"]
            self.location_lon = answer["lon"]
            self.location_resolved_by = LocationSource.GUESSED

    @property
    def is_employment_service(self) -> bool:
        return self.kind == CompanyKind.EMPLOYMENT_SERVICE

    #: How deep an ownership chain may go. Real ones are two or three; the cap exists so a
    #: mistake cannot produce a thousand-deep chain that every page then walks.
    MAX_DEPTH = 10

    def clean(self) -> None:
        """Refuse the three ways a parent can be wrong, each with the reason.

        A cycle is the one worth being careful about: the message names the company whose
        link would close the loop, because "this is not allowed" leaves somebody looking at
        a list of subsidiaries trying to work out which one.
        """
        super().clean()
        if self.parent_id is None:
            return
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": _("A company cannot be part of itself.")})

        seen, node, depth = set(), self.parent, 1
        while node is not None:
            if node.pk == self.pk:
                raise ValidationError(
                    {
                        "parent": _("That would make a loop: %(name)s is already part of this one.")
                        % {"name": self.parent.name}
                    }
                )
            if node.pk in seen:
                break
            seen.add(node.pk)
            depth += 1
            if depth > self.MAX_DEPTH:
                raise self._too_deep()
            node = node.parent
        # The company may already have companies under it: the chain is what hangs above
        # the new parent, this company, and the longest run beneath it (#532).
        if self.pk and depth + self._height_below() - 1 > self.MAX_DEPTH:
            raise self._too_deep()

    def _too_deep(self) -> ValidationError:
        return ValidationError(
            {
                "parent": _(
                    "That chain is more than %(limit)s companies deep, which is "
                    "deeper than any group Postulo can usefully draw."
                )
                % {"limit": self.MAX_DEPTH}
            }
        )

    def _height_below(self) -> int:
        """How many companies the longest run under this one holds, itself included."""
        seen = {self.pk}
        level = [self]
        height = 1
        while True:
            level = [
                child
                for node in level
                for child in node.children.all()
                if child.pk not in seen and not seen.add(child.pk)
            ]
            if not level:
                return height
            height += 1

    @property
    def group(self) -> Company:
        """The company at the top of this one's ownership chain, or itself.

        Walks up rather than querying, so a prefetched chain costs nothing, and stops at
        `MAX_DEPTH` so a cycle written directly into the database cannot hang a page.
        """
        node, seen = self, {self.pk}
        for _step in range(self.MAX_DEPTH):
            if node.parent is None or node.parent.pk in seen:
                break
            node = node.parent
            seen.add(node.pk)
        return node

    def group_members(self) -> list[Company]:
        """Every company in this one's group: the top of the chain and everything under it.

        What "the same employer" means once an employer is a tree (#138). Used to decide
        whether a department may be named on an application -- a posting at the Irish arm
        can perfectly well be for the group's engineering team, and refusing that would make
        the tree decorative.
        """
        top = self.group
        return [top, *top.descendants()]

    def descendants(self) -> list[Company]:
        """Every company under this one, breadth first, without repeating a visit."""
        found: list[Company] = []
        seen = {self.pk}
        queue = list(self.children.all())
        while queue:
            node = queue.pop(0)
            if node.pk in seen:
                continue
            seen.add(node.pk)
            found.append(node)
            queue.extend(node.children.all())
        return found

    @property
    def industry_names(self) -> str:
        """The industries as one line, for places that want text rather than labels."""
        return ", ".join(industry.name for industry in self.industries.all())

    def identifier(self, scheme: str) -> CompanyIdentifier | None:
        """The identifier under ``scheme``, from the prefetched set when there is one."""
        for identifier in self.identifiers.all():
            if identifier.scheme == scheme:
                return identifier
        return None

    @classmethod
    def by_identifier(cls, owner, scheme: str, raw: str) -> Company | None:
        """The owner's company carrying this identifier, or None.

        The value is normalised first, so a pasted URL finds what a typed id recorded.
        A malformed value simply matches nothing; it is not this method's job to complain.

        Matched without regard to case (#211), which is what finds a row written before the
        scheme folded its values -- a lowercase `q95` from an early import is the same
        Wikidata item as `Q95`, and a lookup that missed it would quietly make a second
        company for the same employer.
        """
        try:
            value = identifiers.clean(scheme, raw)
        except ValidationError:
            return None
        found = (
            CompanyIdentifier.objects.for_user(owner)
            .filter(scheme=scheme, value__iexact=value)
            .select_related("company")
            .first()
        )
        return found.company if found else None


class CompanyIdentifier(KeepsItsScheme, OwnedModel):
    """One external id for a company: a Wikidata item, a LEI, a register number, a slug.

    Unique twice over: a company has one value per scheme (except *other*, which is a
    labelled free slot), and one account cannot give two companies the same identifier —
    that would be two records of one employer, which is what identifiers exist to prevent.

    A scheme that does not identify an organisation is refused when a row is saved
    (`KeepsItsScheme.save`), as `accounts.models.PersonIdentifier` refuses a company's.
    """

    SUBJECT = COMPANY

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="identifiers", verbose_name=_("company")
    )
    scheme = scheme_field(COMPANY)
    value = models.CharField(_("identifier"), max_length=MAX_VALUE_LENGTH)
    label = models.CharField(
        _("name"),
        max_length=60,
        blank=True,
        help_text=_("What the identifier is, when the scheme is Other."),
    )

    class Meta:
        verbose_name = _("company identifier")
        verbose_name_plural = _("company identifiers")
        ordering = ("scheme", "value")
        constraints = [
            # The messages are the ones the formset uses, because a constraint checked during
            # `full_clean` lands on the form and is read by whoever is typing (#211). Without
            # them Django says "Constraint “...” is violated", which names our table, not their
            # mistake. The scheme/owner constraint below has none: it names `owner`, which is
            # never a form field, so Django skips it there and only the database ever sees it.
            models.UniqueConstraint(
                fields=("company", "scheme"),
                condition=~models.Q(scheme=identifiers.OTHER),
                name="one_identifier_per_scheme_per_company",
                violation_error_message=_("This type of identifier is already listed."),
            ),
            # **Compared without regard to case** (#211). Every named scheme folds its own
            # value -- Wikidata to upper, LinkedIn to lower -- so for those this changes
            # nothing except for rows written before the folding existed. `other` folds
            # nothing, because a staff number typed `AB-12` should read `AB-12`; that makes
            # the *comparison* the place to be case-blind rather than the stored value.
            models.UniqueConstraint(
                Lower("value"),
                "owner",
                "scheme",
                condition=~models.Q(scheme=identifiers.OTHER),
                name="one_company_per_identifier_per_owner",
            ),
            models.UniqueConstraint(
                "company",
                "scheme",
                Lower("label"),
                Lower("value"),
                name="unique_other_identifier_per_company",
                violation_error_message=_("This identifier is already listed."),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scheme_label}: {self.value}"

    def clean(self) -> None:
        if self.is_as_stored():
            # Left alone: the scheme and the value the table holds. A stored row is not
            # refused by being looked at, whatever its scheme says today; it answers the
            # day either is changed, and the page marks it until then (#311).
            pass
        elif self.keeps_an_undefined_scheme():
            # Kept under a scheme nobody defines any more: there is no shape left to hold
            # the value to, and the row stays what it was (#311).
            self.value = (self.value or "").strip()
        else:
            self.value = identifiers.clean(self.scheme, self.value)
        self.label = (self.label or "").strip()
        if self.scheme == identifiers.OTHER and not self.label:
            raise ValidationError({"label": _("Say what this identifier is.")})
        if self.scheme != identifiers.OTHER:
            self.label = ""

    @property
    def scheme_label(self) -> str:
        if self.scheme == identifiers.OTHER and self.label:
            return self.label
        return identifiers.label_for(self.scheme)

    @property
    def brand(self) -> str:
        """The brand mark Postulo ships for the row's scheme, or nothing (#654)."""
        return identifiers.brand_for(self.scheme)

    @property
    def url(self) -> str:
        """Where the identifier leads; nowhere for a value its scheme refuses today (#311)."""
        return self.link_for(identifiers.url_for(self.scheme, self.value))


class Department(OwnedModel):
    """A team inside a company: engineering, legal, the Lisbon office.

    Deliberately not a `Company` with a parent, though it would fit the shape. A department
    has no website, no logo, no identifiers, no industries and no postings of its own that
    are not also the company's; modelling it as a company would fill the companies list with
    things nobody applied to and teach every count to exclude them. More code, fewer lies.

    Both ends are optional and that is the normal case. Most contacts will never have a
    department, and a department with nobody in it is perfectly ordinary — it is a team you
    applied to before you knew anybody there.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="departments",
        verbose_name=_("company"),
    )
    name = models.CharField(_("name"), max_length=120)
    #: As on `Company`: the name the constraint compares (#546).
    name_key = models.CharField(max_length=360, blank=True, editable=False)

    class Meta:
        verbose_name = _("department")
        verbose_name_plural = _("departments")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "company", "name_key"),
                name="unique_department_name_per_company",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        slugs.keep_name_key(self, kwargs)
        super().save(*args, **kwargs)


class Contact(OwnedModel):
    """Someone at a company: a recruiter, a hiring manager, a friend on the inside."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="contacts",
        null=True,
        blank=True,
        verbose_name=_("company"),
    )
    #: The team they are in, when it is known. `SET_NULL`, not `CASCADE`: a department
    #: going away must not take the people with it — they still work at the company.
    department = models.ForeignKey(
        "Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contacts",
        verbose_name=_("department"),
    )
    name = models.CharField(_("name"), max_length=200)
    role = models.CharField(_("role"), max_length=200, blank=True)
    email = models.EmailField(_("email address"), blank=True)
    #: Deleting the holder deletes its numbers. A ``GenericRelation`` is what gives the
    #: ORM that cascade — a generic foreign key alone has no referential integrity, so
    #: without this a deleted contact would leave its telephone numbers behind, still
    #: holding their claim on the instance-wide uniqueness rule.
    phone_numbers = GenericRelation("core.PhoneNumber", verbose_name=_("telephone numbers"))
    #: And the same for postal addresses, which are the same shape and the same
    #: cascade -- deleting the holder deletes them (#92).
    postal_addresses = GenericRelation("core.PostalAddress", verbose_name=_("postal addresses"))
    #: And for the addresses on the web -- a LinkedIn was one column here until #189, and
    #: a social profile, a repository or a website is a row of one table now.
    web_links = GenericRelation("core.WebLink", verbose_name=_("web links"))
    #: And their handles on messaging services (#682).
    messaging_handles = GenericRelation("core.MessagingHandle", verbose_name=_("messaging handles"))
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("contact")
        verbose_name_plural = _("contacts")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        """A department belongs to a company, so a contact cannot borrow another's."""
        super().clean()
        if self.department_id and self.department.company_id != self.company_id:
            raise ValidationError(
                {"department": _("That department belongs to a different company.")}
            )


class DescriptionFormat(models.TextChoices):
    """How a listing's description is read (#665). Plain is the default, because what a
    capture keeps is text read off somebody's page and a line that begins with ``-`` or
    ``#`` is a line there, not markup."""

    PLAIN = "plain", _("Plain text")
    MARKDOWN = "markdown", _("Markdown")


class RemoteType(models.TextChoices):
    ONSITE = "onsite", _("On site")
    HYBRID = "hybrid", _("Hybrid")
    REMOTE = "remote", _("Remote")


class EmploymentType(models.TextChoices):
    FULL_TIME = "full_time", _("Full time")
    PART_TIME = "part_time", _("Part time")
    CONTRACT = "contract", _("Contract")
    FREELANCE = "freelance", _("Freelance")
    INTERNSHIP = "internship", _("Internship")
    APPRENTICESHIP = "apprenticeship", _("Apprenticeship")


def currency_code(value: str) -> None:
    """Three letters, upper case. Not a list of every code there is.

    The field took any three characters, so a mistyped cell became the currency a salary
    was stored in and nothing said otherwise (#224). Checking the *shape* rather than
    membership of ISO 4217 is deliberate: the list changes, a self-hosted instance cannot
    be asked to upgrade for a new one, and a code that is well formed but not current is a
    far smaller problem than a refusal to record what somebody was actually offered.
    """
    if value and not re.fullmatch(r"[A-Z]{3}", value):
        raise ValidationError(
            _("%(value)s is not a currency code. Use three letters, like EUR."),
            params={"value": value},
        )


class SalaryPeriod(models.TextChoices):
    YEAR = "year", _("Per year")
    MONTH = "month", _("Per month")
    DAY = "day", _("Per day")
    HOUR = "hour", _("Per hour")


class ListingState(models.TextChoices):
    """Where a listing stands before anyone applies to it.

    Two more states exist but are never stored, because they follow from other facts:
    *applied*, when an application exists, and *closed*, when the opening is gone or its
    closing date has passed. See :attr:`JobPosting.derived_state`.
    """

    NEW = "new", _("New")
    SHORTLISTED = "shortlisted", _("Shortlisted")
    DISCARDED = "discarded", _("Discarded")


class DiscardReason(models.TextChoices):
    NOT_FOR_ME = "not_for_me", _("Not for me")
    PAY = "pay", _("The pay")
    LOCATION = "location", _("The location")
    CLOSED = "closed", _("Closed or already filled")
    OTHER = "other", _("Other")


#: The stored states a listing can be filtered by, plus the two derived ones.
#: The tabs above the listings table, in the order they are drawn. *Everything* is not
#: here: it is the absence of a condition rather than one of them.
TAB_ORDER = ("undecided", ListingState.SHORTLISTED, ListingState.DISCARDED, "applied", "closed")


def state_condition(state: str, *, applied=None):
    """What one listing state *is*, as a condition. ``None`` for "no condition at all".

    Written once and used three ways: to filter a tab's rows, to count every tab in one
    query, and -- through `in_state` -- by the API. Three places that must agree about what
    *closed* means, and did agree only because three copies of it happened to match (#231).

    ``applied`` is how "this listing has an application" is expressed. A filtered queryset
    already annotates `application_count` and compares it; the one-query count cannot, since
    an aggregate cannot be grouped and totalled at once, so it passes an `Exists` instead.

    A decision outlives a date: a discarded listing stays *discarded* after its closing date
    passes, as `derived_state` has it, and is never counted as *closed* (#529).
    """
    has_application = applied if applied is not None else Q(application_count__gt=0)
    no_application = ~Q(has_application) if applied is not None else Q(application_count=0)
    shut = Q(closed_at__isnull=False) | Q(closes_at__lt=timezone.localdate())
    if state == "applied":
        return Q(has_application)
    if state == "closed":
        return no_application & shut & ~Q(state=ListingState.DISCARDED)
    if state == "undecided":
        return Q(state__in=(ListingState.NEW, ListingState.SHORTLISTED)) & no_application & ~shut
    if state == ListingState.DISCARDED:
        return Q(state=state) & no_application
    if state in ListingState.values:
        return Q(state=state) & no_application & ~shut
    return None


LISTING_FILTERS = (
    ListingState.NEW,
    ListingState.SHORTLISTED,
    ListingState.DISCARDED,
    "applied",
    "closed",
)


class JobPostingQuerySet(models.QuerySet):
    def for_user(self, user) -> JobPostingQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def open(self) -> JobPostingQuerySet:
        return self.filter(closed_at__isnull=True)

    def with_application_count(self) -> JobPostingQuerySet:
        """How many applications each listing has, and the first one to link to (#558).

        Correlated subqueries, as `CompanyQuerySet.with_table_data` does, not a `GROUP BY`
        over every posting column; and the application's key is annotated so a row draws its
        *Application* button without fetching the application, a query per applied row.
        """
        # By label, as `with_table_data` does: a listing's model importing theirs is a cycle.
        Application = apps.get_model("applications", "Application")
        mine = Application.objects.filter(posting=OuterRef("pk")).order_by()
        return self.annotate(
            application_count=Coalesce(
                Subquery(
                    mine.values(placeholder=Value(1)).annotate(n=Count("*")).values("n")[:1],
                    output_field=IntegerField(),
                ),
                Value(0),
                output_field=IntegerField(),
            ),
            first_application_id=Subquery(
                mine.order_by("created_at", "pk").values("pk")[:1],
                output_field=IntegerField(),
            ),
        )

    def with_salary_order(self) -> JobPostingQuerySet:
        """Annotate a yearly figure to sort the salary column by, within a currency.

        The same arithmetic the applications table does on the posting it points at (#224),
        moved to where the posting is the row. Sorting on the raw number puts an hourly rate
        below every annual one; the currency is sorted on first, because turning one into
        another needs a rate, and a wrong rate would be a number Postulo invented and then
        showed as a fact (#160).
        """
        yearly = Case(
            When(salary_period=SalaryPeriod.HOUR, then=Value(1680)),
            When(salary_period=SalaryPeriod.DAY, then=Value(220)),
            When(salary_period=SalaryPeriod.MONTH, then=Value(12)),
            default=Value(1),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        return self.annotate(
            salary_year_max=F("salary_max") * yearly,
            salary_year_min=F("salary_min") * yearly,
        )

    def with_state_order(self) -> JobPostingQuerySet:
        """Annotate the order the *State* column sorts by: the state a row shows.

        `derived_state` is a property over four facts, so a column that sorted on the stored
        `state` would order by something other than the word in the cell -- an applied
        listing still stores *new*. The same precedence, expressed once more in SQL, in the
        order the tabs above the table are in: to decide, applied, closed, discarded (#160).
        """
        return self.annotate(
            state_order=Case(
                When(application_count__gt=0, then=Value(2)),
                When(state=ListingState.DISCARDED, then=Value(4)),
                When(
                    Q(closed_at__isnull=False) | Q(closes_at__lt=timezone.localdate()),
                    then=Value(3),
                ),
                When(state=ListingState.SHORTLISTED, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    def with_table_data(self) -> JobPostingQuerySet:
        """What the listings table sorts by, over a set that has already been counted.

        Not the count itself: `undecided` and `in_state` annotate it to do their own work,
        and annotating the same name twice is an error rather than a no-op. The view picks
        its rows first and asks for this second (#160).
        """
        return self.with_salary_order().with_state_order()

    def undecided(self) -> JobPostingQuerySet:
        """New or shortlisted, not applied to, and still open: what the person must decide."""
        return self.with_application_count().filter(state_condition("undecided"))

    def closing_soon(self, days: int = 7) -> JobPostingQuerySet:
        today = timezone.localdate()
        return self.undecided().filter(
            closes_at__gte=today, closes_at__lte=today + timedelta(days=days)
        )

    def in_state(self, state: str) -> JobPostingQuerySet:
        """Filter by a stored state or one of the two derived ones."""
        annotated = self.with_application_count()
        condition = state_condition(state)
        return annotated.filter(condition) if condition is not None else annotated

    def tab_counts(self) -> dict[str, int]:
        """How many listings each tab above the table holds, in one query (#231).

        Six grouped `COUNT`s before this, each of which annotated its own
        `Count("applications", distinct=True)` and grouped by posting to do it -- six passes
        over the whole table to draw six numbers, on the page the audit calls the triage
        page because it is opened more than any other.

        One pass now, with a conditional count per tab. `Exists` rather than the grouped
        count: *has this listing any applications* is the only thing the tabs ask, and asking
        it as `COUNT(...) > 0` makes the database group before it can answer.
        """
        applied = Exists(
            apps.get_model("applications", "Application").objects.filter(posting=OuterRef("pk"))
        )
        tabs = {name: state_condition(name, applied=applied) for name in TAB_ORDER}
        counted = self.aggregate(
            all=Count("pk"),
            **{
                f"tab_{index}": Count("pk", filter=condition)
                for index, (_name, condition) in enumerate(tabs.items())
            },
        )
        answer = {name: counted[f"tab_{index}"] for index, name in enumerate(TAB_ORDER)}
        answer["all"] = counted["all"]
        return answer


class JobPosting(OwnedModel):
    """A specific opening at a company — a listing, until the person decides about it.

    Every posting arrives here first, captured or typed, and waits: new, then shortlisted
    or discarded by the person, and *applied* the moment an application exists. That last
    state is never stored; it is read from the applications, so it can never disagree
    with them.
    """

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="postings", verbose_name=_("company")
    )
    title = models.CharField(_("title"), max_length=250)
    #: The ISCO-08 unit group the title matches in the ESCO classification, or empty where
    #: it matches nothing. It follows the title rather than being set beside it, so the two
    #: cannot disagree; a posting that matches nothing has no code, and that is not a lesser
    #: kind of job (#266).
    isco_code = models.CharField(_("ISCO-08 code"), max_length=4, blank=True, editable=False)
    location = models.CharField(_("location"), max_length=200, blank=True)
    remote_type = models.CharField(
        _("working arrangement"), max_length=20, choices=RemoteType, blank=True
    )
    employment_type = models.CharField(
        _("employment type"), max_length=20, choices=EmploymentType, blank=True
    )

    url = models.URLField(_("posting URL"), blank=True, max_length=500)
    #: The address as `same_url` reduces it, kept so that "have I seen this advert?" is one
    #: indexed lookup instead of a comparison in Python over what the path narrowed to (#556).
    url_key = models.CharField(max_length=500, blank=True, editable=False)
    source = models.CharField(
        _("found via"),
        max_length=120,
        blank=True,
        help_text=_("Where you came across it: a job board, a referral, the company site."),
    )
    description = models.TextField(_("description"), blank=True)
    description_format = models.CharField(
        _("description format"),
        max_length=10,
        choices=DescriptionFormat.choices,
        default=DescriptionFormat.PLAIN,
    )

    salary_min = models.DecimalField(
        _("salary from"), max_digits=12, decimal_places=2, null=True, blank=True
    )
    salary_max = models.DecimalField(
        _("salary to"), max_digits=12, decimal_places=2, null=True, blank=True
    )
    salary_currency = models.CharField(
        _("currency"),
        max_length=3,
        blank=True,
        default="EUR",
        validators=[currency_code],
        help_text=_("A three-letter ISO 4217 code, such as EUR, GBP or USD."),
    )
    salary_period = models.CharField(
        _("salary period"),
        max_length=10,
        choices=SalaryPeriod,
        blank=True,
        default=SalaryPeriod.YEAR,
    )

    posted_at = models.DateField(_("posted on"), null=True, blank=True)
    closes_at = models.DateField(
        _("closing date"),
        null=True,
        blank=True,
        help_text=_("The application deadline, if stated."),
    )
    #: The closing date this posting's *closes soon* message was sent for, or empty where
    #: nothing has been sent (#238). The date rather than a moment, so that moving the
    #: closing date announces it again -- a deadline being brought forward is news, and a
    #: stamp that only said "told them once" would swallow it.
    closing_announced_for = models.DateField(
        _("closing announced for"), null=True, blank=True, editable=False
    )
    closed_at = models.DateTimeField(
        _("closed on"),
        null=True,
        blank=True,
        help_text=_("Set when the opening is no longer available."),
    )

    state = models.CharField(
        _("state"), max_length=20, choices=ListingState, default=ListingState.NEW
    )
    discard_reason = models.CharField(
        _("why it was discarded"), max_length=20, choices=DiscardReason, blank=True
    )
    noted_at = models.DateTimeField(
        _("noted on"), default=timezone.now, help_text=_("When it entered your listings.")
    )
    decided_at = models.DateTimeField(_("decided on"), null=True, blank=True)

    objects = JobPostingQuerySet.as_manager()

    class Meta:
        verbose_name = _("job posting")
        verbose_name_plural = _("job postings")
        ordering = ("-noted_at", "-created_at")
        indexes = [
            models.Index(fields=("owner", "state")),
            models.Index(fields=("owner", "url_key"), name="jobposting_owner_url_key"),
        ]

    def __str__(self) -> str:
        return f"{self.title} — {self.company.name}"

    def save(self, *args, **kwargs):
        self.url_key = same_url(self.url)
        update = kwargs.get("update_fields")
        if update is not None and "url" in update and "url_key" not in update:
            kwargs["update_fields"] = [*update, "url_key"]
        # Upper-cased rather than refused: "eur" is the code, typed the way people type.
        # The validator then has only one shape to judge (#224).
        self.salary_currency = (self.salary_currency or "").strip().upper()
        update = kwargs.get("update_fields")
        if update is None or "title" in update:
            # The code follows the title, in the language being read (#266): derived
            # whenever the title may be written, and carried along then, whatever else the
            # save meant to write.
            self.isco_code = esco.code_for(self.title)
            if update is not None and "isco_code" not in update:
                kwargs["update_fields"] = [*update, "isco_code"]
        return super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("jobs:posting_detail", args=[self.pk])

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def is_past_closing(self) -> bool:
        return self.closes_at is not None and self.closes_at < timezone.localdate()

    @property
    def isco_name(self) -> str:
        """What the classification calls the code this posting carries, in the language read."""
        return esco.name_for(self.isco_code)

    @property
    def has_applications(self) -> bool:
        count = getattr(self, "application_count", None)
        if count is None:
            count = self.applications.count()
        return count > 0

    @property
    def is_markdown(self) -> bool:
        return self.description_format == DescriptionFormat.MARKDOWN

    @property
    def description_html(self):
        """The description as sanitised markup, for the one page that shows it as such.
        Plain text is not routed through here: the template prints it escaped (#665)."""
        from postulo.core import markdown

        return markdown.render(self.description)

    @property
    def description_text(self) -> str:
        """The description as words, for a table cell or a search excerpt: what Markdown
        marks up is left out, and plain text is as it was typed (#665)."""
        if not self.is_markdown:
            return self.description
        from postulo.core import markdown

        return markdown.plain(self.description)

    @property
    def derived_state(self) -> str:
        """What the listing is, all things considered: the stored state, or one it earned."""
        if self.has_applications:
            return "applied"
        if self.state == ListingState.DISCARDED:
            return ListingState.DISCARDED
        if not self.is_open or self.is_past_closing:
            return "closed"
        return self.state

    @property
    def derived_state_label(self) -> str:
        labels = {**dict(ListingState.choices), "applied": _("Applied"), "closed": _("Closed")}
        return str(labels[self.derived_state])

    @property
    def is_undecided(self) -> bool:
        return self.derived_state in (ListingState.NEW, ListingState.SHORTLISTED)

    def shortlist(self) -> bool:
        """Shortlist it; False, and nothing touched, when it already was."""
        if self.state == ListingState.SHORTLISTED:
            return False
        self.state = ListingState.SHORTLISTED
        self.discard_reason = ""
        self.decided_at = timezone.now()
        self.save(update_fields=["state", "discard_reason", "decided_at", "updated_at"])
        return True

    def discard(self, reason: str = "") -> bool:
        """Discard it; False, and the date kept, when it already was, for the same reason."""
        if self.state == ListingState.DISCARDED and self.discard_reason == reason:
            return False
        self.state = ListingState.DISCARDED
        self.discard_reason = reason
        self.decided_at = timezone.now()
        self.save(update_fields=["state", "discard_reason", "decided_at", "updated_at"])
        return True

    def restore(self) -> bool:
        """Back to new, as if the decision had not been made; False when it was new already."""
        if self.state == ListingState.NEW:
            return False
        self.state = ListingState.NEW
        self.discard_reason = ""
        self.decided_at = None
        self.save(update_fields=["state", "discard_reason", "decided_at", "updated_at"])
        return True

    def close(self) -> None:
        if self.closed_at is None:
            self.closed_at = timezone.now()
            self.save(update_fields=["closed_at", "updated_at"])

    @property
    def salary_display(self) -> str:
        """A readable salary range, or an empty string when nothing is known.

        Grouping is left to Django's locale machinery rather than hard-coded, so a
        French reader sees 65 000 where a British one sees 65,000.
        """
        if self.salary_min is None and self.salary_max is None:
            return ""

        def amount(value) -> str:
            return number_format(value, decimal_pos=0, use_l10n=True, force_grouping=True)

        if self.salary_min is not None and self.salary_max is not None:
            figure = _("%(low)s to %(high)s") % {
                "low": amount(self.salary_min),
                "high": amount(self.salary_max),
            }
        elif self.salary_min is not None:
            figure = _("from %(low)s") % {"low": amount(self.salary_min)}
        else:
            figure = _("up to %(high)s") % {"high": amount(self.salary_max)}

        shown = f"{figure} {self.salary_currency}".strip()
        # The period belongs in the sentence: 30 and 40 with no period reads as a year's
        # pay, and an hourly rate shown that way is wrong by a factor of about two thousand
        # (#224). A yearly figure says so too, because saying nothing is what caused this.
        period = self.get_salary_period_display() if self.salary_period else ""
        return f"{shown} {period}".strip() if period else shown


class CaptureStatus(models.TextChoices):
    PENDING = "pending", _("Waiting for review")
    ACCEPTED = "accepted", _("Turned into an application")
    DISCARDED = "discarded", _("Discarded")


class Capture(OwnedModel):
    """A posting read off a page, waiting for a person to confirm it.

    A capture is never an application. Parsing somebody else's markup is guesswork often
    enough that turning the result straight into a record would put invented job titles
    into the one place they must not be. Everything captured waits here until it has been
    looked at.

    The parsed fields are kept as JSON rather than as columns because they are not the
    record — they are a suggestion for a form, and the shape belongs to
    :class:`~postulo.plugins.base.JobPostingData`, which validates them on the way in and
    on the way out.
    """

    url = models.URLField(_("address"), max_length=500, blank=True)
    #: The address as `same_url` reduces it (#556), as on a listing.
    url_key = models.CharField(max_length=500, blank=True, editable=False)
    source_name = models.CharField(_("read by"), max_length=60, blank=True)
    source_version = models.CharField(_("source version"), max_length=20, blank=True)
    origin = models.CharField(
        _("captured from"),
        max_length=20,
        default="web",
        help_text=_("Which part of Postulo produced this: the web interface, or the API."),
    )
    data = models.JSONField(_("parsed posting"), default=dict)
    status = models.CharField(
        _("status"), max_length=20, choices=CaptureStatus, default=CaptureStatus.PENDING
    )
    posting = models.ForeignKey(
        JobPosting,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captures",
        verbose_name=_("listing"),
        help_text=_("The listing this capture became, once reviewed."),
    )
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captures",
        verbose_name=_("application"),
    )
    #: What the review of this capture learns from, and nothing once it is decided (#267):
    #: which of the owner's remembered places filled which field, and -- only where the
    #: page's source was not kept (`CapturedPage`) -- the bounded list of the page's places
    #: and digests `jobs.remembered` recognises a correction in. Never the page's text.
    learning = models.JSONField(
        _("what the review learns from"), default=dict, blank=True, editable=False
    )

    class Meta:
        verbose_name = _("capture")
        verbose_name_plural = _("captures")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("owner", "status")),
            models.Index(fields=("owner", "url_key"), name="capture_owner_url_key"),
        ]

    def save(self, *args, **kwargs):
        self.url_key = same_url(self.url)
        update = kwargs.get("update_fields")
        if update is not None and "url" in update and "url_key" not in update:
            kwargs["update_fields"] = [*update, "url_key"]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.data.get("title") or self.url or _("Empty capture")

    def get_absolute_url(self) -> str:
        return reverse("jobs:capture_review", args=[self.pk])

    @property
    def posting_data(self):
        """The parsed posting, validated again on the way out.

        Stored JSON can outlive the schema that wrote it. Validating on read means a
        capture saved by an older version shows up as a clear error rather than as
        subtly wrong values in a form somebody is about to accept.
        """
        from postulo.plugins.base import JobPostingData

        return JobPostingData(**self.data)

    @property
    def is_pending(self) -> bool:
        return self.status == CaptureStatus.PENDING

    @property
    def kept_page(self):
        """What was kept of the page this was read from, or ``None`` (#256).

        A reverse one-to-one raises where there is no row, and nearly every capture has
        none -- keeping the page is off until an administrator and the person have both
        said otherwise -- so the ordinary case is asked for here rather than caught at
        every call site.
        """
        try:
            return self.page
        except CapturedPage.DoesNotExist:
            return None


def page_upload_to(instance, filename: str) -> str:
    """Under the owner, like every other file: a stray path reaches only one person."""
    return f"captures/{instance.owner_id}/{timezone.now():%Y/%m}/{filename}"


class RenderingKind(models.TextChoices):
    """What a rendering may be, and the whole of it (#256).

    Four formats a browser draws without running anything that came inside them. **Not
    HTML and not SVG**: both are documents, and a stranger's document drawn from this
    instance is that stranger's code running as the person who opened it. The value is a
    media type because that is what the file is answered with, and it is always one of
    these -- never the header a client sent.
    """

    PNG = "image/png", _("PNG image")
    JPEG = "image/jpeg", _("JPEG image")
    WEBP = "image/webp", _("WebP image")
    PDF = "application/pdf", _("PDF")


class RenderedBy(models.TextChoices):
    """Who drew the rendering, which is also what it can be taken as evidence of.

    A picture taken in the person's browser is the page as they were seeing it: styled,
    with its images, and with whatever the site showed them because they were signed in. A
    rendering drawn here is the kept source laid out with scripts, the network and every
    outside resource switched off -- the words of the page, and none of its looks.
    """

    CLIENT = "client", _("Sent with the capture, by the browser that was looking at the page")
    INSTANCE = "instance", _("Drawn by this instance from the kept source")


class CapturedPage(OwnedModel):
    """What a capture kept of the page it was read from (#256).

    Two things, both optional and each refusable on its own: the **source**, exactly as it
    was parsed, and a **rendering** of the whole page. The parsed fields on a `Capture` are
    a reading; this is what they were read from, kept for the day the advert is gone and
    the reading is all that would otherwise be left of it.

    A model of its own rather than columns on `Capture`. Almost no capture has one, the
    review queue reads captures by the dozen and has no use for file columns, and what was
    kept can be thrown away without touching the capture -- which is a deletion of this row
    and of nothing else.

    **The source is a stranger's markup and is never served as a page.** It is kept
    gzipped under a name ending `.txt.gz`, so that nothing reading the media directory by
    mistake would call it HTML, shown as text, and downloaded as ``text/plain``. The
    rendering is an image or a PDF; its media type comes from `RenderingKind` and its bytes
    were checked against that type before they were stored.

    Files are removed with the row: `jobs.signals` does for these what #217 did for
    documents.
    """

    capture = models.OneToOneField(
        Capture,
        on_delete=models.CASCADE,
        related_name="page",
        verbose_name=_("capture"),
    )
    source = models.FileField(_("source"), upload_to=page_upload_to, blank=True, max_length=255)
    #: How long the source is as text, in bytes of UTF-8 -- what a download weighs.
    source_size = models.PositiveIntegerField(_("size of the source"), default=0)
    #: What it weighs on disk, gzipped: the number the account's allowance counts.
    source_stored = models.PositiveIntegerField(_("size of the source on disk"), default=0)
    #: SHA-256 of the text. A rendering drawn from the source months later is evidence of
    #: what the page said only if the source is the one that was captured, and this is how
    #: anybody can tell.
    source_checksum = models.CharField(_("checksum of the source"), max_length=64, blank=True)

    rendering = models.FileField(
        _("rendering"), upload_to=page_upload_to, blank=True, max_length=255
    )
    rendering_type = models.CharField(
        _("type of rendering"), max_length=20, choices=RenderingKind, blank=True
    )
    rendering_size = models.PositiveIntegerField(_("size of the rendering"), default=0)
    rendering_checksum = models.CharField(_("checksum of the rendering"), max_length=64, blank=True)
    rendered_by = models.CharField(_("drawn by"), max_length=10, choices=RenderedBy, blank=True)

    class Meta:
        verbose_name = _("captured page")
        verbose_name_plural = _("captured pages")
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return str(self.capture)

    def get_absolute_url(self) -> str:
        return reverse("jobs:capture_page", args=[self.capture_id])

    @property
    def has_source(self) -> bool:
        return bool(self.source)

    @property
    def has_rendering(self) -> bool:
        return bool(self.rendering)

    @property
    def weight(self) -> int:
        """What this row's files weigh on disk, together."""
        return (self.source_stored if self.source else 0) + (
            self.rendering_size if self.rendering else 0
        )

    @property
    def rendering_is_a_picture(self) -> bool:
        """Whether a page can draw it in an ``<img>``; a PDF is opened instead."""
        return self.rendering_type in (
            RenderingKind.PNG,
            RenderingKind.JPEG,
            RenderingKind.WEBP,
        )


class ListingEventKind(models.TextChoices):
    """What arrived about a listing, or was written down about it (#270).

    The first four are the timeline's own words, with the same values and the same labels
    as `applications.models.EventKind`, because they mean the same thing on either side of
    applying: a note is a note, and an email received is one whether or not an application
    exists yet. The rest are what a listing needs and an application never did -- a message
    from the employment service, a document somebody forwarded, and the same advert read off
    another board. `tests/test_listing_history.py` holds the shared four to the timeline's.

    Written out rather than imported: `applications` depends on `jobs`, and a vocabulary
    borrowed the other way would close the loop.
    """

    NOTE = "note", _("Note")
    EMAIL_RECEIVED = "email_received", _("Email received")
    CALL = "call", _("Call")
    MESSAGE = "message", _("Message")
    DOCUMENT = "document", _("Document")
    CAPTURE = "capture", _("Capture")
    OTHER = "other", _("Other")


#: Kinds the record writes for itself. A capture entry is what binding a capture writes,
#: pointing at that capture, and offering it to be typed would let the history name a
#: capture that never happened.
SYSTEM_LISTING_EVENT_KINDS = frozenset({ListingEventKind.CAPTURE})


class ListingEventQuerySet(models.QuerySet):
    def for_user(self, user) -> ListingEventQuerySet:
        """Scope through the listing, as the timeline scopes through its application.

        An entry carries no owner of its own, for the reason `ApplicationEvent` gives:
        duplicating it would be a second source of truth that could drift out of step with
        the listing it belongs to.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(posting__owner=user)


class ListingEvent(models.Model):
    """One thing that arrived about a listing, or was written down about it (#270).

    An application has had a timeline from the start; a listing had nothing, so everything
    that arrives about a job before somebody applies for it -- the counsellor's message, a
    description a contact forwarded, the board's reminder that it closes on Friday, the same
    advert captured again from another board -- was kept somewhere else or lost. This is the
    listing's history, append-only in the way the timeline is: nothing offers to change an
    entry, because a history you can quietly rewrite is worth very little.

    **A second log rather than a second parent for the first.** `ApplicationEvent` scopes
    through `application__owner` and nothing else; giving it a listing as a second possible
    parent would mean scoping through either, which is cheap to write and expensive to get
    wrong. When a listing becomes an application nothing is carried over: the application's
    page reads this history through its posting, first, and its own timeline after it. One
    history in two parts, stored once, so nothing can drift.

    **An entry may point at something stored where it belongs** -- a `Capture`, or a file in
    the person's documents -- through a generic link with **no** reverse relation, for the
    reason `documents.RenderedDocument.source` has none: a reverse relation would give a
    cascade. Deleting or discarding a listing must not delete a job description that lives in
    the documents; deleting that file leaves the entry standing with the words it had, and
    `jobs.signals` clears the link. A note is the entry's own text, and goes with the
    listing. This is not a file manager: a binding points, and stores nothing of its own.

    **Bound text is foreign text** (#218). A message or an email body was written by a
    stranger, so it is bounded on the way in (`jobs.history`), escaped on the page, never
    interpreted, and reaches no spreadsheet and no calendar.
    """

    posting = models.ForeignKey(
        JobPosting, on_delete=models.CASCADE, related_name="events", verbose_name=_("listing")
    )
    kind = models.CharField(
        _("type"), max_length=20, choices=ListingEventKind, default=ListingEventKind.NOTE
    )
    occurred_at = models.DateTimeField(_("happened on"), default=timezone.now, db_index=True)
    summary = models.CharField(_("summary"), max_length=250, blank=True)
    body = models.TextField(_("details"), blank=True)
    #: Who it came from, where that is somebody the person has recorded: the counsellor, the
    #: contact who forwarded the advert. `SET_NULL`, as an application's contact is -- the
    #: person going must not take what they said with them -- and it is what puts the entry
    #: in that person's own document under data protection (`core.gdpr`).
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="listing_events",
        verbose_name=_("from"),
    )
    #: What the entry points at, when it points at anything: a `Capture` or an
    #: `UploadedDocument`, and only one of the listing's owner's (`jobs.history.ARTEFACTS`).
    artefact_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("type of thing it points at"),
    )
    artefact_id = models.PositiveBigIntegerField(null=True, blank=True)
    artefact = GenericForeignKey("artefact_type", "artefact_id")
    #: What the source calls the thing it bound: a message id, say. Given one, binding is
    #: idempotent for that listing, which is what lets a mailbox be read every five minutes
    #: without the same message landing in a history twice -- `suggest`'s rule.
    external_id = models.CharField(_("identifier at the source"), max_length=250, blank=True)
    #: Who wrote it when it was not the person at the keyboard: "API token Thunderbird", or
    #: a plugin's name. Blank means the person themselves.
    actor = models.CharField(_("recorded by"), max_length=120, blank=True)
    created_at = models.DateTimeField(_("recorded on"), auto_now_add=True)

    objects = ListingEventQuerySet.as_manager()

    class Meta:
        verbose_name = _("listing entry")
        verbose_name_plural = _("listing entries")
        ordering = ("-occurred_at", "-pk")
        indexes = [
            # A listing's history, newest first, which both pages that draw it ask for.
            models.Index(fields=("posting", "-occurred_at"), name="listing_event_latest"),
            # Every entry pointing at one thing, which deleting that thing asks.
            models.Index(fields=("artefact_type", "artefact_id"), name="listing_event_artefact"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("posting", "external_id"),
                condition=~models.Q(external_id=""),
                name="listing_event_once_per_source_id",
            ),
        ]

    def __str__(self) -> str:
        return self.summary or str(self.get_kind_display())

    @property
    def points_at(self):
        """What the entry points at, when it is still there and still this owner's.

        The link is checked when it is made, so this second look is for a row the database
        was handed some other way: an entry never draws another person's record, whatever
        its columns say.
        """
        found = self.artefact if self.artefact_type_id and self.artefact_id else None
        if found is None or getattr(found, "owner_id", None) != self.posting.owner_id:
            return None
        return found

    @property
    def bound_capture(self) -> Capture | None:
        """The capture this entry points at, for the page to link to its advert and page."""
        found = self.points_at
        return found if isinstance(found, Capture) else None

    @property
    def advert_address(self) -> str:
        """Where the bound capture was read, when that is an address a browser should follow.

        A capture's address was held to http and https on the way in (#218), and it is held
        there again here, because this is drawn as a link and a stored `javascript:`
        address is the one kind of text that acts when somebody follows it.
        """
        from postulo.core.addresses import page_address

        capture = self.bound_capture
        if capture is None:
            return ""
        try:
            return page_address(capture.url)
        except ValueError:
            return ""

    @property
    def bound_document(self):
        """The file in the person's documents this entry points at, or nothing.

        Recognised by its label rather than by `isinstance`, because `documents` is not
        this module's to import.
        """
        found = self.points_at
        if found is not None and found._meta.label_lower == "documents.uploadeddocument":
            return found
        return None


class HintField(models.TextChoices):
    """What a place on a page can be remembered for (#267).

    The fields the review screen shows and a page can be read for. The pay is one: a place
    that holds it holds the figures, the currency and the period together. Not the working
    arrangement, which has no vocabulary to read a place's words against, and not the date
    of posting, which the review screen does not ask about.
    """

    TITLE = "title", _("Job title")
    COMPANY = "company_name", _("Company")
    LOCATION = "location", _("Location")
    EMPLOYMENT_TYPE = "employment_type", _("Employment type")
    SALARY = "salary", _("Salary")
    CLOSES_AT = "closes_at", _("Closing date")
    DESCRIPTION = "description", _("Description")


class FieldHint(OwnedModel):
    """Where one person's own corrections showed one field to be, on one site (#267).

    Learned on review: somebody corrected a field, and the value they typed was on the page
    the capture was read from, at a place that survives a small change to the page -- an
    id, a label, a heading under the page's main landmark (`plugins.builtin.hints`). The
    next capture from the same site reads that place before what the page says about
    itself, and the review screen says which fields came from one.

    **One person's, never shared.** Scoped by owner like everything else, in their archive,
    and gone with their account. There is no registry of places and nothing reported
    anywhere: a place is learned from one person's corrections and used for their captures.

    **It decays.** ``misses`` counts the reviews in a row at which the place was wrong --
    the person changed what it found, or typed a value it had not found -- and a place that
    reaches `DROPPED_AFTER` is deleted: a site redesign costs one poor capture rather than a
    wrong answer for ever.
    """

    #: How many reviews in a row a place may be wrong before it is forgotten.
    DROPPED_AFTER = 2

    host = models.CharField(_("site"), max_length=253)
    field = models.CharField(_("field"), max_length=20, choices=HintField)
    #: The shape `plugins.builtin.hints.clean` accepts, and only that: checked on the way in
    #: and again whenever it is read.
    place = models.JSONField(_("place on the page"), default=dict)
    misses = models.PositiveSmallIntegerField(_("wrong in a row"), default=0)

    class Meta:
        verbose_name = _("remembered place")
        verbose_name_plural = _("remembered places")
        ordering = ("host", "field")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "host", "field"), name="one_place_per_field_per_site"
            )
        ]

    def __str__(self) -> str:
        return f"{self.host}: {self.get_field_display()}"
