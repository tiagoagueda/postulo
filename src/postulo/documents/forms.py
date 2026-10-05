"""Forms for CVs, cover letters and uploads."""

from __future__ import annotations

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.html import format_html
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from postulo.core import personal, postal
from postulo.jobs.forms import OwnerScopedModelForm
from postulo.resume.models import Link
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

from . import themes
from .models import (
    CV,
    LETTER_STARTERS,
    LETTER_THEMES,
    CoverLetter,
    CVItem,
    CVKind,
    LetterKind,
    Prints,
    UploadedDocument,
)
from .properties import (
    KEYWORDS_HELP,
    MAX_AUTHOR,
    MAX_KEYWORDS,
    MAX_SUBJECT,
    MAX_TITLE,
    WHAT_IS_KEPT,
)

#: Maximum size for an upload, in bytes. Generous for a CV, mean for a video.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def language_choices() -> list[tuple[str, str]]:
    """The languages a document may declare, with blank meaning "whatever I read in".

    A list rather than a text box because the value ends up in the ``lang`` of a PDF that
    gets sent to somebody, and a mistyped tag is worse than none: a screen reader will
    happily read Portuguese with the rules of whatever ``pt_PT`` or ``portuguese`` failed
    to parse as. The choices are the instance's own languages, each under its own name.
    """
    from postulo.core import languages

    return [("", _("Follow your profile")), *languages.LANGUAGES]


def language_widget(held="", choices: list | None = None) -> forms.Select:
    """The same menu the profile uses, so an option says which language it is in.

    ``held`` is what the document already declares, which stays on the menu where it is
    none of the instance's own: the field is free text, and a menu that could not show
    `pt-AO` saved *Follow your profile* over it (#337).
    """
    from postulo.accounts.forms import LanguageSelect, with_what_is_held

    choices = language_choices() if choices is None else choices
    return LanguageSelect(choices=with_what_is_held(choices, held))


