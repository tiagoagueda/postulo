"""Private file delivery.

Uploaded CVs are not served by the web server, so the view layer is the only thing
standing between a personal document and the internet.
"""

from pathlib import Path

import pytest
from django.http import Http404

from postulo.core.files import UnsafeMediaPath, resolve_media_path, serve_private_file


class FakeFieldFile:
    """The part of a FileField's value that serve_private_file actually uses."""

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture
def serve(rf):
    """Call serve_private_file and close any file handle it opens afterwards.

    A FileResponse left to the garbage collector shows up as an unraisable exception,
    which is noise in the test output rather than a finding about the code.
    """
    handles = []

    def _serve(field, **kwargs):
        response = serve_private_file(rf.get("/"), field, **kwargs)
        handle = getattr(response, "file_to_stream", None)
        if handle is not None:
            handles.append(handle)
        return response

    yield _serve

    for handle in handles:
        handle.close()


@pytest.fixture
def stored_file(settings) -> str:
    path = Path(settings.MEDIA_ROOT) / "cvs" / "backend-engineer.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7 pretend this is a CV")
    return "cvs/backend-engineer.pdf"


# ------------------------------------------------------------------ path safety


def test_a_normal_name_resolves_inside_media_root(settings, stored_file):
    resolved = resolve_media_path(stored_file)

    assert resolved.is_file()
    assert Path(settings.MEDIA_ROOT).resolve() in resolved.parents


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "cvs/../../secrets.txt",
        "cvs/../../../../../../Windows/System32/config/SAM",
    ],
)
def test_traversal_outside_media_root_is_refused(name):
    with pytest.raises(UnsafeMediaPath):
        resolve_media_path(name)


def test_a_traversing_name_produces_a_404_rather_than_a_file(rf):
    request = rf.get("/")

    with pytest.raises(Http404):
        serve_private_file(request, FakeFieldFile("../../../etc/passwd"))


def test_a_missing_file_produces_a_404(rf, settings):
    request = rf.get("/")

    with pytest.raises(Http404):
        serve_private_file(request, FakeFieldFile("cvs/never-existed.pdf"))


def test_an_empty_field_produces_a_404(rf):
    request = rf.get("/")

    with pytest.raises(Http404):
        serve_private_file(request, FakeFieldFile(""))


# --------------------------------------------------------------------- delivery


def test_the_file_is_streamed_by_default(serve, stored_file):
    response = serve(FakeFieldFile(stored_file))

    assert response.status_code == 200
    assert b"pretend this is a CV" in b"".join(response.streaming_content)


def test_private_documents_are_never_cached(serve, stored_file):
    response = serve(FakeFieldFile(stored_file))

    assert "no-store" in response["Cache-Control"]
    assert response["Cache-Control"].startswith("private")
    assert response["X-Content-Type-Options"] == "nosniff"


def test_the_content_type_follows_the_file_name(serve, stored_file):
    response = serve(FakeFieldFile(stored_file))

    assert response["Content-Type"] == "application/pdf"


def test_downloads_can_be_forced_and_renamed(serve, stored_file):
    response = serve(
        FakeFieldFile(stored_file),
        download_name="Tiago Agueda — CV.pdf",
        as_attachment=True,
    )
    disposition = response["Content-Disposition"]

    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition, "non-ASCII names need the RFC 6266 form"


def test_nginx_serves_the_bytes_when_configured(serve, settings, stored_file):
    settings.POSTULO_MEDIA_ACCEL_PREFIX = "/protected-media/"
    response = serve(FakeFieldFile(stored_file))

    assert response["X-Accel-Redirect"] == "/protected-media/cvs/backend-engineer.pdf"
    assert not response.content, "Django must not also send the body"


def test_apache_serves_the_bytes_when_configured(serve, settings, stored_file):
    settings.POSTULO_MEDIA_ACCEL_PREFIX = ""
    settings.POSTULO_MEDIA_SENDFILE = True
    response = serve(FakeFieldFile(stored_file))

    assert response["X-Sendfile"].endswith("backend-engineer.pdf")
    assert not response.content
