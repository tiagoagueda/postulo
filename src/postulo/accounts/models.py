"""The user model, personal profiles, and invitations.

The user model is defined in the very first migration on purpose: swapping
``AUTH_USER_MODEL`` after a database exists is painful, and Postulo is multi-user from
day one.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.identifiers import MAX_VALUE_LENGTH, PERSON, KeepsItsScheme, scheme_field
from postulo.core.language_field import LanguageField
from postulo.core.personal import (
    BIRTH_DATE_LENGTH,
    BIRTH_PLACE_LENGTH,
    SCOPE_CHOICES,
    validate_birth_date,
    validate_country_code,
    validate_nationalities,
)
from postulo.core.stored_pictures import StoredPicture

from . import identifiers
from .validators import USERNAME_MAX_LENGTH, slug_from_email, username_validator

INVITE_TOKEN_BYTES = 32
#: How long a form of address or a set of pronouns may be (#309): well beyond anything
#: `accounts.addressing` lists, with room for a string of them ("Prof. Dr. Eng.ª") or a set
#: of pronouns written out, and bounded because it is a column.
ADDRESSING_MAX_LENGTH = 40
DEFAULT_INVITE_VALIDITY = timedelta(days=14)


def vouch_for_address(user) -> None:
    """Mark ``user.email`` verified and the account's one primary address.

    For an operator at the console, who vouches for the address by typing it. Any other
    primary row is cleared, so the account never ends with two.
    """
    from allauth.account.models import EmailAddress

    EmailAddress.objects.filter(user=user, primary=True).exclude(email__iexact=user.email).update(
        primary=False
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email__iexact=user.email,
        defaults={"email": user.email, "verified": True, "primary": True},
    )


def unique_username(candidate: str, taken) -> str:
    """``candidate``, or ``candidate`` with the smallest numeric suffix not in ``taken``.

    ``taken`` is any callable answering whether a username exists, so the same rule
    serves the manager, the importer and the migration that gives existing accounts
    their first username.
    """
    if not taken(candidate):
        return candidate
    for suffix in range(2, 10_000):
        stem = candidate[: USERNAME_MAX_LENGTH - len(str(suffix))]
        attempt = f"{stem}{suffix}"
        if not taken(attempt):
            return attempt
    raise ValueError(f"No free username near {candidate!r}.")


class UserManager(DjangoUserManager):
    """Manager for a user known by a username, reached by an email address.

    Both are obligatory. Code that creates accounts — tests, the seeder, an import — may
    leave the username out, in which case it is derived from the address; a person
    signing up chooses it.
    """

    def _create_user(self, email, password, **extra_fields):  # type: ignore[override]
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        username = (extra_fields.pop("username", "") or "").strip().casefold()
        if not username:
            username = unique_username(
                slug_from_email(email),
                lambda name: self.model._default_manager.filter(username=name).exists(),
            )
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):  # type: ignore[override]
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email=None, password=None, **extra_fields):  # type: ignore[override]
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        user = self._create_user(email, password, **extra_fields)
        # The operator typed this address at the console of their own server. That is
        # all the proof there is going to be, and without it the first account could not
        # sign in until email delivery worked — which is the thing it would be signing in
        # to configure.
        vouch_for_address(user)
        return user


class User(AbstractUser):
    """A person applying for jobs.

    Known by a username, which is what they sign in with and what others on a shared
    instance see; reached by an email address, which is unique too and works for signing
    in as well. Both are obligatory, as is a full name, because a job search is conducted
    under one's own name and every document starts from it.
    """

    username = models.CharField(
        _("username"),
        max_length=USERNAME_MAX_LENGTH,
        unique=True,
        validators=[username_validator],
        help_text=_("Lowercase letters, digits, dots, underscores and hyphens."),
        error_messages={"unique": _("Somebody already has that username.")},
    )
    email = models.EmailField(_("email address"), unique=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["email", "first_name", "last_name"]

    objects = UserManager()  # type: ignore[misc,assignment]

    class Meta(AbstractUser.Meta):  # type: ignore[name-defined]
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return self.username

    def save(self, *args, **kwargs) -> None:
        # One spelling per person: "Alex" and "alex" must never be two accounts.
        self.username = (self.username or "").strip().casefold()
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        """The full name, or the username while an account has none yet."""
        return self.get_full_name() or self.username


class Theme(models.TextChoices):
    SYSTEM = "system", _("Match the operating system")
    LIGHT = "light", _("Light")
    DARK = "dark", _("Dark")


def upload_to_avatars(instance, filename: str) -> str:
    """Pictures live under the person, like every other private file."""
    return f"avatars/{instance.user_id}/{filename}"


class PersonIdentifier(KeepsItsScheme, models.Model):
    """One external identifier for the person whose account this is.

    A name is not an identity. Two researchers share one, one researcher publishes under
    three, and a marriage or a transliteration turns one into another — which is the whole
    reason ORCID exists, and the reason an application form for an academic post asks for
    it by name.

    Attached to the profile rather than to a CV, because it is a fact about the person and
    not about one variant of their career record. Which CVs show it is a separate choice,
    made the same way the rest of the contact block is.

    A scheme that does not identify a person is refused when a row is saved, by every route
    in (`KeepsItsScheme.save`). A row already stored under a scheme this instance defined
    and has since deleted is the one thing let through, and it is kept as it is (#311).
    """

    SUBJECT = PERSON

    profile = models.ForeignKey(
        "accounts.Profile",
        on_delete=models.CASCADE,
        related_name="identifiers",
        verbose_name=_("profile"),
    )
    scheme = scheme_field(PERSON)
    value = models.CharField(_("identifier"), max_length=MAX_VALUE_LENGTH)
    label = models.CharField(
        _("name"),
        max_length=60,
        blank=True,
        help_text=_("What the identifier is, when the scheme is Other."),
    )

    class Meta:
        verbose_name = _("identifier")
        verbose_name_plural = _("identifiers")
        ordering = ("scheme", "value")
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "scheme"),
                condition=~models.Q(scheme=identifiers.OTHER),
                name="one_identifier_per_scheme_per_person",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scheme_label}: {self.value}"

    def clean(self) -> None:
        """Tidy and check the value here, so nothing stores a half-typed identifier.

        On the model rather than only on the form, because an import or a plugin writing
        one should get the same answer as somebody typing it into a page. Two rows are left
        as they are (#311): one whose scheme and value are what the table holds, which is
        not refused by being looked at whatever its scheme says today, and one kept under
        a scheme nobody defines any more, which has no shape left to be held to.
        """
        super().clean()
        if self.is_as_stored():
            pass
        elif self.keeps_an_undefined_scheme():
            self.value = (self.value or "").strip()
        elif self.scheme and self.value:
            self.value = identifiers.clean(self.scheme, self.value)

    @property
    def scheme_label(self) -> str:
        return identifiers.label_for(self.scheme)

    @property
    def brand(self) -> str:
        """The brand mark Postulo ships for the row's scheme, or nothing (#654)."""
        return identifiers.brand_for(self.scheme)

    @property
    def url(self) -> str:
        """Where the identifier leads; nowhere for a value its scheme refuses today (#311)."""
        return self.link_for(identifiers.url_for(self.scheme, self.value))

    @property
    def display_label(self) -> str:
        return (
            self.label
            if self.scheme == identifiers.OTHER and self.label
            else identifiers.label_for(self.scheme)
        )


class Profile(models.Model):
    """Personal details and preferences.

    The contact block is kept here rather than on the user because it is *content*: it
    is what gets rendered onto a CV, and it changes for reasons that have nothing to do
    with authentication.

    Neither ``language`` nor ``time_zone`` declares model-level choices. Doing so would
    write hundreds of time zone names into a migration and produce a fresh migration
    every time the IANA database or the language list changed. The choices belong to
    the form, which is where they are actually needed.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("user"),
    )

    #: What is written before the name -- Mr, Mme, *Eng.ª* -- and how to refer to the person
    #: -- she/her, *iel* -- as two answers, because they are two questions (#309). Each is
    #: the text itself rather than a key into `accounts.addressing`'s lists, so it survives
    #: a change of language and an *Other* is just text. Optional, never worked out from
    #: anything, and nothing is assumed while blank. Neither is printed anywhere unless a
    #: CV says so: each CV chooses which of these details it carries, and both of these
    #: start off (#308, `documents.printing`).
    form_of_address = models.CharField(
        _("form of address"), max_length=ADDRESSING_MAX_LENGTH, blank=True
    )
    pronouns = models.CharField(_("pronouns"), max_length=ADDRESSING_MAX_LENGTH, blank=True)
    #: Gender in the person's own words (#681), stored as the text itself exactly as the form
    #: of address is: a listed word, or whatever they typed. Optional, never worked out from a
    #: name, an address or any other field, independent of the two above, and printed only on
    #: a CV that says so. There is none on a contact, which would record something about
    #: another person that nobody asked to be recorded.
    gender = models.CharField(_("gender"), max_length=ADDRESSING_MAX_LENGTH, blank=True)
    #: When and where the person was born (#679). Optional, never worked out from anything,
    #: and printed nowhere unless a CV says so. The date is the text of an ISO 8601 reduced
    #: form -- a year, a year and month, or a whole date -- held to `core.personal`'s rule by
    #: this validator, which the form, the API, the candidate file and the archive importer
    #: all run through; the place is what was typed and a country's code, with no lookup and
    #: nothing for the map. An age is never computed, stored or shown.
    birth_date = models.CharField(
        _("date of birth"),
        max_length=BIRTH_DATE_LENGTH,
        blank=True,
        validators=[validate_birth_date],
    )
    birth_place = models.CharField(_("place of birth"), max_length=BIRTH_PLACE_LENGTH, blank=True)
    birth_country = models.CharField(
        _("country of birth"), max_length=2, blank=True, validators=[validate_country_code]
    )
    #: What the person is a citizen of (#680): a list of country codes from `core.phones`'s
    #: table, in the order given, each once, at most ten, held to `core.personal`'s rule at
    #: every door. Optional, never worked out from anything, printed only where a CV says so.
    #: A list rather than rows: a nationality has no label, kind or primary, so a table of its
    #: own would be a model and a migration for a list of codes, as the navigation lists are
    #: not.
    nationalities = models.JSONField(
        _("nationalities"), default=list, blank=True, validators=[validate_nationalities]
    )
    #: For somebody who would rather say less: only whether they are a citizen of the EU, the
    #: EEA or Switzerland, or of another country. The answer only while the list above is
    #: empty -- with countries listed the scope is worked out from them (`personal.derived_scope`)
    #: and this is cleared on save, so the two can never disagree.
    nationality_scope = models.CharField(
        _("nationality scope"), max_length=5, blank=True, choices=SCOPE_CHOICES
    )
    headline = models.CharField(
        _("headline"),
        max_length=200,
        blank=True,
        help_text=_("A short professional title, such as “Backend engineer”."),
    )
    #: Deleting the holder deletes its numbers. A ``GenericRelation`` is what gives the
    #: ORM that cascade — a generic foreign key alone has no referential integrity, so
    #: without this a deleted profile would leave its telephone numbers behind, still
    #: holding their claim on the instance-wide uniqueness rule.
    phone_numbers = GenericRelation("core.PhoneNumber", verbose_name=_("telephone numbers"))
    #: And the same for postal addresses, which are the same shape and the same
    #: cascade -- deleting the holder deletes them (#92).
    postal_addresses = GenericRelation("core.PostalAddress", verbose_name=_("postal addresses"))
    #: And for the addresses on the web -- social profiles, repositories, websites -- which
    #: were three single columns here until #189 and are rows of one table now.
    web_links = GenericRelation("core.WebLink", verbose_name=_("web links"))
    #: And for how somebody is reached on a messaging service (#682).
    messaging_handles = GenericRelation("core.MessagingHandle", verbose_name=_("messaging handles"))
    #: What was typed, and only that. Blank means the town and country of the primary
    #: postal address, worked out whenever it is printed rather than copied in here, so a
    #: new primary address moves it without this being saved again (#309). Everything that
    #: prints or shows where somebody is asks `core.postal.printed_location`, which is that
    #: rule written once.
    location = models.CharField(
        _("location"),
        max_length=120,
        blank=True,
        help_text=_("City and country, as it should appear on a CV."),
    )
    language = LanguageField(_("language"), blank=True)
    #: Which language the career record itself is written in -- the job titles, the
    #: summaries, the highlights -- as opposed to `language`, which is the interface. The
    #: two are often different: somebody reading Postulo in English may have typed their
    #: career in Portuguese, and a CV declaring `pt-PT` then needs no translations at all.
    #:
    #: Blank means "the same as the interface", which is the right guess and never a claim.
    #: What it is actually for is knowing when *not* to warn: without it, every entry on a
    #: CV in the record's own language would be reported as having fallen back (#131).
    record_language = LanguageField(_("language of your career record"), blank=True)
    time_zone = models.CharField(_("time zone"), max_length=64, blank=True)
    theme = models.CharField(_("theme"), max_length=10, choices=Theme, default=Theme.SYSTEM)
    #: Keys from postulo.core.navigation that this person has chosen not to see in the
    #: main navigation. Everything there is reachable another way, so hiding one takes
    #: nothing away; the row across the top is what runs out of room first.
    hidden_nav_items = models.JSONField(_("hidden navigation items"), default=list, blank=True)
    #: The keys of the main navigation this person has placed, in their order (#299).
    #:
    #: Empty is the default order, which is what every profile had before there was a
    #: choice, so nothing had to be migrated. What is kept is what was *placed*, the same
    #: way round as `hidden_nav_items` and for the same reason: an item added in a later
    #: release is in neither list, and is drawn after the placed ones rather than lost.
    #: `postulo.core.navigation` reads and writes it.
    nav_order = models.JSONField(_("navigation order"), default=list, blank=True)
    #: The same pair for the registry's identifier schemes (#672): the keys placed, in the
    #: person's order, and the keys switched off. Empty is the default, so nothing was
    #: migrated; a scheme in neither is drawn after the placed ones. `postulo.core.
    #: identifier_order` reads and writes them.
    identifier_order = models.JSONField(_("identifier order"), default=list, blank=True)
    hidden_identifiers = models.JSONField(_("hidden identifiers"), default=list, blank=True)
    #: Whether an entry's form on Your career shows its order number. Off, because the
    #: arrows on the overview are the control; on for somebody who cannot use them or would
    #: rather type a number, which is why it lives under Appearance with the other
    #: accessibility choices (#203).
    show_career_order = models.BooleanField(
        _("show the order number on career entries"), default=False
    )
    #: Whether a single printable character on its own does anything: "d" and "j" on the
    #: capture review, "/" for the search box. WCAG 2.1.4 is level A and says a shortcut
    #: made of one character must be switchable off, because somebody dictating into a page
    #: presses every key in the sentence -- and "d" discarded a listing outright (#227).
    #:
    #: On by default: the review screen is worked through forty times in a row and the keys
    #: are why that is bearable. The criterion asks for a way out, not for the default.
    #: Shortcuts with a modifier, Ctrl+Enter among them, are outside this and always work.
    keyboard_shortcuts = models.BooleanField(_("single-key shortcuts"), default=True)
    #: Whether the small key badges beside a control that has a key are drawn (#658). Apart
    #: from `keyboard_shortcuts`: that switch is the behaviour, this one is only what tells
    #: somebody the keys exist. The control keeps `aria-keyshortcuts` either way, so
    #: assistive technology is told whether or not the badge is on screen. On by default,
    #: because a hint helps discovery and the conformance claim rests on the default.
    show_key_hints = models.BooleanField(_("show key hints"), default=True)
    #: Whether the navigation link for the page you are on is underlined as well as tinted
    #: and set in bolder type. The underline arrived with #274, because the tint alone is
    #: 1.1:1 against the header -- nothing to somebody who does not tell those two greys
    #: apart, and nothing at all under a high-contrast theme, where a background is
    #: discarded and a text decoration is kept.
    #:
    #: On by default, and off is not a hole: the weight stays, so what marks the current
    #: page is still not colour alone, which is what WCAG 2.2 SC 1.4.1 asks. The underline
    #: also comes back under forced colours whatever this says, because there it is the
    #: only one of the three cues that survives (#289).
    nav_underline = models.BooleanField(_("underline the page you are on"), default=True)
    #: How much room the interface leaves around things (#292).
    #:
    #: **Comfortable is the default and stays the default.** A generous interface is the
    #: one that is easiest to hit and easiest to read, and it is what the conformance claim
    #: rests on; compact is somebody asking for more rows on a screen, which is a fair thing
    #: to want and not a thing to impose. This is why density is a preference rather than a
    #: compromise: neither answer has to be a little bit of the other.
    #:
    #: Compact tightens the padding around a card and inside a table cell, and nothing else.
    #: It may not shrink a target below 24×24 -- SC 2.5.8 holds whatever anybody chooses,
    #: and `tests/e2e/test_target_size.py` walks the interface with this on to say so.
    density = models.CharField(
        _("density"),
        max_length=12,
        choices=(("comfortable", _("Comfortable")), ("compact", _("Compact"))),
        default="comfortable",
    )
    #: Plugins this person has switched off for themselves. Stored as what was turned
    #: *off*, like `hidden_nav_items` and for the same reason: a plugin installed in a
    #: later release should be available without anybody having to opt into it.
    #:
    #: Only consulted where the choice is theirs to make. An administrator may take it
    #: away, and `plugins.policy` decides that before this is looked at (#95).
    plugins_off = models.JSONField(_("plugins switched off"), default=list, blank=True)
    #: Whether this person's captures keep the page they were read from: the source as it
    #: was parsed, and a rendering of the whole page (#256). Two switches, because the two
    #: leak differently, and both **off**: a copy of a page is kept because somebody asked
    #: for one, never because nobody said no.
    #:
    #: Stored as what was switched *on*, the opposite way round from `plugins_off`, and for
    #: the opposite reason: a plugin installed later should be available without anybody
    #: opting in, and a copy of somebody else's page should not.
    #:
    #: Only half of the answer. An administrator decides whether the instance keeps pages
    #: at all, and `jobs.pages` asks that first: a yes here can narrow what the instance
    #: allows and never widen it.
    keep_page_source = models.BooleanField(_("keep the source of a captured page"), default=False)
    keep_page_rendering = models.BooleanField(
        _("keep a rendering of a captured page"), default=False
    )
    #: Whether a change to a field on a Settings page is saved as it is made, instead of at
    #: the foot of the page (#656). Off, so *Save* stays the moment of decision for anybody
    #: who has not said otherwise; a form says whether it *can* (`data-save-as-you-go`) and
    #: this says whether it *does*. Saving the field, not the form, is the only "draft" an
    #: existing record needs.
    save_as_you_go = models.BooleanField(_("save as I go"), default=False)
    #: How each table is laid out — which columns, in what order, how many rows a page
    #: holds — keyed by the table's name. A preference, so it follows the account.
    table_settings = models.JSONField(_("table settings"), default=dict, blank=True)
    #: Which dashboard widgets this person has chosen, in the order they appear.
    #:
    #: ``None`` means "never arranged", which is the default set; an empty list means
    #: "arranged, to nothing", which is a page somebody deliberately cleared. They have to
    #: be different values, or clearing the page would hand the defaults straight back.
    #:
    #: What is stored is what was *chosen*, the opposite way round from
    #: ``hidden_nav_items`` and on purpose: a widget added in a later release should appear
    #: for somebody who never arranged anything, and stay off the page of somebody who did.
    dashboard_widgets = models.JSONField(_("dashboard widgets"), blank=True, default=list)
    #: Every widget key this account has already decided about -- on the page or off it.
    #:
    #: A key in neither this nor `dashboard_widgets` is new *to this account*, whether it
    #: arrived in a release or with a plugin installed on a Tuesday. That is what replaced
    #: a null arrangement meaning "never arranged": every account owns a list now, so
    #: nobody is ever "never arranged", and the rule the null carried had to be written
    #: down somewhere rather than dropped (#123).
    dashboard_known = models.JSONField(_("widgets already offered"), blank=True, default=list)
    #: LEGACY (#662): the avatar was a file, and is a `ProfilePicture` row now. Read only by
    #: the migration that moved it and by `prune_media`, which lists what is left as
    #: orphans; dropped in the release after, so a rollback finds its files.
    avatar = models.ImageField(_("picture"), upload_to=upload_to_avatars, blank=True)
    #: Whether a `ProfilePicture` of the `upload` kind exists, and the same for the copy of
    #: the Gravatar: on this row so that drawing the masthead never touches the table that
    #: holds the bytes, and written only by `avatars.store` and `avatars.forget`, in the
    #: transaction that writes or deletes the row.
    has_avatar = models.BooleanField(_("has an uploaded picture"), default=False, editable=False)
    has_gravatar_copy = models.BooleanField(_("has a Gravatar copy"), default=False, editable=False)
    #: Opt-in: fetch the Gravatar for the primary address, once, server-side.
    use_gravatar = models.BooleanField(_("use my Gravatar"), default=False)
    #: LEGACY (#662): the copy the server fetched, a `ProfilePicture` row now.
    gravatar_image = models.ImageField(
        _("Gravatar copy"), upload_to=upload_to_avatars, blank=True, editable=False
    )
    gravatar_checked_at = models.DateTimeField(_("Gravatar checked on"), null=True, blank=True)
    #: Days without activity after which an open application counts as having gone quiet.
    quiet_after_days = models.PositiveSmallIntegerField(
        _("quiet after"),
        default=21,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text=_("Days without anything happening, and nothing planned, before Postulo asks."),
    )
    #: How much warning to give before a listing the person has not decided about closes.
    closing_notice_days = models.PositiveSmallIntegerField(
        _("notice before a listing closes"),
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text=_("Days of warning before a listing you have not decided about closes."),
    )

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("profile")
        verbose_name_plural = _("profiles")

    def __str__(self) -> str:
        return f"Profile for {self.user}"

    def save(self, *args, **kwargs) -> None:
        """Clear the nationality scope while countries are listed (#680).

        Here rather than in a form, because the page, the API, a candidate file and the
        archive importer all write a profile and none of them may leave the two disagreeing.
        A save of some columns only (`update_fields`) takes the scope along when it clears it.
        """
        if self.nationalities and self.nationality_scope:
            self.nationality_scope = ""
            fields = kwargs.get("update_fields")
            if fields is not None and "nationality_scope" not in fields:
                kwargs["update_fields"] = [*fields, "nationality_scope"]
        super().save(*args, **kwargs)

    @property
    def picture(self) -> str | None:
        """The kind of picture to show: the upload, else the Gravatar when opted in, else
        nothing. Read from the flags on this row, so it costs no query (#662)."""
        if self.has_avatar:
            return ProfilePicture.UPLOAD
        if self.use_gravatar and self.has_gravatar_copy:
            return ProfilePicture.GRAVATAR
        return None

    @property
    def picture_version(self) -> int:
        """Changes whenever the profile does, so a browser cache never shows an old face."""
        return int(self.updated_at.timestamp()) if self.updated_at else 0


class ProfilePicture(StoredPicture):
    """A person's picture, kept in the database so that it goes with the profile (#662).

    Up to two per profile: the one they uploaded and the copy of their Gravatar the server
    fetched once. `Profile.picture` chooses between them. The profile's own flags say which
    exist, so a page that draws the avatar never reads this table; `accounts:avatar` does,
    to serve one.
    """

    UPLOAD = "upload"
    GRAVATAR = "gravatar"
    KINDS = ((UPLOAD, _("Uploaded")), (GRAVATAR, _("From Gravatar")))

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="pictures")
    kind = models.CharField(_("Type"), max_length=10, choices=KINDS)

    class Meta:
        verbose_name = _("profile picture")
        verbose_name_plural = _("profile pictures")
        constraints = [
            models.UniqueConstraint(fields=["profile", "kind"], name="one_picture_of_each_kind")
        ]

    def __str__(self) -> str:
        return f"{self.kind} picture of profile {self.profile_id}"


class RecoveryLinkQuerySet(models.QuerySet):
    def live(self) -> RecoveryLinkQuerySet:
        """Links that would work if somebody opened one right now."""
        return self.filter(
            used_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now()
        )


class RecoveryLink(models.Model):
    """One single-use way back into one account, issued by an administrator (#103).

    Not an `OwnedModel`. It is a record of an administrative act about an account rather than
    something the account holder owns, which is also why it stays out of their export: an
    archive of somebody's job search should not contain the administrative history of their
    password.

    The row outlives the link. Used, revoked or expired, it stays as the trace of who took
    whose account back and when — the one thing an administrator doing this should not be
    able to do silently. There is no audit trail in *Server settings* yet and building one is
    somebody else's issue; a full account takeover leaving no record at all is not a thing to
    wait for it with.
    """

    #: An hour. A link that still works next week is a link that has been sitting in a chat
    #: log for a week, and the person it was made for is standing next to the administrator
    #: or on the telephone to them right now.
    LIFETIME = timedelta(hours=1)

    person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_links",
        verbose_name=_("person"),
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("issued by"),
    )
    #: SHA-256 of the token, because a link only ever needs checking. Nothing anywhere keeps
    #: the token itself: it is shown to the administrator once and then exists only in
    #: whatever they wrote it into.
    token_fingerprint = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"))
    used_at = models.DateTimeField(_("used at"), null=True, blank=True)
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    objects = RecoveryLinkQuerySet.as_manager()

    class Meta:
        verbose_name = _("recovery link")
        verbose_name_plural = _("recovery links")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("person", "-created_at"))]

    def __str__(self) -> str:
        return f"recovery link for {self.person_id}"

    @property
    def is_live(self) -> bool:
        return self.used_at is None and self.revoked_at is None and self.expires_at > timezone.now()

    @property
    def state(self) -> str:
        """What happened to it, for the page that lists them."""
        if self.used_at is not None:
            return "used"
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at <= timezone.now():
            return "expired"
        return "live"


