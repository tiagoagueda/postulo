"""Your details: the name and the contact block a CV prints (#309).

The caller's own and nobody else's: there is no id in the address, so there is nothing to
ask for somebody else's by. Reading needs ``read`` and changing needs ``write``, as
everywhere else.

``location`` is what was typed, and a blank is kept a blank: it means the primary postal
address's town and country, worked out whenever it is printed. ``printed_location`` is that
answer, read-only, from the same function the CV's header asks.
"""

from django.utils.translation import gettext as _
from ninja import Router
from ninja.errors import HttpError

from postulo.accounts.models import Profile

from ..auth import scope
from ..schemas import ProfileOut, ProfilePatch, profile_out

router = Router(tags=["profile"], auth=scope("read"))


def _mine(request) -> Profile:
    profile, _made = Profile.objects.select_related("user").get_or_create(user=request.auth.owner)
    return profile


@router.get("", response=ProfileOut, summary="Your details: the name and the contact block")
def get_profile(request):
    return profile_out(_mine(request))


@router.patch("", response=ProfileOut, auth=scope("write"), summary="Change your details")
def patch_profile(request, payload: ProfilePatch):
    """Change the name, the form of address, the pronouns, the headline or the location.

    A field left out is left alone. The form of address and the pronouns take any text:
    the lists *Your details* offers are a convenience of the page, and what is stored is the
    text either way. A first or last name may not be emptied, as on the page, because a job
    search is conducted under one's own name.

    Answers with the record as it now stands, as every `PATCH` here does, including one
    that changed nothing.
    """
    # Already stripped, measured and checked for NUL by the schema, in the page's order.
    data = {
        name: value for name, value in payload.dict(exclude_unset=True).items() if value is not None
    }
    for name in ("first_name", "last_name"):
        if name in data and not data[name]:
            raise HttpError(422, _("%(field)s may not be empty.") % {"field": repr(name)})
    profile = _mine(request)
    user = profile.user
    names = [name for name in ("first_name", "last_name") if name in data]
    for name in names:
        setattr(user, name, data.pop(name))
    if names:
        user.save(update_fields=names)
    for name, value in data.items():
        setattr(profile, name, value)
    # The name is on the account and `updated_at` is on the profile, and it is the profile's
    # that this call answers with: a change to the name alone moves it too, as saving the
    # page does, or a client comparing stamps would be told nothing had changed.
    if data or names:
        profile.save(update_fields=[*data, "updated_at"])
    return profile_out(profile)
