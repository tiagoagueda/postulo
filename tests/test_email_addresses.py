"""Several email addresses as a feature, and the honest limit of what it governs (#145).

> multiple e-mails shoud became an internal plugin as we did to the phone numbers and play
> the same role on the recovery of the user acount

Everything the request asks for already worked — allauth's `EmailAddress` is the shape #90
*copied* when telephone numbers were built. What framing it as a plugin raised is a question
about plugins rather than about email: **may a feature govern a page it does not own the data
behind?**

Yes, and these tests are the answer with its price attached. Off means Postulo offers the
primary address and stops offering the management page; it deletes nothing, because it owns
nothing. And it can never take the page from somebody already relying on it, because that is
a lock-out arriving through a setting nobody thought was about recovery.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from postulo.accounts import addresses
from postulo.plugins.email_addresses import EMAIL_ADDRESSES

pytestmark = pytest.mark.django_db


@pytest.fixture
def switched_off(monkeypatch):
    """An administrator has switched the feature off for everybody."""
    from postulo.plugins import policy

    real = policy.decide

    def decide(name, person):
        if name == EMAIL_ADDRESSES:
            return policy.Decision(
                on=False, offered=False, theirs=False, decided_by="administrator"
            )
        return real(name, person)

    monkeypatch.setattr(policy, "decide", decide)
    monkeypatch.setattr(addresses, "decide", decide, raising=False)


def only_address(user, *, verified: bool) -> EmailAddress:
    user.emailaddress_set.all().delete()
    return EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=verified)


# ----------------------------------------------------------- what it governs


def test_it_is_a_feature_that_owns_no_data():
    """The limit worth stating rather than working around.

    `EmailAddress` belongs to `allauth.account`, with allauth's migrations behind it and
    allauth's flows reading it: verification, password reset, signing in by address, linking
    a social account. A plugin taking that over would be a plugin owning a library's core
    model — and one that cannot verify an address cannot honestly own one.
    """
    import ast
    import inspect

    from postulo.plugins import email_addresses
    from postulo.plugins.base import manifest_of

    tree = ast.parse(inspect.getsource(email_addresses))
    # The code, not the prose: the docstring talks about allauth at length, because saying
    # what a plugin may not own is the point of this one.
    ast.get_docstring(tree) and tree.body.pop(0)
    code = ast.unparse(tree)

    assert "allauth" not in code, "the model is a library's, and stays that way"
    assert "emailaddress_set" not in code, "the plugin reads no addresses"
    assert email_addresses.EmailAddressesFeature.kind == "feature"
    # And the description says what off actually does, rather than implying more.
    said = str(manifest_of(email_addresses.EmailAddressesFeature()).description)
    assert "Nothing is deleted" in said


def test_on_by_default(user):
    """Nothing about installing Postulo takes an address away from anybody."""
    assert addresses.is_offered(user) is True


def test_off_hides_the_page_and_deletes_nothing(user, switched_off, client):
    only_address(user, verified=True)
    client.force_login(user)

    assert addresses.is_offered(user) is False
    html = client.get(reverse("settings:account")).content.decode()
    assert reverse("account_email") not in html, "the link is what goes away"
    assert user.emailaddress_set.count() == 1, "and the address is still there"


def test_the_primary_address_is_still_shown_when_it_is_off(user, switched_off, client):
    """Off is what Postulo did before anybody thought about it: one address, the one on the
    account. Exactly `phone-numbers`' "off shows the primary".
    """
    only_address(user, verified=True)
    client.force_login(user)

    html = client.get(reverse("settings:account")).content.decode()

    assert user.email in html


# ------------------------------------------------- and what it may never take away


def test_somebody_who_already_has_two_keeps_the_page(user, switched_off, client):
    """The failure this exists to prevent, and the reason the floor is not optional.

    Somebody keeps a second address because the first is a work account they are about to
    lose. Hiding the page does not remove the address — it removes their ability to promote
    it on the day they need to, which is a lock-out arriving through a setting nobody thought
    was about recovery.
    """
    only_address(user, verified=True)
    EmailAddress.objects.create(
        user=user, email="alex@personal.example", primary=False, verified=True
    )

    assert addresses.is_offered(user) is True

    client.force_login(user)
    html = client.get(reverse("settings:account")).content.decode()
    assert reverse("account_email") in html


def test_somebody_whose_only_address_is_unverified_keeps_the_page(user, switched_off):
    """The page is what fixes exactly that, so it cannot be the thing withheld."""
    only_address(user, verified=False)

    assert addresses.is_offered(user) is True


def test_the_floor_is_below_the_policy_rather_than_beside_it(user, switched_off):
    """Not "the administrator's decision is ignored" — it applies to everybody it can apply
    to without stranding them, which is everybody who is not already relying on this.
    """
    only_address(user, verified=True)
    assert addresses.is_offered(user) is False

    EmailAddress.objects.create(user=user, email="second@example.org", verified=False)
    assert addresses.is_offered(user) is True


# --------------------------------------------------- the division of labour


def test_the_plugin_does_not_ask_whether_it_is_switched_on():
    """A plugin marking its own homework. Asking the policy is Postulo's job, which is the
    same division `phone-numbers` uses and the reason this file imports two modules.
    """
    import inspect

    from postulo.plugins import email_addresses

    assert "decide" not in inspect.getsource(email_addresses)
    assert "decide" in inspect.getsource(addresses)
