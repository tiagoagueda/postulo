"""A choice field that is sent a number the database cannot hold says so, and does not crash (#767).

Django turns a value the client sent for a model choice into a primary key, and asks the
database for it. A number past what the column holds -- thirty digits in a tag box -- is not a
``ValueError`` and not a ``DoesNotExist``, which is all the field expects: with SQLite it is
an ``OverflowError`` from the driver, and the request answered 500 with a traceback in the log.
It is an invalid choice like any other, and is reported as one.

Done once, here, on the two fields every model choice in the project is made of, instead of on
the fifteen places that declare one: a field added tomorrow is covered the day it is added.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.forms import models as forms_models


def install() -> None:
    """Wrap the two methods, once. Safe to call again."""
    single = forms_models.ModelChoiceField
    multiple = forms_models.ModelMultipleChoiceField
    if getattr(single.to_python, "_postulo_safe", False):
        return

    original_one = single.to_python
    original_many = multiple._check_values

    def to_python(self, value):
        try:
            return original_one(self, value)
        except OverflowError as error:
            raise ValidationError(
                self.error_messages["invalid_choice"],
                code="invalid_choice",
                params={"value": value},
            ) from error

    def check_values(self, value):
        try:
            return original_many(self, value)
        except OverflowError as error:
            raise ValidationError(
                self.error_messages["invalid_choice"],
                code="invalid_choice",
                params={"value": value},
            ) from error

    to_python._postulo_safe = True
    single.to_python = to_python
    multiple._check_values = check_values
