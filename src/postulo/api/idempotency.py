"""Sending the same request twice, and getting the same answer twice.

Capturing was the one call here that made something every time it was asked, while its own
comment took retrying for granted: a client that sends a posting and never sees the ``201``
cannot tell a lost reply from a lost request, and the safe thing for it to do is send it
again. It then had two captures of one posting to decline, and had been told about it twice
(#230). ``/captures/known`` was only ever advice, asked before sending and answered
honestly, which does nothing for the answer that went missing afterwards.

So a capture may carry an ``Idempotency-Key`` — any string the client invents per posting,
a UUID being the obvious choice — and the first answer given under that key is the answer
it keeps getting, for a day, without the page being read or anybody being told a second
time. The key belongs to the account rather than to the token, because a person retrying
from a second tool is retrying the same gesture.

The key is claimed before the work starts, so two requests racing each other cannot both
do it: the second is told the first is still being answered rather than quietly making a
duplicate. A claim whose request then failed is given up again, because a capture that was
refused is exactly the one worth sending again.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja.errors import HttpError

from .models import IDEMPOTENCY_WINDOW, IdempotentRequest

#: Said to a program, and read by the person debugging it, as every other refusal here is.
BUSY = _("Another request is using this Idempotency-Key; try again in a moment.")
REUSED = _("This Idempotency-Key was already used for a different capture.")


def fingerprint(payload) -> str:
    """One hash standing for what was asked, so a reused key cannot answer a new question."""
    return hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest()


class Answer:
    """One request's place in the record: what was answered before, and what to keep now."""

    def __init__(self, owner, key: str | None, payload) -> None:
        self.owner = owner
        self.key = key
        self.payload = payload
        #: The answer this key already has, or ``None`` when the work is still to be done.
        self.held: dict | None = None
        self.held_status = 0
        self._claimed = False
        self._kept = False

    def keep(self, status_code: int, body: dict) -> None:
        """Remember this answer as the one the key stands for from now on."""
        self._kept = True
        if self._claimed:
            IdempotentRequest.objects.filter(owner=self.owner, key=self.key).update(
                status_code=status_code, body=body, updated_at=timezone.now()
            )


@contextmanager
def once(owner, key: str | None, payload):
    """Claim ``key`` for this request, or hand back the answer it already has.

    Without a key nothing is recorded and nothing is replayed: a client that does not ask
    for this gets exactly what it always got.
    """
    answer = Answer(owner, key, payload)
    if key is None or not key.strip():
        yield answer
        return

    answer.key = key.strip()
    now = timezone.now()
    IdempotentRequest.objects.filter(owner=owner, created_at__lt=now - IDEMPOTENCY_WINDOW).delete()

    asked = fingerprint(payload)
    try:
        with transaction.atomic():
            record = IdempotentRequest.objects.create(
                owner=owner, key=answer.key, fingerprint=asked
            )
    except IntegrityError:
        record = IdempotentRequest.objects.filter(owner=owner, key=answer.key).first()
        if record is not None and record.fingerprint != asked:
            # Said whether or not the first one has finished: a key used twice for two
            # different postings is a bug in the client either way, and answering it with
            # somebody else's capture would hide it.
            raise HttpError(422, str(REUSED)) from None
        if record is None or not record.is_answered:
            # Either the identical request is still being answered, or the row it claimed the
            # key with expired between the sweep above and the lookup here. Both mean the key
            # is spoken for by something unfinished, and neither of them is an answer.
            raise HttpError(409, str(BUSY)) from None
        answer.held, answer.held_status = record.body, record.status_code
        yield answer
        return

    answer._claimed = True
    try:
        yield answer
    except BaseException:
        record.delete()
        raise
    if not answer._kept:
        record.delete()
