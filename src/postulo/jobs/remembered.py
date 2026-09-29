"""Remembered places: the rows, and what a capture and its review do with them (#267).

Reading a page at a place is the built-in sources' business (`plugins.builtin.hints`). This
is Postulo's half: whose places they are, which ones a capture is read with, what the
review of that capture says about them, and what it teaches.

**At capture.** The owner's places for the page's site are handed to Postulo's own sources,
which try them below the recipes and schema.org and above what the page says about itself.
The capture remembers which places filled which field and which found nothing -- the review
screen marks the first, in words -- and, where the page's source was not kept, the bounded
list of the page's places that a correction is later recognised in. Where a source was kept
(`CapturedPage`, #256), nothing more is kept: the review reads the page from it.

**At review, and only there, the score is kept.** A place whose value the person saved as
it was did its job, and its count of failures goes back to nought. A place whose value they
changed, or that found nothing where they then typed something, failed; two failures in a
row and it is forgotten. A capture discarded, or never reviewed, says nothing either way.

**Only what was touched teaches.** A field the person left alone is not evidence that it was
right, so nothing is learned from it. A field they changed, to a value the page showed at a
place that survives a small change to the page, is remembered at that place for that site,
replacing whatever was there. The capture API's corrections are the same act, earlier, and
teach the same way.

**Never anybody else's.** Every row is its owner's, read through `for_user()`, handed only to
Postulo's own sources, carried in the owner's archive and gone with their account.

Learning is a courtesy to the next capture. Nothing here is allowed to be the reason a
capture was not made or a review was not saved: each failure is logged and forgotten.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.db import transaction

from postulo.plugins import registry
from postulo.plugins.base import RememberedPlace

from .models import CaptureStatus, FieldHint, HintField

logger = logging.getLogger(__name__)

#: What a remembered place did when a page was read: filled its field, or found nothing.
USED = "used"
MISSED = "missed"


def host_of(url: str) -> str:
    """The site a place is remembered for: the page's host, less a leading "www."."""
    try:
        host = (urlsplit(url or "").hostname or "").strip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")[:253]


# ------------------------------------------------------------------ reading a page


def remembered_for(owner, url: str) -> list[RememberedPlace]:
    """The owner's places for this page's site, in the shape a source is handed them."""
    host = host_of(url)
    if not host:
        return []
    fields = registry.hint_fields()
    handed = []
    for hint in FieldHint.objects.for_user(owner).filter(host=host).order_by("field"):
        place = registry.clean_place(hint.place)
        if place is not None and hint.field in fields:
            handed.append(RememberedPlace(field=hint.field, place=place))
    return handed


def read_page(owner, url: str, html: str):
    """Read a page the way a capture does: ``(data, source, handed)``, or ``None``.

    ``handed`` is what the sources were given, each with the ``outcome`` the source that
    answered set -- which is what `after_capture` keeps and the review screen marks.
    """
    handed = remembered_for(owner, url)
    # Asked with the places only where there are some, so that the question is the same
    # one it always was for everybody who has none.
    result = (
        registry.parse_page(url, html, hints=handed) if handed else registry.parse_page(url, html)
    )
    if result is None:
        return None
    data, source = result
    return data, source, handed


def _filled(pairs, data: dict) -> list[str]:
    """The posting fields that came from a remembered place and hold something."""
    fields = registry.hint_fields()
    names: list[str] = []
    for field, outcome in pairs:
        if outcome != USED:
            continue
        for name in fields.get(field, ()):
            if data.get(name) not in (None, "") and name not in names:
                names.append(name)
    return names


def filled_by(handed, data: dict) -> list[str]:
    """The posting fields a remembered place filled on this reading, for a caller to mark."""
    return _filled(((hint.field, hint.outcome) for hint in handed), data)


def marked(capture) -> list[str]:
    """The posting fields of a waiting capture whose value came from a remembered place."""
    entries = (capture.learning or {}).get("hints") or []
    pairs = (
        (entry.get("field"), entry.get("outcome")) for entry in entries if isinstance(entry, dict)
    )
    return _filled(pairs, capture.data or {})


