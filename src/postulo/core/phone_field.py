"""A telephone field that asks which country the number is for.

Two controls, one value. The country is not stored: a number kept as ``+33612345678``
already says which country it belongs to, and a second column holding ``FR`` would be a
second place for the same fact to be wrong. It is read back from the number when the field
is next shown.

The country defaults to the one the person's own language suggests, which is right far
more often than any other guess and costs one click when it is not.

**The field is where a number is checked** (#304), so that the rows on a page, the one box
that stands in for them and anything else built on it all refuse the same numbers in the
same words. What it refuses and what it keeps is `phones.check`'s to say. What is the
field's own is the rule about a number that is already stored: it comes back exactly as
it is kept unless somebody changed it, unchecked and unrewritten, and is checked the
moment they do. The widget draws the mark such a number wears until then.
"""

from __future__ import annotations

from django import forms
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from . import phones

#: What every telephone field says under itself: the rows, and the one box that stands in
#: for them on *Your details* and on a contact. One sentence in one place, because it was
#: three, in two wordings, and all of them went on saying that a number starting with a
#: plus is taken "as it is" after #304 made that untrue: it is read against its country's
#: plan like any other, and what the plus changes is that the chooser is not asked.
HELP = _(
    "Kept in the international form, so it can be dialled from anywhere. A number that "
    "starts with + says its own country, and the chooser beside it is ignored."
)


class PhoneWidget(forms.MultiWidget):
    """A country beside a box for the rest of the number, drawn as one group."""

    template_name = "partials/phone_widget.html"

    #: A `MultiWidget` says it is a fieldset, and Django then leaves `aria-describedby` for
    #: a `<fieldset>` it expects somebody to draw. Nothing draws one: the visible label
    #: points at the number box, so the box is what the help and the errors describe, and
    #: saying so here is what makes Django work the attribute out (#416).
    use_fieldset = False

    #: The number the field already holds, where it holds one: `PhoneField` hands it over.
    #: The mark under the group is about this value and is drawn only while the group still
    #: shows it.
    stored = ""

    #: Whether the form this sits on has a notes box, which is where a refusal sends an
    #: extension or the words beside a number. `PhoneField` hands it over.
    has_notes = False

    def __init__(self, attrs=None, default_country: str = ""):
        self.default_country = default_country
        # Dialling code first, name last. A closed select is clipped to its own width, and
        # what somebody needs to see once they have chosen is which code -- not the tail of
        # a long name.
        #
        # No flag in the label. An <option> may contain text and nothing else, in every
        # browser, so the flag emoji that used to sit here could never have become an
        # image; on Windows it was drawing as two letters anyway (#88). The flag moved out
        # beside the closed select, where it is visible without opening anything, and each
        # option carries its country in a data attribute so the script can find the right
        # one. An attribute is not content, which is why that much is allowed.
        choices = [("", _("Country"))] + [
            (country.code, f"+{country.dialling} {country.name}") for country in phones.countries()
        ]
        super().__init__(
            widgets=[
                forms.Select(choices=choices),
                forms.TextInput(
                    attrs={
                        "inputmode": "tel",
                        "autocomplete": "tel",
                        **(attrs or {}),
                    }
                ),
            ]
        )

    def get_context(self, name, value, attrs):
        """Hand the template the chosen country, what describes the box, and the mark.

        The country is for the flag, which is drawn on the server: the script keeps it in
        step afterwards, but it must be right before any script runs and right when none
        does. With JavaScript off the field still shows the flag of the country it loaded
        with, which is the true answer until the form is saved.

        `aria-describedby` and `aria-invalid` are what Django worked out for the field as a
        whole. They belong on the box, which is the control the visible label points at,
        and the template used to write neither (#416): somebody whose number was refused
        landed on a box that said neither that it was wrong nor why.
        """
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        parts = list(value) if isinstance(value, (list, tuple)) else self.decompress(value)
        widget["country"] = (parts[0] if parts else "") or ""

        described = [widget["attrs"].get("aria-describedby", "")]
        mark = self.mark(parts)
        if mark is not None and widget["attrs"].get("id"):
            widget["mark"] = mark
            widget["mark_says"] = self.said(mark)
            widget["mark_id"] = f"{widget['attrs']['id']}_kept"
            described.append(widget["mark_id"])
        widget["described_by"] = " ".join(part for part in described if part)
        widget["invalid"] = widget["attrs"].get("aria-invalid", "")
        return context

    def mark(self, parts) -> phones.Verdict | None:
        """What to say under the group about the number already stored, if anything.

        Only while the group still shows that number. Once somebody has typed another, the
        mark is about a number that is no longer in the box, and what they typed is answered
        by the field's own errors.
        """
        if not self.stored or list(parts) != self.decompress(self.stored):
            return None
        verdict = phones.kept(self.stored)
        return None if verdict.fine else verdict

    def said(self, mark: phones.Verdict):
        """The mark's sentence, with the number it gives kept to its own direction.

        One sentence holds a telephone number: the spelling that can be dialled, of a
        number stored another way. A number is written left to right in every language,
        and inside a right-to-left sentence its groups are otherwise laid out last group
        first, so it is isolated in markup. The sentence itself is text and is escaped.
        """
        sentence = mark.sentence(notes=self.has_notes)
        number = phones.readable(mark.dialled) if mark.dialled else ""
        if not number or number not in sentence:
            return sentence
        before, _number, after = sentence.partition(number)
        return format_html('{}<bdi dir="ltr">{}</bdi>{}', before, number, after)

    def id_for_label(self, id_):
        """Point the visible label at the number box.

        A ``MultiWidget`` has no single id, and Django's default is to render
        ``for=""`` — a label attached to nothing, which is worse than no label. The number
        is the control somebody is looking for; the country chooser carries its own name.
        """
        return f"{id_}_1" if id_ else ""

    def decompress(self, value):
        """Split a stored number back into the country and the rest.

        `phones.split` promises that typing the two back gives the stored number exactly,
        which is what lets the field tell an untouched number from a changed one. A value
        that was never in international form has no country to show, and the chooser is
        left empty for it: nothing is rewritten on the way to the screen, and nothing is
        suggested about a number nobody said the country of.
        """
        if not value:
            return [self.default_country, ""]
        return list(phones.split(value))


