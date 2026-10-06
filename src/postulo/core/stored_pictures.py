"""The row that holds a picture's bytes (#662), below every app that keeps one.

Apart from `core.models` because `accounts` and `jobs` both subclass it, and `core.models`
reaches back into both: a module of its own is what keeps the import graph without a cycle.
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class StoredPicture(models.Model):
    """The bytes of one picture, kept in the database beside the row that owns it (#662).

    An avatar and a company's logo are at most a mebibyte each, so they are a column and
    not a file: a subclass names its owner with a real foreign key and `on_delete=CASCADE`,
    which makes "the picture goes with its owner" a fact of the database and not a habit of
    the application. There is no receiver to forget, no file to find afterwards and no
    orphan to sweep, whichever way the owner goes -- the page, an account's deletion, a
    merge or the admin.

    **Written by `pictures.keep` and nothing else**, after the bytes have been through
    `as_stored` or `sanitise_svg`. The bytes are never loaded with the owner: the owner says
    on its own row whether it has one, and a view reads this table only to serve it.
    """

    data = models.BinaryField(_("picture"), editable=False)
    media_type = models.CharField(_("media type"), max_length=40, editable=False)
    stored_at = models.DateTimeField(_("stored at"), default=timezone.now, editable=False)

    class Meta:
        abstract = True
