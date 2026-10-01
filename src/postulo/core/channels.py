"""One contract every kind of contact detail obeys: shape, proof, and who does the proving.

Postulo holds three kinds of contact detail and each invented its own answer. An email
address is validated and confirmed by allauth over SMTP. A telephone number is checked less
than syntactically, on purpose, and confirmed by nothing. A postal address (#92) is neither.
Nothing could ask any of them the same question, and the questions are about to matter: a
number that becomes a way back into somebody's account (#144) has to be one somebody proved
they hold, and a channel that proves things needs to say *what carries the proof*.

So: one protocol, three answers to it, and a place to ask.

**Validation has three depths and this names them.** *Syntactic* — is this the shape of an
address, does this parse as E.164. *Plausible* — is this dialling range assigned, does this
domain have an MX record. *Real* — does anybody answer.

`plausible` is not what a channel checks, and that is a decision rather than an inheritance.
`phones.py` used to refuse it as well, because it needs the numbering plan of every country;
it carries the plans now (#304), and a number somebody types is read against its country's
before it is stored. That is the field's check, made once, where the number arrives. A
channel is asked a different question -- may a code be sent here, and did it come back -- and
the thing that proves a number is *sending to it and being told the code back*, which is the
`real` depth. A second reading of the plan here would sit between a value already read and a
check that settles the question anyway.

**Validated but not confirmable is a legitimate answer, not a missing feature.** A postal
address can only be proved by posting something to it. Some services do that; this one is not
going to, and a contract that treats "cannot be proved" as a gap would keep the postal channel
looking permanently unfinished.

**A confirmation is a message, so it needs whatever carries messages.** A channel names its
carrier rather than reaching for one, so the interlock that already protects the mail
transport (#104) has something to read when the question becomes "may this be switched off".

**allauth is described, not rewritten.** The email channel satisfies this contract by
delegation: allauth owns the token, the expiry, the resend and the `verified` flag, and this
says so rather than proposing a second implementation beside it. A contract an existing
working implementation cannot be described by is a contract nobody adopts.

**Why this is not a plugin kind yet**, though it is shaped like one. A plugin can be switched
off, and a channel that can be switched off is somebody's way back into their account that can
be switched off. Transports have an interlock for exactly that (#104) and channels would need
it extended before the two ideas could safely meet. The protocol below is the one a kind would
declare, so that step is a registration and not a redesign.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from django.utils.translation import gettext_lazy as _

# --------------------------------------------------------------- how deep a check went

#: The shape is right: it parses, it has the parts an address or a number has.
SYNTACTIC = "syntactic"
#: The value refers to something that exists: an assigned range, a domain with an MX
#: record. No channel checks this; see the module docstring for where it is checked.
PLAUSIBLE = "plausible"
#: Somebody answered. This is what a confirmation establishes, and nothing else does.
REAL = "real"

DEPTHS = (SYNTACTIC, PLAUSIBLE, REAL)


# ------------------------------------------------------------ how a value gets proved

#: Proved by following a link that only the holder receives. Right for an address.
LINK = "link"
#: Proved by typing back a short code. Right for a number, because a link in an SMS is a
#: phishing lesson nobody should be teaching.
CODE = "code"
#: Cannot be proved, ever, by this project. A legitimate answer, not a gap.
UNPROVABLE = "unprovable"

PROOFS = (LINK, CODE, UNPROVABLE)


@dataclass(frozen=True)
class Checked:
    """What checking a value found, and how deep the check went.

    `depth` matters as much as `ok`: a caller deciding whether a number may be somebody's
    way back into their account needs to know that a passing check went as far as the shape
    and no further.
    """

    ok: bool
    depth: str
    #: Why not, in the person's language. Empty when `ok`.
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


@runtime_checkable
class ContactChannel(Protocol):
    """One kind of contact detail, and everything the rest of Postulo needs to ask it."""

    #: Stable identifier. Policy rows and stored decisions would key on it, so it is fixed
    #: the way a plugin's name is.
    name: str
    #: What a person calls it.
    label: str
    #: How deep this channel's own checking goes. Never `REAL`: that is what confirming is.
    depth: str
    #: How a value on this channel gets proved, or `UNPROVABLE`.
    proof: str

    def check(self, value: str) -> Checked:
        """Whether this value has the shape this channel requires."""
        ...

    def carrier(self) -> str:
        """What would carry a confirmation, or an empty string if nothing can today.

        Two different empty answers, deliberately kept apart: a channel whose `proof` is
        `UNPROVABLE` will never have a carrier, and a channel that has one but has not been
        given it yet returns nothing until it is. `confirmable()` tells them apart.
        """
        ...


def confirmable(channel: ContactChannel) -> bool:
    """Whether a value on this channel could be proved on this instance, today."""
    return channel.proof != UNPROVABLE and bool(channel.carrier())


# ------------------------------------------------------------------- what one message says

#: How often one account may ask for a confirmation to be sent again, across every channel.
#: One setting rather than one per channel: a resend button is a way to make somebody's
#: phone buzz forty times, and the channel that costs money per send is not the one whose
#: limit anybody would remember to configure. `POSTULO_CAPTURE_RATE` is the precedent.
RESEND_RATE_SETTING = "POSTULO_CONFIRMATION_RATE"


def resend(who) -> None:
    """Spend one of this account's confirmation sends, or refuse."""
    from . import throttle

    throttle.consume("confirmation", who, throttle.rate_for(RESEND_RATE_SETTING))


