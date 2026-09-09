"""Acting on several rows at once, and the one rule that makes it safe to.

Every view in Postulo until now has fetched *one* object through `OwnedObjectMixin`, where a
foreign id is a 404. A bulk action receives **a list of ids from the client**, which is exactly
the shape of request that goes wrong, so the rule is written here once instead of at each call
site.

**Every id is re-scoped, and the action works on the intersection.** Not on the list that was
sent, and not by refusing the whole batch when one id is foreign — refusing is an answer, and
an answer tells the sender which ids exist. Forty ids of which thirty-nine are somebody else's
does the one, says "1 changed", and reveals nothing. That is the same shape as the 404 for a
single object, extended to a list.

Four decisions the shape of this feature forced, taken here rather than in a template.

**Only additive actions, in this version.** Tag them, move them along. Deleting forty things is
a different act from deleting one: for a company it cascades to every posting under it, which
the interface says plainly when it is one company and would be saying about an unseen number
when it is forty. That confirmation deserves its own design, and until it has one there is no
bulk delete.

**A changed query clears the selection.** Tick twelve, narrow the filter to four, press the
button — every answer surprises somebody, so the one that surprises them harmlessly wins. The
checkboxes are form state on a page, and a filter change loads a new page with fresh ones. It
is also what happens naturally, which means there is no way for it to drift out of true.

**Select-all means this page and is a button, not a header checkbox.** A checkbox in a header
row is inert without script, and an inert control is worse than a missing one. `app.js` adds a
button saying exactly what it does; without script, ticking rows works and nothing is broken.
"All 340 matching" is not offered: it is a different promise, it has to work from the query
rather than a list of ids, and it needs its own step.

**It is a form before it is anything else.** Checkboxes sharing a name and a submit button are
a complete implementation, and that is the version that exists. Everything scripted is added on
top of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.translation import gettext as _

#: The field every selected row is posted under.
CHOSEN = "chosen"
#: The field naming which action to take.
ACTION = "bulk-action"

#: How many rows one submission may name. Far more than a page holds, and a bound on a list
#: that arrives from the client: without it, a hundred thousand ids is one request.
MAX_CHOSEN = 1000


@dataclass(frozen=True)
class Action:
    """One thing that can be done to several rows."""

    key: str
    label: str
    #: The name of a companion field the action needs — a tag to add, a status to move to.
    #: Empty for an action that needs nothing else.
    needs: str = ""
    #: What to call the companion field on screen.
    needs_label: str = ""
    #: The choices for it, as ``(value, label)``. Filled per request when they are a person's
    #: own rows rather than a fixed list.
    choices: tuple = field(default_factory=tuple)


def chosen_ids(request) -> list[int]:
    """The ids that were ticked, as integers, bounded and de-duplicated.

    Anything unparseable is dropped rather than raising: a list from the client is not a
    promise, and a form that 500s on a stray value is a form somebody can break for fun.
    """
    seen: dict[int, None] = {}
    for raw in request.POST.getlist(CHOSEN)[:MAX_CHOSEN]:
        try:
            seen.setdefault(int(raw), None)
        except (TypeError, ValueError):
            continue
    return list(seen)


def chosen_rows(request, model):
    """The rows this person actually owns, out of the ids they sent.

    The whole of the safety of this feature is in this function. `for_user()` is what makes a
    list of ids from a stranger's browser into a list of this person's rows, and the caller
    never sees the difference between "you did not send that" and "that is not yours".
    """
    ids = chosen_ids(request)
    if not ids:
        return model.objects.none()
    return model.objects.for_user(request.user).filter(pk__in=ids)


def nothing_chosen() -> str:
    return str(_("Nothing was selected, so nothing changed."))


def changed(count: int, noun: tuple[str, str]) -> str:
    """What to say afterwards: the number, and nothing about what was not changed.

    Deliberately says how many *did* change rather than how many were sent. The difference
    between the two is the number of rows belonging to somebody else, and that is a fact
    nobody outside this account is entitled to learn.
    """
    singular, plural = noun
    # One string with the noun already in the right number, rather than a plural form: the
    # noun is the table's, so a plural form here would have to pluralise a word it does not
    # know. The same trick the transport interlock uses for the same reason.
    return str(
        _("%(count)d %(noun)s changed.")
        % {"count": count, "noun": singular if count == 1 else plural}
    )