# ------------------------------------------------------------------ comparing values


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return None


def _date(value) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _value(data: dict, field: str):
    """One field as a review compares it: spacing aside, the pay as its two figures.

    The pay is its figures and nothing else. The review form offers a currency and a period
    even where the page stated neither, so a person who touched neither would otherwise
    look as though they had corrected both.
    """
    if field == "salary":
        return (_decimal(data.get("salary_min")), _decimal(data.get("salary_max")))
    if field == "closes_at":
        return _date(data.get("closes_at"))
    return " ".join(str(data.get(field) or "").split())


def _empty(value) -> bool:
    if isinstance(value, tuple):
        return all(part is None for part in value)
    return value in (None, "")


def _learnable(data: dict, field: str):
    """The value a place is looked for by: text, a date, a word, or the pay's figures."""
    if field in ("salary", "closes_at"):
        return _value(data, field)
    return str(data.get(field) or "").strip()


def touched(read: dict, saved: dict) -> list[str]:
    """The fields the person changed to something: the only ones anything is learned from."""
    return [
        field
        for field in registry.hint_fields()
        if _value(read, field) != _value(saved, field) and not _empty(_value(saved, field))
    ]


# ------------------------------------------------------------------ the score


def _judge(entry: dict, read: dict, saved: dict, *, final: bool) -> str:
    """ "right", "wrong", "" for neither, or "later" where the review has still to say."""
    field, outcome = entry.get("field"), entry.get("outcome")
    changed = _value(read, field) != _value(saved, field)
    if outcome == USED:
        if changed:
            return "wrong"
        return "right" if final else "later"
    # It found nothing. Typed in by the person, or kept from the page below it, the value
    # was there to be found; left empty, there was nothing to find, as far as anybody knows.
    if _empty(_value(saved, field)):
        return "" if final else "later"
    if changed or final:
        return "wrong"
    return "later"


def _keep_score(owner, host: str, entries, read: dict, saved: dict, *, final: bool) -> list:
    """Keep the score of the places a capture was read with; the entries not yet judged.

    Only the place that was used is scored: one relearned or forgotten since is not the one
    this capture is evidence about.
    """
    fields = registry.hint_fields()
    later = []
    for entry in entries or ():
        if not isinstance(entry, dict) or entry.get("field") not in fields:
            continue
        place = registry.clean_place(entry.get("place"))
        if place is None or entry.get("outcome") not in (USED, MISSED):
            continue
        verdict = _judge(entry, read, saved, final=final)
        if verdict == "later":
            later.append(entry)
            continue
        if not verdict:
            continue
        hint = FieldHint.objects.for_user(owner).filter(host=host, field=entry["field"]).first()
        if hint is None or registry.clean_place(hint.place) != place:
            continue
        if verdict == "right":
            if hint.misses:
                hint.misses = 0
                hint.save(update_fields=["misses", "updated_at"])
        elif hint.misses + 1 >= FieldHint.DROPPED_AFTER:
            hint.delete()
        else:
            hint.misses += 1
            hint.save(update_fields=["misses", "updated_at"])
    return later


def _learn(owner, host: str, field: str, value, records) -> bool:
    """Remember where the page showed ``value``, for this site; whether anything was."""
    place = registry.learned_place(records, field, value)
    if place is None:
        return False
    FieldHint.objects.update_or_create(
        owner=owner, host=host, field=field, defaults={"place": place, "misses": 0}
    )
    return True


# ------------------------------------------------------------------ a capture's life