def one_line(code: str) -> str:
    """A confirmation code as a single line of text, in the reader's language.

    Built here rather than by each channel, because a code arriving on a number has no
    interface around it to supply context: no page, no sender name a person recognises, no
    heading. The line has to say which instance sent it and what it is for, or it is a
    stranger's text message containing six digits, which is the shape of every scam there
    is.
    """
    from . import site

    return str(
        _("%(code)s is your confirmation code for %(instance)s. Nobody will ask you for it.")
        % {"code": code, "instance": site.instance_name()}
    )


# ------------------------------------------------------------------------ the register

_CHANNELS: dict[str, ContactChannel] = {}


def register(channel: ContactChannel) -> None:
    _CHANNELS[channel.name] = channel


def unregister(name: str) -> None:
    _CHANNELS.pop(name, None)


def all_channels() -> list[ContactChannel]:
    return sorted(_CHANNELS.values(), key=lambda channel: channel.name)


def find(name: str) -> ContactChannel | None:
    return _CHANNELS.get(name)


# ---------------------------------------------------------------- the three that exist

EMAIL = "email"
TELEPHONE = "telephone"


class EmailChannel:
    """An address, described rather than reimplemented.

    allauth owns every part of this: the `EmailAddress` rows, the token, its expiry, the
    resend, the link and the `verified` flag. Nothing here duplicates any of it. What this
    adds is the ability to *ask* — so code that has a channel in its hand can find out that
    an address is confirmed by a link carried by the mail transport, without knowing that
    allauth is the thing on the other side.
    """

    name = EMAIL
    label = _("Email address")
    depth = SYNTACTIC
    proof = LINK

    def check(self, value: str) -> Checked:
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        try:
            validate_email((value or "").strip())
        except ValidationError:
            return Checked(False, SYNTACTIC, str(_("That is not the shape of an email address.")))
        return Checked(True, SYNTACTIC)

    def carrier(self) -> str:
        """Whatever is carrying the mail. Named, not assumed to be SMTP.

        An instance running a transport plugin confirms addresses through that plugin, and
        the interlock that stops it being switched off (#104) is reading the same answer.
        """
        from postulo.notifications import transport

        chosen = transport.selected()
        return getattr(chosen, "name", "") if chosen is not None else ""


class TelephoneChannel:
    """A number, checked as far as its shape and no further.

    This raises telephone validation from *less than syntactic* to *syntactic*, which is the
    part that costs nothing: a number in international form starts with `+`, carries a
    dialling code that is assigned to somewhere, and holds between four and fifteen digits
    because E.164 says fifteen and nothing real is shorter than four.

    **A number that fails this may still be one that is kept.** What a field refuses is
    `phones.check`'s to say (#304): a number its country's plan calls impossible. A number
    the plan cannot place -- one with no country in front, a short number, words -- is kept
    as it was typed, and fails here. This reports; whether a caller refuses is the caller's
    business, and the one caller that will refuse is the one asking whether this number may
    be a way back into an account (#142).
    """

    name = TELEPHONE
    label = _("Telephone number")
    depth = SYNTACTIC
    proof = CODE

    #: E.164 allows fifteen digits including the country code. Four is not a rule anywhere;
    #: it is a floor low enough that no real number is below it and high enough to catch a
    #: field somebody typed two digits into.
    MIN_DIGITS = 4
    MAX_DIGITS = 15

    def check(self, value: str) -> Checked:
        from . import phones

        international = phones.normalise(value)
        if not international:
            return Checked(
                False,
                SYNTACTIC,
                str(
                    _(
                        "That number has no country in front of it, so nowhere else can dial "
                        "it. Choose the country, or write it starting with +."
                    )
                ),
            )
        digits = international.lstrip("+")
        if not (self.MIN_DIGITS <= len(digits) <= self.MAX_DIGITS):
            return Checked(
                False,
                SYNTACTIC,
                str(
                    _(
                        "A telephone number has between %(low)d and %(high)d digits, and "
                        "that one has %(count)d."
                    )
                    % {"low": self.MIN_DIGITS, "high": self.MAX_DIGITS, "count": len(digits)}
                ),
            )
        return Checked(True, SYNTACTIC)

    def carrier(self) -> str:
        """Whatever this instance can reach a telephone with, and usually nothing.

        A number is confirmable — by a code, typed back. What is usually missing is anything
        that can carry a message to one: Postulo ships no text gateway and names no vendor,
        because every one of them is somebody else's jurisdiction (#143). An operator who
        installs one turns this into a name, and `confirmable()` says yes from that moment
        without anything here changing.
        """
        from postulo.notifications import text

        carrier = text.gateway()
        return getattr(carrier, "name", "") if carrier is not None else ""


def register_the_ones_that_exist() -> None:
    """Called from the app's `ready()`. Idempotent, so a reload does not double up."""
    register(EmailChannel())
    register(TelephoneChannel())
