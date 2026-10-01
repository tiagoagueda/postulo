"""A column that holds a language code, written the one way Postulo writes one (#337).

Nine places store a language: what somebody reads Postulo in, what their career is written
in, what each document declares, what a translation is a translation into. Each was a free
`CharField` of ten characters with nothing between it and whatever arrived, so the same
language was `pt-br` from a form, `pt-BR` from a test and `pt_BR` from a file, and nothing
could ask for one of them and find the others.

The field is what stands in between, and it is one place rather than a call at every write:
an archive is restored with `setattr` and `Model(**entry)`, a migration writes with
`update`, and the next column somebody adds would not have remembered. So a value is put in
its canonical form on the way into the database, however it got there, and a lookup is
prepared the same way: `filter(language="pt-br")` finds what was stored as `pt-BR`.

**Shaped like a tag, and no more than that.** A document may be in a language Postulo has
never heard of, and a record from elsewhere may say `pt-AO`. Only what Postulo itself offers
is held to the registry (`tests/test_language_registry.py`). What is not shaped like a tag
at all is refused where a person or a file hands it over, by `validate`; a value already in
a row is never rewritten into something else, only written properly.

Thirty-five characters, which RFC 5646 section 4.4.1 advises and `ca-ES-valencia` needs.
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from . import languages

#: Two codes to show somebody what one looks like. Handed to the sentence rather than
#: written in it, so that no catalogue holds a spelling of a code: the three that had this
#: sentence translated said `en-gb`, and would have gone on saying it.
EXAMPLES = ("en-GB", "pt-PT")


def refusal(value) -> ValidationError:
    """What is said about something that is not a language code, wherever it was handed over."""
    return ValidationError(
        _("“%(code)s” is not a language code, like %(first)s or %(second)s."),
        code="not_a_language",
        params={"code": str(value)[:20], "first": EXAMPLES[0], "second": EXAMPLES[1]},
    )


def validate(value) -> None:
    """Refuse what is not shaped like a language tag. Nothing is not a claim, and passes."""
    if value and not languages.well_formed(value):
        raise refusal(value)


class LanguageField(models.CharField):
    """A language tag in its canonical form, or blank where the model allows it."""

    # A tuple where Django writes a list: it is only ever read, and a list on a class is
    # one list for every field.
    default_validators = (validate,)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", languages.MAX_LENGTH)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return languages.tag(value) if isinstance(value, str) else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return languages.tag(value) if isinstance(value, str) else value

    def pre_save(self, model_instance, add):
        # On the instance as well as in the row, so that what was just saved reads back as
        # it was stored without being fetched again.
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


def _offered(choices) -> list[str]:
    """Every value a menu offers, out of the groups it may be drawn in."""
    found: list[str] = []
    for value, label in choices:
        if isinstance(label, list | tuple):
            found.extend(str(inner) for inner, _name in label)
        else:
            found.append(str(value))
    return found


class LanguageChoiceField(forms.ChoiceField):
    """One of the languages a form offers, taken however its code is spelt.

    A page drawn before the list was respelt posts ``pt-br``, and so does anything somebody
    wrote against the form: it is the same choice, and a refusal telling a person that the
    language they picked from the menu is not a valid choice would be true of nothing they
    did. What is kept is the choice as the menu spells it.
    """

    def to_python(self, value):
        value = super().to_python(value)
        return languages.find(value, _offered(self.choices)) or value


class LanguagesChoiceField(forms.MultipleChoiceField):
    """Several of the languages a form offers, each taken however its code is spelt."""

    def to_python(self, value):
        offered = _offered(self.choices)
        return [languages.find(one, offered) or one for one in super().to_python(value)]
