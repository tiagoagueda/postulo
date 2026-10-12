"""The log carries no member file names or captured addresses (#797)."""

from __future__ import annotations

import logging
import os
import time

import pytest

from postulo.documents import scrub

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_the_sweep_logs_a_count_and_not_a_name(caplog, media_root):
    stray = media_root / "documents" / "7" / "my-union-membership-letter.pdf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(PDF)
    old = time.time() - 100 * 3600
    os.utime(stray, (old, old))
    with caplog.at_level(logging.INFO, logger="postulo.documents.scrub"):
        removed = scrub.sweep()
    assert removed, "the stray was removed"
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "union-membership" not in text
    assert "1 orphan" in text


def test_a_failing_capture_source_does_not_log_the_address(caplog):
    from postulo.plugins import registry

    class Boom:
        name = "boom"

        def can_handle(self, url):
            return True

        def parse(self, url, html, **kw):
            raise RuntimeError("no")

    original = registry.available_sources
    registry.available_sources = lambda **kw: [Boom()]
    try:
        with caplog.at_level(logging.ERROR, logger="postulo.plugins.registry"):
            registry.parse_page("https://secret-employer.example/jobs/1", "<html></html>")
    finally:
        registry.available_sources = original
    assert "secret-employer" not in " ".join(r.getMessage() for r in caplog.records)
