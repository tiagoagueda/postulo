"""What a username may look like.

Lowercase, because a username is typed from memory and "Alex" and "alex" must never be two
people. Short, because it appears beside the name in the header. Letters, digits, dots,
underscores and hyphens, because that is what people expect from every other service, and
because anything else would need escaping somewhere.
"""

import re

from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
# Three to thirty-two characters, starting and ending with a letter or digit. The middle
# group is obligatory, so a one- or two-character name is refused as the message promises.
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$")

username_validator = RegexValidator(
    regex=USERNAME_PATTERN,
    message=_(
        "Use 3 to 32 lowercase letters, digits, dots, underscores or hyphens, "
        "starting and ending with a letter or digit."
    ),
    code="invalid_username",
)

#: What allauth applies to a username at signup, alongside its own length and blacklist.
username_validators = [username_validator]

#: Names that would confuse, impersonate, or collide with something the instance owns.
USERNAME_BLACKLIST = [
    "admin",
    "administrator",
    "postulo",
    "root",
    "support",
    "system",
    "staff",
    "me",
    "null",
    "undefined",
]


def slug_from_email(email: str) -> str:
    """The best username the local part of an address suggests, padded to a valid one.

    ``alex.morgan@example.org`` becomes ``alex.morgan``; characters outside the alphabet
    become hyphens; a result too short to be valid is extended, and an empty one becomes
    ``user``. Uniqueness is the caller's business.
    """
    local = email.split("@", 1)[0].casefold()
    candidate = re.sub(r"[^a-z0-9._-]+", "-", local).strip("._-")
    candidate = re.sub(r"[._-]{2,}", "-", candidate)[:USERNAME_MAX_LENGTH].strip("._-")
    if not candidate:
        candidate = "user"
    while len(candidate) < USERNAME_MIN_LENGTH:
        candidate += "0"
    return candidate
