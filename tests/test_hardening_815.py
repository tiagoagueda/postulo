"""The OpenID Connect exception to the guarded client is written down, and stays true (#815)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_threat_model_names_the_exception():
    assert "OpenID Connect" in (ROOT / "docs" / "THREAT-MODEL.md").read_text(encoding="utf-8")


def test_the_provider_address_is_an_environment_setting_and_no_form_field():
    """The reason the exception is acceptable: nothing a page can write reaches it."""
    forms = (ROOT / "src" / "postulo" / "accounts" / "forms.py").read_text(encoding="utf-8")
    assert "server_url" not in forms and "oidc" not in forms.lower().replace("openid", "")