class PhoneField(forms.MultiValueField):
    """The pair, cleaned into one stored value."""

    widget = PhoneWidget

    def __init__(self, *, default_country: str = "", has_notes: bool = False, **kwargs):
        kwargs.setdefault("require_all_fields", False)
        kwargs.setdefault("help_text", HELP)
        fields = (
            forms.ChoiceField(
                choices=[("", "")] + [(row[0], row[0]) for row in phones.COUNTRIES],
                required=False,
            ),
            forms.CharField(max_length=40, required=False, strip=True),
        )
        super().__init__(fields=fields, **kwargs)
        self.has_notes = has_notes
        self.widget.default_country = default_country
        self.widget.has_notes = has_notes
        # Whatever `initial` was passed in was set before the widget existed.
        self.initial = self.initial

    @property
    def initial(self):
        return self._initial

    @initial.setter
    def initial(self, value) -> None:
        """The number already stored, which the widget needs as well as the field.

        A form says what a field starts with by setting this, and for a telephone field
        that value is the stored number: the one that must come back untouched, and the
        one the mark under the group is about. Setting it in one place keeps the two from
        ever being told different things.
        """
        self._initial = value
        widget = getattr(self, "widget", None)
        if isinstance(widget, PhoneWidget):
            widget.stored = value if isinstance(value, str) else ""

    def has_changed(self, initial, data):
        """Whether somebody changed the number. A chooser showing its default is not that.

        A field with nothing stored starts from `None`, and Django then compares what was
        posted, part by part, with two empty strings. The chooser of an empty row is not
        empty: it starts on the country the reader's language suggests and is posted that
        way by a row nobody touched. So the blank row at the foot of every block had
        "changed", the formset saved it, and *Your details* gained a telephone row with no
        number in it each time the page was saved in a language that suggests a country
        (#649). In one that suggests none -- Ukrainian -- it never happened.

        Nothing stored and nothing typed is no change, whatever the chooser is on: a
        country beside an empty box is not a number. Otherwise nothing stored is compared
        as the widget draws nothing stored, with the chooser on its default.
        """
        typed = data[1] if isinstance(data, (list, tuple)) and len(data) > 1 else ""
        if not initial and not (typed or "").strip():
            return False
        return super().has_changed("" if initial is None else initial, data)

    def clean(self, value):
        """The stored number exactly as it is kept, unless somebody changed it.

        A number recorded before the numbering plans were asked (#304) may be one they call
        impossible, or one that was never given a country. Saving the page it sits on is
        not a decision about it: it is neither refused nor rewritten, and the page marks
        it. Changing it is, and from then on it is checked like any other.
        """
        stored = self.initial if isinstance(self.initial, str) else ""
        if stored and not self.has_changed(stored, value):
            return stored
        return super().clean(value)

    def compress(self, values) -> str:
        if not values:
            return ""
        country, number = [*values, "", ""][:2]
        verdict = phones.check(number or "", country or "")
        if verdict.impossible:
            raise forms.ValidationError(verdict.sentence(notes=self.has_notes), code=verdict.reason)
        return phones.combine(number or "", country or "")
