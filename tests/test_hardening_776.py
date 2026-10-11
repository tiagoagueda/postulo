"""The account's kept-page quota holds when uploads arrive together (#776)."""

from __future__ import annotations

import io

import pytest

from postulo.jobs import pages
from postulo.jobs.models import Capture, CapturedPage

from .test_captured_pages import PNG, keeping  # noqa: F401

pytestmark = pytest.mark.django_db


class InterleavedStream(io.BytesIO):
    """A request body during whose read another request of the same account finishes."""

    def __init__(self, data, during):
        super().__init__(data)
        self.during = during

    def read(self, *args):
        if self.during is not None:
            run, self.during = self.during, None
            run()
        return super().read(*args)


def test_a_second_upload_that_found_room_when_it_began_gives_way_if_the_first_took_it(
    keeping,  # noqa: F811
    settings,
):
    first = Capture.objects.create(owner=keeping, url="https://example.org/j/1", data={})
    second = Capture.objects.create(owner=keeping, url="https://example.org/j/2", data={})
    settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES = len(PNG) * 3 // 2

    def the_other_request():
        pages.attach_rendering(second, io.BytesIO(PNG), content_type="image/png", length=len(PNG))

    with pytest.raises(pages.NotKept) as refused:
        pages.attach_rendering(
            first,
            InterleavedStream(PNG, the_other_request),
            content_type="image/png",
            length=len(PNG),
        )

    assert refused.value.reason == "no-room"
    assert pages.weight_of(keeping) <= settings.POSTULO_CAPTURE_ACCOUNT_MAX_BYTES
    assert CapturedPage.objects.filter(capture=second).exclude(rendering="").exists()
    assert not CapturedPage.objects.filter(capture=first).exists(), "and its file is gone with it"


def test_uploads_that_fit_are_all_kept(keeping, settings):  # noqa: F811
    for n in range(3):
        capture = Capture.objects.create(owner=keeping, url=f"https://example.org/j/{n}", data={})
        pages.attach_rendering(capture, io.BytesIO(PNG), content_type="image/png", length=len(PNG))

    assert CapturedPage.objects.count() == 3