class InviteQuerySet(models.QuerySet):
    def pending(self) -> InviteQuerySet:
        """Invitations that could still be accepted right now."""
        return self.filter(accepted_at__isnull=True, expires_at__gt=timezone.now())


def generate_invite_token() -> str:
    return secrets.token_urlsafe(INVITE_TOKEN_BYTES)


def fingerprint_of_a_fresh_token() -> str:
    """What a row made without :meth:`Invite.issue` gets: a link nobody holds.

    The token is made and thrown away here, so the row is a valid invitation that no link
    opens. That is the right default for a row created any other way -- a test that only
    needs one to exist, an import -- because the alternative, a blank that the unique
    constraint would refuse on the second row, is a worse surprise.
    """
    from .tokens import fingerprint

    return fingerprint(generate_invite_token())


def default_invite_expiry():
    return timezone.now() + DEFAULT_INVITE_VALIDITY


class Invite(models.Model):
    """An invitation to create an account on this instance.

    A self-hosted instance is normally closed. Rather than asking an operator to choose
    between "only me" and "anyone on the internet", an invitation grants exactly one
    signup, optionally bound to one email address, and expires on its own.

    **The token is not stored.** Only its SHA-256 fingerprint is, as with a recovery link
    and an API token, and the link is shown once, on the page that made it. A copy of the
    database used to be a set of working invitations -- and, for one bound to an address,
    proof of holding that mailbox, since following the link is what verifies it (#232).
    """

    token_fingerprint = models.CharField(
        _("token"),
        max_length=64,
        unique=True,
        default=fingerprint_of_a_fresh_token,
        editable=False,
    )
    email = models.EmailField(
        _("email address"),
        blank=True,
        help_text=_("Optional. If set, only this address may use the invitation."),
    )
    note = models.CharField(
        _("note"), max_length=200, blank=True, help_text=_("A reminder of who this is for.")
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites_created",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"), default=default_invite_expiry)

    accepted_at = models.DateTimeField(_("accepted at"), null=True, blank=True)
    accepted_by = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite_used",
        verbose_name=_("accepted by"),
    )

    objects = InviteQuerySet.as_manager()

    class Meta:
        verbose_name = _("invitation")
        verbose_name_plural = _("invitations")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.email or self.note or f"Invitation {self.pk}"

    @classmethod
    def issue(cls, *, created_by, **fields) -> tuple[Invite, str]:
        """Make an invitation, and return it with the one copy of its token.

        The caller shows the token once and forgets it. Nothing else ever has it: not the
        row, not a log line, not the list of invitations.
        """
        from .tokens import fingerprint

        token = generate_invite_token()
        invite = cls.objects.create(
            created_by=created_by, token_fingerprint=fingerprint(token), **fields
        )
        return invite, token

    @classmethod
    def find(cls, token: str) -> Invite | None:
        """The invitation this token opens, valid or not, or nothing."""
        from .tokens import fingerprint

        if not token:
            return None
        return cls.objects.filter(token_fingerprint=fingerprint(token)).first()

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def is_valid(self, email: str | None = None) -> bool:
        """Whether this invitation may still be used, optionally by ``email``."""
        if self.is_accepted or self.is_expired:
            return False
        if self.email and email and self.email.casefold() != email.casefold():
            return False
        return True

    def accept(self, user) -> bool:
        """Spend the invitation for ``user``, and say whether that happened.

        One statement, which changes the row only while it is unspent and unexpired. Two
        requests can each hold a copy that looks unspent; saving the copy let the later one
        write its own account over the first one's, and both accounts existed (#544). Here
        the database decides, and the copy that lost is left saying what it said before.
        """
        now = timezone.now()
        spent = Invite.objects.filter(
            pk=self.pk, accepted_at__isnull=True, expires_at__gt=now
        ).update(accepted_at=now, accepted_by=user)
        if spent:
            self.accepted_at, self.accepted_by = now, user
        return bool(spent)