class LanguageChoiceMixin:
    """Turn the model's free-text ``language`` field into a picker."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("language")
        if field is not None:
            field.widget = language_widget(self["language"].value())
            field.required = False


class ThemeChoiceMixin:
    """Offer only the themes that set this kind of document.

    The picker is where #132 is actually answered. A theme declares what it sets; refusing
    a pair at render time would be a message after the export button, which is exactly the
    thing the issue calls out. So the pair never gets chosen: a theme that does not set
    this kind is not in the menu, and the validator on the field says the same thing to
    anything that arrives another way.

    A list rather than the model's ``choices`` because the model has none any more --
    choices are frozen into migrations, and a theme from an installed plugin is not known
    when a migration is written.
    """

    #: Which `themes.Kind` this form's document is.
    theme_kind = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("theme")
        if field is None:
            return
        choices = themes.choices_for(self.theme_kind)
        self.fields["theme"] = forms.ChoiceField(
            label=field.label,
            help_text=field.help_text,
            required=field.required,
            choices=choices,
        )
        # A document whose theme came from a plugin that has since been removed opens on the
        # theme it is actually being rendered in, rather than on an error about a field
        # nobody touched. It already looks like this; the form stops pretending otherwise.
        if self.initial.get("theme") not in {value for value, _label in choices}:
            self.initial["theme"] = themes.DEFAULT


#: What is under each chooser: what the CV prints of that kind as things stand, which is
#: what gives *your primary number* a meaning somebody can check. One sentence for every
#: kind, with the value in a slot of its own -- a number or an address is not a word, so
#: nothing has to agree with it.
PRINTED_NOW = _("Printed as things stand: {value}.")
NONE_PRINTED_NOW = _("As things stand, none is printed.")
#: Where the profile has nothing of the kind at all: not a choice that prints nothing, but
#: nothing to choose from, and the sentence says which.
GIVES_NONE = _("Your details give none yet, so nothing is printed until they do.")
#: Under the email chooser, only for an account that has an address nobody has confirmed.
WAITS_TO_BE_CONFIRMED = _("An address of yours is offered here once it has been confirmed.")


def _typed(value: str):
    """Something a person typed, set inline in a sentence Postulo wrote."""
    return format_html("<bdi>{}</bdi>", value)


class CVForm(ThemeChoiceMixin, LanguageChoiceMixin, OwnerScopedModelForm):
    """A CV's settings, and which of its owner's details it prints (#308).

    The choosers are built for whoever the form is for and offer that person's own rows
    and nothing else: `printing.offered` is the list, so a value that is not on it -- a
    row of somebody else's, a row that has gone -- is not a valid choice and is refused
    before anything is stored. A chooser left out of what was posted leaves its answer as
    it is, so a client that has never heard of them changes nothing.

    A chooser whose chosen row is not offered at present opens on *none*, which is what the
    CV prints, and posting that *none* back is not an answer: see `clean`.
    """

    #: The document itself, which is the first card; the rest is what it prints about
    #: its owner, which is the second.
    SETTINGS = ("name", "kind", "headline", "summary", "theme", "language")

    class Meta:
        model = CV
        fields = (
            "name",
            "kind",
            "headline",
            "summary",
            "theme",
            "language",
            "show_contact_details",
            "show_location",
            "show_form_of_address",
            "show_pronouns",
            "show_birth_date",
            "show_birth_place",
            "show_nationality",
            "show_gender",
        )
        widgets = {"summary": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "kind": _(
                "A CV leads with a career, a portfolio with work. It decides which themes "
                "are offered below."
            ),
            "headline": _(
                "The line under your name. Left empty, the one on your profile is printed instead."
            ),
            "theme": _("How it is set on the page. Change it later without touching a word."),
        }

    def scope_querysets(self) -> None:
        """Build the choosers from this person's own rows, and say what each prints now."""
        from . import printing

        owner = self.user
        saved = self.instance if self.instance.pk else None
        printed = printing.resolve(owner, saved) if owner is not None else printing.Printed()
        for detail in printing.DETAILS:
            rows = printing.offered(owner, detail.key) if owner is not None else []
            value = getattr(printed, detail.key)
            if value:
                help_text = format_html(str(PRINTED_NOW), value=_typed(value))
            elif rows or detail.key == "email":
                help_text = NONE_PRINTED_NOW
            else:
                help_text = GIVES_NONE
            if detail.key == "email" and printing.has_unconfirmed_addresses(owner):
                # An address the account holds and the menu does not list: said, so that
                # its absence reads as a rule rather than as a fault.
                help_text = format_html("{} {}", help_text, WAITS_TO_BE_CONFIRMED)
            self.fields[f"prints_{detail.key}"] = forms.ChoiceField(
                label=detail.label,
                required=False,
                choices=[
                    # A kind whose default prints nothing has *none* and no second way of
                    # saying it (#682).
                    *(((Prints.DEFAULT.value, detail.follow),) if detail.follows else ()),
                    *((str(row.pk), printing.name_of(row, detail.key)) for row in rows),
                    (Prints.NONE.value, detail.none),
                ],
                help_text=help_text,
            )
            self.initial[f"prints_{detail.key}"] = self._answer_of(detail)

        identifiers = printing.offered(owner, "identifiers") if owner is not None else []
        answers = [(Prints.DEFAULT.value, _("Every identifier, and any you add later"))]
        if identifiers:
            # Only where there is something to tick: with none, the list below is not
            # drawn, and a choice that leads nowhere is not offered.
            answers.append((Prints.CHOSEN.value, _("Only the ones ticked below")))
        answers.append((Prints.NONE.value, _("No identifiers")))
        self.fields["prints_identifiers"] = forms.ChoiceField(
            label=_("Identifiers"),
            required=False,
            choices=answers,
            help_text=(
                _("An ORCID or another public id, as your details list them.")
                if identifiers
                else GIVES_NONE
            ),
        )
        self.fields["identifier_rows"] = forms.MultipleChoiceField(
            label=_("Identifiers to print"),
            required=False,
            choices=[(str(row.pk), printing.name_of(row, "identifiers")) for row in identifiers],
            widget=forms.CheckboxSelectMultiple,
            help_text=_("One you add to your details later is not printed until you tick it here."),
        )
        chosen = saved is not None and saved.identifiers_choice == Prints.CHOSEN
        ticked = (
            [str(pk) for pk in saved.pinned_identifiers.values_list("pk", flat=True)]
            if chosen
            else []
        )
        self.initial["identifier_rows"] = ticked
        self.initial["prints_identifiers"] = (
            saved.identifiers_choice if saved is not None else Prints.DEFAULT.value
        )
        if chosen and not identifiers:
            # Every one it had chosen has gone, and so has the choice that named them:
            # the menu shows what that comes to.
            self.initial["prints_identifiers"] = Prints.NONE.value

        profile = getattr(owner, "profile", None)
        where = postal.printed_location(profile)
        self.fields["show_location"].help_text = (
            format_html(
                str(_("The line your details give, never a street. As things stand: {value}.")),
                value=_typed(where),
            )
            if where
            else _("The line your details give, never a street. They give none yet.")
        )
        for name in ("form_of_address", "pronouns"):
            said = (getattr(profile, name, "") or "").strip()
            self.fields[f"show_{name}"].help_text = (
                format_html(str(_("As things stand: {value}.")), value=_typed(said))
                if said
                else GIVES_NONE
            )
        # What each would print as things stand, and said so where there is nothing to print:
        # a switch for a date nobody has given would otherwise look broken (#679). The values
        # are the person's own, drawn only on their own form, as the neighbours' are.
        if profile is not None and profile.birth_date:
            self.fields["show_birth_date"].help_text = format_html(
                str(_("As things stand: {value}.")),
                value=_typed(personal.birth_date_text(profile.birth_date) or profile.birth_date),
            )
        else:
            self.fields["show_birth_date"].help_text = GIVES_NONE
        place = personal.place_text(
            getattr(profile, "birth_place", ""), getattr(profile, "birth_country", "")
        )
        self.fields["show_birth_place"].help_text = (
            format_html(str(_("As things stand: {value}.")), value=_typed(place))
            if place
            else GIVES_NONE
        )
        gender = (getattr(profile, "gender", "") or "").strip()
        self.fields["show_gender"].help_text = (
            format_html(str(_("As things stand: {value}.")), value=_typed(gender))
            if gender
            else GIVES_NONE
        )
        said = personal.country_names(getattr(profile, "nationalities", None))
        scope = personal.scope_text(getattr(profile, "nationality_scope", ""))
        self.fields["show_nationality"].help_text = (
            format_html(str(_("As things stand: {value}.")), value=_typed(", ".join(said) or scope))
            if said or scope
            else GIVES_NONE
        )
        #: The kinds whose chosen row is no longer there, for the page to say so.
        self.gone = printing.gone(saved)

    @property
    def settings_fields(self) -> list:
        """The first card's fields. Asked for when the page is drawn rather than kept from
        here, because the theme's field is replaced after this form's own `__init__`."""
        return [self[name] for name in self.SETTINGS if name in self.fields]

    @property
    def chooser_fields(self) -> list:
        """The choosers that print one of a kind, in the order the contact line has them."""
        from . import printing

        return [self[f"prints_{detail.key}"] for detail in printing.DETAILS]

    def _answer_of(self, detail) -> str:
        """What a chooser opens on: the saved answer, as one of the values it offers.

        A chosen row that is not there to print opens on *none*, because that is what the
        CV prints. For one that was deleted, saving the form then makes it the answer; for
        one that is only kept back, it does not (`clean`).
        """
        from . import printing

        if not self.instance.pk:
            return Prints.DEFAULT.value if detail.follows else Prints.NONE.value
        answer = getattr(self.instance, detail.choice_field)
        if answer != Prints.CHOSEN:
            return answer if detail.follows or answer != Prints.DEFAULT else Prints.NONE.value
        row = printing.pinned(self.instance, detail)
        return str(row.pk) if row is not None else Prints.NONE.value

    @property
    def choice_is_open(self) -> bool:
        """Whether the choosers are drawn open: when something in them was chosen, has gone
        or was refused. Closed, the card says what a CV prints until somebody chooses."""
        from . import printing

        names = [field.name for field in self.chooser_fields]
        names += ["prints_identifiers", "identifier_rows", *printing.SWITCHES.values()]
        if any(self[name].errors for name in names):
            return True
        if not self.instance.pk:
            return False
        return bool(self.gone) or not printing.is_default(self.instance)

    def clean(self):
        """Put each chooser's answer on the CV, through the one door that accepts a row.

        Here rather than in `save`, so that a row deleted between the page being drawn and
        the form being posted is an error under its chooser and not a failure afterwards.

        **A menu nobody touched does not unpin a row that is kept back.** The row a CV
        chose may be there and not offered -- a second number while *Several telephone
        numbers* is off, an address that is no longer confirmed -- and its menu then opens
        on *none*, because the row cannot be listed and *none* is what the CV prints. Read
        as an answer, that *none* made every save of this page, for a new name or a new
        theme, delete a pin that was promised to print again once the row is offered again.
        So *none* posted for such a kind is what the page was drawn with, and the kind is
        left alone; any other value is somebody choosing, and is stored. A row that was
        deleted is not coming back, and there *none* is the answer.
        """
        from . import printing

        cleaned = super().clean()
        owner = self.user if self.user is not None else getattr(self.instance, "owner", None)
        saved = self.instance if self.instance.pk else None
        for detail in printing.DETAILS:
            name = f"prints_{detail.key}"
            raw = cleaned.get(name)
            if not raw:
                continue
            if raw == Prints.NONE and saved is not None and printing.is_kept_back(saved, detail):
                continue
            try:
                if raw in (Prints.DEFAULT, Prints.NONE):
                    # *None* of a kind that prints nothing by default is the default.
                    answer = raw if detail.follows else Prints.DEFAULT
                    printing.choose(self.instance, detail.key, answer, owner=owner)
                else:
                    printing.choose(self.instance, detail.key, Prints.CHOSEN, int(raw), owner=owner)
            except (printing.NotOffered, ValueError):
                self.add_error(name, _("Choose one of the rows in your details."))
        self._pinned_identifiers = None
        raw = cleaned.get("prints_identifiers")
        if raw:
            ticked = (cleaned.get("identifier_rows") or []) if raw == Prints.CHOSEN else []
            try:
                self._pinned_identifiers = printing.choose_identifiers(
                    self.instance, raw, [int(pk) for pk in ticked], owner=owner
                )
            except (printing.NotOffered, ValueError):
                self.add_error("identifier_rows", _("Choose one of the rows in your details."))
        return cleaned

    def _save_m2m(self) -> None:
        super()._save_m2m()
        if getattr(self, "_pinned_identifiers", None) is not None:
            self.instance.pinned_identifiers.set(self._pinned_identifiers)

    @property
    def theme_kind(self) -> str:
        """Which themes to offer: the ones that set whatever this is.

        A property rather than the class attribute it used to be, because one form now
        covers two shapes -- and a portfolio offered a theme that only knows how to set a CV
        is exactly the pair #132 exists to keep out of the menu (#133).
        """
        raw = self.data.get(self.add_prefix("kind")) if self.is_bound else None
        kind = raw or getattr(self.instance, "kind", "") or CVKind.CV
        return themes.Kind.PORTFOLIO if kind == CVKind.PORTFOLIO else themes.Kind.CV

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if self.user is None:
            return name
        clash = CV.objects.for_user(self.user).filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("You already have a CV or portfolio with that name."))
        return name


