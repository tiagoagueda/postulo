"""Industries are drawn as badges with an icon and a tone (#856)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs import industries
from postulo.jobs.models import Company, Industry

pytestmark = pytest.mark.django_db


def test_every_nace_section_has_a_look_with_a_real_icon_and_tone():
    from postulo.core.templatetags.postulo import _icon_source

    sections = set(industries.classification()["sections"])

    assert sections == set(industries.LOOKS)
    for icon, tone in industries.LOOKS.values():
        assert _icon_source(icon)
        assert tone in {"grey", "blue", "amber", "violet", "teal", "green", "rose", "brand"}


def test_a_division_inherits_its_sections_look_and_a_made_up_word_is_plain():
    assert industries.look_of("62") == industries.LOOKS[industries.section_of("62")]
    assert industries.look_of("") == industries.PLAIN_LOOK
    assert industries.look_of("zz") == industries.PLAIN_LOOK


def test_the_company_page_and_table_draw_the_badge(client, user):
    company = Company.objects.create(owner=user, name="Aperture")
    mine = Industry.named(user, [industries.name_for("10"), "Home-made words"])
    company.industries.set(mine)
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    table = client.get(reverse("jobs:company_list")).content.decode()

    for html in (page, table):
        icon, tone = industries.look_of("10")
        assert f'data-icon="{icon}"' in html and f'data-tone="{tone}"' in html
        assert 'data-icon="tag"' in html
    assert "?industry=" in page, "the badge is still a link to the filter"