def after_capture(capture, handed, html: str, *, read=None, corrected=None) -> None:
    """What a new capture keeps for its review, and what corrections sent with it teach.

    ``read`` and ``corrected`` are the posting as the page was read and as the capture API's
    corrections left it; given, the corrections are scored and learned from now, exactly as
    a review would, and what they did not touch waits for the review. The page's places are
    kept only where its source was not (`CapturedPage`), and never its text.

    The page is read before anything is written, and each write is a transaction of its own
    with nothing slow inside it -- the rule #220 set for every writer on the SQLite file,
    which the errand that calls this keeps too.
    """
    try:
        host = host_of(capture.url)
        entries = [
            {"field": hint.field, "place": hint.place, "outcome": hint.outcome}
            for hint in handed or ()
            if hint.outcome in (USED, MISSED)
        ]
        page = capture.kept_page
        wants_places = bool(host) and not (page is not None and page.source)
        before = after = None
        changed: list[str] = []
        if host and read is not None and corrected is not None:
            before, after = read.model_dump(), corrected.model_dump()
            changed = touched(before, after)
        records = registry.page_places(capture.url, html) if changed or wants_places else []

        if before is not None:
            with transaction.atomic():
                entries = _keep_score(capture.owner, host, entries, before, after, final=False)
                for field in changed:
                    _learn(capture.owner, host, field, _learnable(after, field), records)
        learning: dict = {}
        if entries:
            learning["hints"] = entries
        if wants_places:
            learning["places"] = records
        if learning:
            capture.learning = learning
            with transaction.atomic():
                capture.save(update_fields=["learning"])
    except Exception:
        logger.exception("Could not note what capture %s can learn from", capture.pk)


def before_the_source_goes(page) -> None:
    """Keep what a waiting capture's review learns from, when its kept source is deleted.

    The review reads the page from the kept source where there is one, so nothing else was
    kept beside it; thrown away first, the source would take the lesson with it.
    """
    from . import kept

    capture = page.capture
    try:
        learning = dict(capture.learning or {})
        if capture.status != CaptureStatus.PENDING or "places" in learning:
            return
        if not host_of(capture.url):
            return
        learning["places"] = registry.page_places(capture.url, kept.read_source(page))
        capture.learning = learning
        with transaction.atomic():
            capture.save(update_fields=["learning"])
    except Exception:
        logger.exception("Could not keep what capture %s learns from", capture.pk)


def _records(capture) -> list:
    """The places of the page a capture was read from: from its kept source, else as kept."""
    from . import kept

    page = capture.kept_page
    if page is not None and page.source:
        try:
            html = kept.read_source(page)
        except (kept.Unreadable, OSError):
            html = ""
        if html:
            return registry.page_places(capture.url, html)
    return (capture.learning or {}).get("places") or []


def at_review(capture, saved: dict) -> None:
    """Keep the score and learn from a capture being saved. Its ``learning`` is emptied.

    ``saved`` is the posting as the person saved it (`PostingIntakeForm.posting_data`). The
    caller saves the capture, with ``learning`` among what it writes.
    """
    try:
        host = host_of(capture.url)
        if host:
            read = dict(capture.data or {})
            changed = touched(read, saved)
            # The page is read before the transaction opens; only the rows are written in it.
            records = _records(capture) if changed else []
            with transaction.atomic():
                entries = (capture.learning or {}).get("hints") or []
                _keep_score(capture.owner, host, entries, read, saved, final=True)
                for field in changed:
                    _learn(capture.owner, host, field, _learnable(saved, field), records)
    except Exception:
        logger.exception("Could not learn from the review of capture %s", capture.pk)
    capture.learning = {}


# ------------------------------------------------------------------ the person's own page


def sites(owner) -> list[dict]:
    """What is remembered, by site: ``[{"host", "fields": [label, ...]}]``, for Settings."""
    found: dict[str, list[str]] = {}
    labels = dict(HintField.choices)
    for hint in FieldHint.objects.for_user(owner).order_by("host", "field"):
        found.setdefault(hint.host, []).append(str(labels.get(hint.field, hint.field)))
    return [{"host": host, "fields": fields} for host, fields in found.items()]


def forget(owner, host: str) -> int:
    """Forget every place remembered for one site. How many there were."""
    host = (host or "").strip().lower()
    if not host:
        return 0
    deleted, _per_model = FieldHint.objects.for_user(owner).filter(host=host).delete()
    return deleted