class CVItemForm(OwnerScopedModelForm):
    """Edit one entry's placement on one CV."""

    class Meta:
        model = CVItem
        fields = ("is_included", "override_highlights", "order")
        widgets = {"override_highlights": forms.Textarea(attrs={"rows": 6})}


class AddCVItemsForm(forms.Form):
    """Choose which career entries to put on a CV.

    Presented as one grouped list of checkboxes rather than a picker per kind, because
    building a variant is one decision — "what goes on this one?" — not six.
    """

    def __init__(self, *args, cv: CV, **kwargs):
        super().__init__(*args, **kwargs)
        self.cv = cv
        self.groups = []

        already_on_cv = {(item.content_type_id, item.object_id) for item in cv.items.all()}

        for slug in OVERVIEW_ORDER:
            spec = SECTIONS[slug]
            content_type = ContentType.objects.get_for_model(spec.model)
            available = [
                obj
                for obj in spec.model.objects.for_user(cv.owner)
                if (content_type.id, obj.pk) not in already_on_cv
            ]
            if not available:
                continue

            field_name = f"add_{slug.replace('-', '_')}"
            self.fields[field_name] = forms.MultipleChoiceField(
                label=spec.plural,
                required=False,
                choices=[(obj.pk, str(obj)) for obj in available],
                widget=forms.CheckboxSelectMultiple,
            )
            # The bound field, not its name: a template cannot look a field up by a
            # variable, and inventing a filter to do so would be worse than this.
            self.groups.append(
                {"field": self[field_name], "name": field_name, "label": spec.plural, "slug": slug}
            )

    def selected(self) -> list[tuple[ContentType, int]]:
        """The chosen entries, as content type and primary key pairs."""
        chosen: list[tuple[ContentType, int]] = []
        for group in self.groups:
            spec = SECTIONS[group["slug"]]
            content_type = ContentType.objects.get_for_model(spec.model)
            for raw_pk in self.cleaned_data.get(group["name"], []):
                chosen.append((content_type, int(raw_pk)))
        return chosen


