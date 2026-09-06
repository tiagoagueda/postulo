"""A telephone field that asks which country the number is for.

Two controls, one value. The country is not stored: a number kept as ``+33612345678``
already says which country it belongs to, and a second column holding ``FR`` would be a
second place for the same fact to be wrong. It is read back from the dialling code when
the field is next shown.

The country defaults to the one the person's own language suggests, which is right far
more often than any other guess and costs one click when it is not.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from . import phones


class PhoneWidget(forms.MultiWidget):
    """A country beside a box for the rest of the number."""

    template_name = "partials/phone_widget.html"

    def __init__(self, attrs=None, default_country: str = ""):
        self.default_country = default_country
        # Flag and dialling code first, name last. A closed select is clipped to its own
        # width, and what somebody needs to see once they have chosen is which country and
        # which code -- not the tail of a long name.
        choices = [("", _("Country"))] + [
            (country.code, f"{country.flag} +{country.dialling} {country.name}")
            for country in phones.countries()
        ]
        super().__init__(
            widgets=[
                forms.Select(choices=choices, attrs={"class": "field-input"}),
                forms.TextInput(
                    attrs={
                        "class": "field-input",
                        "inputmode": "tel",
                        "autocomplete": "tel",
                        **(attrs or {}),
                    }
                ),
            ]
        )

    def id_for_label(self, id_):
        """Point the visible label at the number box.

        A ``MultiWidget`` has no single id, and Django's default is to render
        ``for=""`` — a label attached to nothing, which is worse than no label. The number
        is the control somebody is looking for; the country chooser carries its own name.
        """
        return f"{id_}_1" if id_ else ""

    def decompress(self, value):
        """Split a stored number back into the country and the rest.

        A number that was never in international form has no country to show, so the
        chooser falls back to the person's own and the number is shown exactly as it was
        stored. Nothing is silently rewritten on the way to the screen.
        """
        if not value:
            return [self.default_country, ""]
        found = phones.country_of(value)
        if found is None:
            return [self.default_country, value]
        rest = value.lstrip("+")[len(found.dialling) :]
        return [found.code, rest]


class PhoneField(forms.MultiValueField):
    """The pair, cleaned into one stored value."""

    widget = PhoneWidget

    def __init__(self, *, default_country: str = "", **kwargs):
        kwargs.setdefault("require_all_fields", False)
        fields = (
            forms.ChoiceField(
                choices=[("", "")] + [(row[0], row[0]) for row in phones.COUNTRIES],
                required=False,
            ),
            forms.CharField(max_length=40, required=False, strip=True),
        )
        super().__init__(fields=fields, **kwargs)
        self.widget.default_country = default_country

    def compress(self, values) -> str:
        if not values:
            return ""
        country, number = [*values, "", ""][:2]
        return phones.combine(number or "", country or "")
