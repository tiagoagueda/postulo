"""The choices a mail setting is stored as: how TLS gets on, and how the session signs in.

They are a field's choices, so `core.models` needs them the moment it loads, and they need
nothing but Django's `TextChoices` in return. The code that opens a session and the code that
fetches a token import them from here as well, which is why they are not beside either any
more: both of those reach up into the site's row, and a model reaching back into them was a
cycle held open by an import inside a function (#248). `core.mail` and `core.mail_auth` still
hand them out under their old names.
"""

from __future__ import annotations

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


class MailSecurity(TextChoices):
    """How TLS gets onto an SMTP session. Two ways, and they are not interchangeable.

    STARTTLS connects in the clear and asks the server to upgrade the socket; implicit TLS
    hands over a certificate before a byte of SMTP is spoken. Point one at the other's port
    and nothing happens until the timeout, because each is waiting for the other to speak.
    """

    NONE = "none", _("None")
    STARTTLS = "starttls", _("STARTTLS, after connecting")
    SSL = "ssl", _("TLS from the first byte")


class MailAuth(TextChoices):
    """How a mail server is told who is connecting.

    Blank keeps the meaning it has in every other column on the Email page: not set from the
    interface, so the environment answers -- and the environment's default is a password,
    which is what every existing instance uses.
    """

    PASSWORD = "password", _("A password, or an app password")
    XOAUTH2 = "xoauth2", _("XOAUTH2, with a token from the provider")


class MailGrant(TextChoices):
    """Which OAuth grant fetches the token, which is a question about who is consenting.

    **Signed in once** is an ordinary authorization-code grant: an operator agrees on a
    consent screen, as the mailbox that will be sending, and the refresh token is kept. It
    needs no administrator of anything and works at both providers.

    **The application sends on its own** is the client-credentials grant, which suits a
    server better -- nobody's session is involved and nothing expires because a person left.
    It needs a tenant administrator, an application registration and a permission scoped to
    one mailbox, which is Microsoft's route. Google's equivalent is a service account with
    domain-wide delegation, a different grant again and one not every operator can create, so
    it is not offered rather than offered and broken.
    """

    MAILBOX = "mailbox", _("Signed in once, as the mailbox that sends")
    APPLICATION = "application", _("The application sends on its own")