class CoverLetterForm(ThemeChoiceMixin, LanguageChoiceMixin, OwnerScopedModelForm):
    """A letter of any of the four kinds.

    A new letter starts from the kind's own text rather than an empty box: what a
    motivation letter is supposed to look like is not obvious, and a shape on the page
    says it better than help text underneath.
    """

    theme_kind = themes.Kind.LETTER

    class Meta:
        model = CoverLetter
        fields = ("name", "kind", "subject", "body", "theme", "is_template", "language")
        widgets = {"body": forms.Textarea(attrs={"rows": 18})}
        help_texts = {
            "name": _("What you will look for it under. It is never printed on the letter."),
            "kind": _("It sets the shape the box below starts from, and which themes are offered."),
            "subject": _("Printed at the top. It takes the same placeholders as the body."),
            "theme": _("How it is set on the page. Change it later without touching a word."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            return
        kind = self.initial.get("kind") or LetterKind.COVER
        self.initial.setdefault("kind", kind)
        self.initial.setdefault("body", str(LETTER_STARTERS.get(kind, "")))
        self.initial.setdefault("theme", LETTER_THEMES.get(kind, themes.DEFAULT))


class UploadedDocumentForm(OwnerScopedModelForm):
    class Meta:
        model = UploadedDocument
        fields = ("title", "kind", "file", "language", "notes", "replaces")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "title": _("What you will look for it under."),
            "kind": _("What sort of thing it is, so the lists can be narrowed by it."),
            "file": _(
                "PDF, Word, OpenDocument, RTF, plain text, PNG or JPEG. Postulo keeps it "
                "exactly as uploaded, under a name of its own — what you send is the file "
                "you gave it."
            ),
            "notes": _("Yours. They go nowhere with the file."),
            "replaces": _("An older version of the same thing, kept but no longer offered."),
        }

    def scope_querysets(self) -> None:
        # The same picker the other documents use, with one word changed: blank here means
        # *nobody has said*, not "follow your profile", because this is a file Postulo has
        # never read (#283). The model field stays free text, as it is everywhere else, so
        # a language an instance has since stopped offering still saves.
        language = self.fields.get("language")
        if language is not None:
            language.widget = language_widget(
                self["language"].value(), [("", _("Not said")), *language_choices()[1:]]
            )
            language.required = False

        queryset = UploadedDocument.objects.for_user(self.user)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
            # **The file itself cannot be edited** (#217). An application records which upload
            # it sent, so swapping the bytes under it would make the record say a file was
            # sent that never was — and an external store, which is told about a document once
            # when it is created, would keep the old copy and call it archived. A different
            # file is a new upload that `replaces` this one, which keeps both and says which
            # came after.
            self.fields.pop("file", None)
        self.fields["replaces"].queryset = queryset
        self.fields["replaces"].label = _("Supersedes")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded and uploaded.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                _("That file is larger than %(limit)s MB.")
                % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024)}
            )
        return uploaded

    def save(self, commit: bool = True) -> UploadedDocument:
        document = super().save(commit=False)
        # A new version numbers itself from the one it supersedes, so the history reads
        # in order without anyone having to keep count.
        if document.replaces_id and not self.instance.pk:
            document.version = document.replaces.version + 1
        if commit:
            document.save()
        return document


