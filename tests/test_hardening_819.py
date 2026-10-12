"""A wheel download is saved under a name made here unless the address names a wheel (#819)."""

from __future__ import annotations

import pytest

from postulo.plugins.catalogue import _wheel_file_name


def test_a_real_wheel_name_is_kept():
    url = "https://example.org/dl/postulo_helloworld-0.3.1-py3-none-any.whl"
    assert _wheel_file_name(url) == "postulo_helloworld-0.3.1-py3-none-any.whl"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/dl/",
        "https://example.org/dl/..%2f..%2fx.whl",
        "https://example.org/dl/CON.whl",
        "https://example.org/dl/" + "a" * 300 + ".whl",
        "https://example.org/dl/not-a-wheel.txt",
        "https://example.org/dl/evil name.whl?x=1",
        "https://example.org/dl/a:b-1-py3-none-any.whl",
    ],
)
def test_anything_else_gets_a_name_made_here(url):
    name = _wheel_file_name(url)
    assert name.startswith("download-") and name.endswith(".whl")
    assert "/" not in name and ":" not in name and len(name) < 60


def test_two_downloads_do_not_share_a_made_up_name():
    url = "https://example.org/dl/whatever"
    assert _wheel_file_name(url) != _wheel_file_name(url)
