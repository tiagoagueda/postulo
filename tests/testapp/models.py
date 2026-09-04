"""Models that exist only to exercise the shared foundations in postulo.core."""

from django.db import models

from postulo.core.models import OwnedModel


class Widget(OwnedModel):
    """A stand-in for any user-owned record.

    Testing OwnedModel through a real feature model would tie these tests to that
    feature's shape; this keeps them about ownership and nothing else.
    """

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"

    def __str__(self) -> str:
        return self.name