class FilePropertiesForm(forms.Form):
    """What a file says about itself, shown before it is made (#480).

    Never validated as a form: it is what the page shows, and what is posted is read by
    `Properties.from_data`, which cuts each field to its length and drops a language that is
    not a tag. Each field starts as today's value, so a person sees what the file will say
    even if they change nothing.
    """

    with_properties = forms.BooleanField(
        label=_("Write the file with its properties"),
        required=False,
        initial=True,
        help_text=WHAT_IS_KEPT,
    )
    title = forms.CharField(label=_("Title"), max_length=MAX_TITLE, required=False)
    author = forms.CharField(label=_("Author"), max_length=MAX_AUTHOR, required=False)
    subject = forms.CharField(label=_("Subject"), max_length=MAX_SUBJECT, required=False)
    keywords = forms.CharField(
        label=_("Keywords"),
        max_length=MAX_KEYWORDS,
        required=False,
        help_text=KEYWORDS_HELP,
    )
    language = forms.CharField(
        label=_("Language"),
        max_length=35,
        required=False,
        help_text=_(
            "A language tag such as fr or pt-BR. A screen reader announces the file in it."
        ),
    )


class SendDocumentsForm(forms.Form):
    """Choose what to send with an application, and freeze it."""

    cv = forms.ModelChoiceField(label=_("CV"), queryset=CV.objects.none(), required=False)
    cover_letter = forms.ModelChoiceField(
        label=_("Letter"), queryset=CoverLetter.objects.none(), required=False
    )
    uploads = forms.ModelMultipleChoiceField(
        label=_("Files you have already"),
        queryset=UploadedDocument.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Each one is frozen as it is now, so what they received stays readable."),
    )
    links = forms.ModelMultipleChoiceField(
        label=_("Links you pointed them at"),
        queryset=Link.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Recorded as part of what they were sent. Postulo sends nothing itself — this "
            "is the note of what you sent."
        ),
    )

    with_properties = forms.BooleanField(
        label=_("Write the files with their properties"),
        required=False,
        initial=True,
        help_text=format_lazy(
            "{} {}",
            _(
                "The author, and the title with your name in it, go into the PDF's "
                "properties, as they always have. Untick it to send files without them."
            ),
            WHAT_IS_KEPT,
        ),
    )

    #: The block that sends it (#361). It exists only for somebody with an outbox set up;
    #: for everybody else the fields are removed below and Postulo sends nothing itself.
    EMAIL_FIELDS = ("send_email", "recipient", "subject", "body")

    send_email = forms.BooleanField(
        label=_("Email these from my own address"),
        required=False,
        help_text=_(
            "Sent first, over your own outbox, with the CV, the letter and the files you "
            "chose attached. They are frozen and recorded only if the email went."
        ),
    )
    recipient = forms.EmailField(label=_("Recipient"), required=False)
    subject = forms.CharField(label=_("Subject"), max_length=200, required=False)
    body = forms.CharField(
        label=_("Message"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("Leave it empty to send the text of the letter you chose."),
    )

    def __init__(self, *args, user=None, application=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from postulo.core import correspondence

        self.can_email = correspondence.can_send_as_themselves(user)
        if self.can_email:
            self.fields["links"].help_text = _(
                "Recorded as part of what they were sent. They are not added to the email."
            )
            if application is not None and not self.is_bound:
                contact = application.contact
                self.initial.setdefault("recipient", contact.email if contact else "")
                self.initial.setdefault("subject", application.posting.title)
        else:
            for name in self.EMAIL_FIELDS:
                del self.fields[name]
        self.fields["cv"].queryset = CV.objects.for_user(user)
        # What the help texts promise: a letter ticked as a one-off is not a template, and a
        # file something replaces is "kept but no longer offered" (#510). Whatever was posted
        # stays valid and listed, so that recording something sent long ago with an older
        # version is still possible and a form re-rendered with errors does not lose it.
        letters = CoverLetter.objects.for_user(user)
        uploads = UploadedDocument.objects.for_user(user)
        self.fields["cover_letter"].queryset = letters.filter(
            Q(is_template=True) | Q(pk__in=self._posted_pks("cover_letter"))
        )
        self.fields["uploads"].queryset = uploads.filter(
            Q(replaced_by__isnull=True) | Q(pk__in=self._posted_pks("uploads"))
        ).distinct()
        self.fields["links"].queryset = Link.objects.for_user(user)

    def document_fields(self):
        """The fields that choose what was sent: all of them but the email block."""
        return [field for field in self if field.name not in self.EMAIL_FIELDS]

    def email_fields(self):
        """The email block's fields, none where there is no outbox."""
        return [field for field in self if field.name in self.EMAIL_FIELDS]

    def _posted_pks(self, name: str) -> list[int]:
        """The ids this form was given for a field, those that are numbers."""
        if not self.is_bound:
            return []
        values = self.data.getlist(name) if hasattr(self.data, "getlist") else self.data.get(name)
        if values is None:
            return []
        if not isinstance(values, (list, tuple)):
            values = [values]
        texts = [str(v) for v in values]
        return [int(v) for v in texts if v.isascii() and v.isdigit() and len(v) < 19]

    def clean(self):
        cleaned = super().clean()
        chosen = (
            cleaned.get("cv"),
            cleaned.get("cover_letter"),
            cleaned.get("uploads"),
            cleaned.get("links"),
        )
        if not any(chosen):
            raise forms.ValidationError(_("Choose at least one document to record."))
        if cleaned.get("send_email"):
            if not cleaned.get("recipient") and not self.has_error("recipient"):
                self.add_error("recipient", _("Say who to send it to."))
            if not cleaned.get("subject"):
                self.add_error("subject", _("Give the email a subject."))
            if not cleaned.get("body") and not cleaned.get("cover_letter"):
                self.add_error(
                    "body", _("Write a message, or choose a letter to send as the message.")
                )
        return cleaned
