"""A certification's issuer is a company, and the awarding body is a role of one (#686).

The role is divisions 85 and 94 of NACE Rev. 2.1, and it only puts likely issuers first:
a vendor that certifies its own users is classified by what it sells, so nothing is turned
away and nothing blocks a save. The notice says when the company carries no code at all.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs import industries, recall, roles
from postulo.jobs.models import Company, Industry
from postulo.resume.forms import CertificationForm
from postulo.resume.models import Certification

pytestmark = pytest.mark.django_db

EDUCATION = industries.name_for("85", "en")
MEMBERSHIP = industries.name_for("94", "en")


def company(owner, name, *industry_names):
    made = Company.objects.create(owner=owner, name=name)
    if industry_names:
        made.industries.set(Industry.named(owner, industry_names))
    return made


def test_the_awarding_body_is_divisions_85_and_94():
    assert roles.role(roles.AWARDING_BODY).divisions == {"85", "94"}


def test_every_code_in_the_registry_exists_in_the_vendored_file():
    divisions = industries.classification()["divisions"]
    for held in roles.ROLES.values():
        for code in held.divisions:
            assert code in divisions, f"{held.key}: {code} is not in nace-2.1.json"
    assert EDUCATION and MEMBERSHIP


def test_education_and_a_membership_organisation_qualify_and_a_vendor_does_not(user):
    school = company(user, "Academy", EDUCATION)
    society = company(user, "Chamber", MEMBERSHIP)
    vendor = company(user, "Cloud Co", "Banking")
    qualifying = Company.objects.for_user(user).qualifying(roles.AWARDING_BODY)
    assert set(qualifying) == {school, society}
    assert vendor not in qualifying


def test_the_issuer_box_puts_awarding_bodies_first_and_loses_no_company(user):
    company(user, "Zeta Vendor")
    company(user, "Alpha Vendor")
    company(user, "Zed Academy", EDUCATION)
    company(user, "Bar Society", MEMBERSHIP)
    names = recall.companies(user, including_career=True, role=roles.AWARDING_BODY)
    assert names[:2] == ["Bar Society", "Zed Academy"]
    assert sorted(names) == sorted(["Alpha Vendor", "Bar Society", "Zed Academy", "Zeta Vendor"])
    assert sorted(names) == sorted(recall.companies(user, including_career=True))


def test_the_issuer_box_offers_a_former_employer_and_never_another_accounts_company(
    user, other_user
):
    company(other_user, "Their Academy", EDUCATION)
    mine = company(user, "Old Employer")
    mine.from_career = True
    mine.save()
    names = CertificationForm(user=user).datalists["issuer-suggestions"]
    assert names == ["Old Employer"]


def make(user, issuer="Cloud Co", *industry_names):
    entry = Certification.objects.create(owner=user, name="CKA", issuer=issuer)
    entry.company = company(user, issuer, *industry_names)
    entry.save()
    return entry


def test_the_notice_shows_for_a_company_with_no_coded_industry(client, user):
    entry = make(user)
    client.force_login(user)
    page = client.get(reverse("resume:item_update", args=["certification", entry.pk]))
    body = page.content.decode()
    assert "none of its industries from the NACE list yet" in body
    assert entry.company.get_absolute_url() in body


def test_an_invented_industry_has_no_code_so_the_notice_still_shows(client, user):
    entry = make(user, "Cloud Co", "Cloud wizardry")
    assert not entry.company.has_nace_industry
    client.force_login(user)
    page = client.get(reverse("resume:item_update", args=["certification", entry.pk]))
    assert "none of its industries from the NACE list yet" in page.content.decode()


@pytest.mark.parametrize("industry", [EDUCATION, industries.name_for("62", "en")])
def test_no_notice_for_a_company_with_any_coded_industry(client, user, industry):
    entry = make(user, "Somebody", industry)
    assert entry.company.has_nace_industry
    client.force_login(user)
    page = client.get(reverse("resume:item_update", args=["certification", entry.pk]))
    assert "none of its industries from the NACE list" not in page.content.decode()


def test_the_notice_never_blocks_the_save(client, user):
    client.force_login(user)
    response = client.post(
        reverse("resume:item_create", args=["certification"]),
        {"name": "CKA", "issuer": "Brand New Body"},
        follow=True,
    )
    entry = Certification.objects.get(owner=user)
    assert entry.company.name == "Brand New Body"
    texts = [str(message) for message in response.context["messages"]]
    assert any("none of its industries from the NACE list yet" in text for text in texts)


def test_an_experience_gets_no_such_notice(client, user):
    from postulo.resume.models import Experience

    entry = Experience.objects.create(
        owner=user, organisation="Acme", role="Dev", start_date="2020-01-01"
    )
    entry.company = company(user, "Acme")
    entry.save()
    client.force_login(user)
    page = client.get(reverse("resume:item_update", args=["experience", entry.pk]))
    assert "NACE" not in page.content.decode()
