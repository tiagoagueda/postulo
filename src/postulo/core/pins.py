"""What the environment holds of the Server pages' settings (#1186).

An administrator's setting lives in a `SiteSettings` field. Where its variable is set, the
environment wins: the row is read with the variable's value laid over the field's, so
everything that reads the row (the accessors in `postulo.core.site`, a transport's
configuration, the scheduler's backup slot) answers with what the instance was started with,
and none of them needs to know where it came from. The stored value is not touched: `save`
puts it back before it writes, so removing the variable gives the stored value again.

The fields here are the ones with no accessor of their own. The older ones (registration, the
capture switches, the mail columns, the map and the geocoder) are read through
`postulo.core.site`, which asks `overridden_by` itself.

This module imports nothing of Postulo's but the registry, so the model can import it.
"""

from __future__ import annotations

from . import configuration

#: Each field and the variable that pins it. A field named twice would be a mistake the test
#: that compares this to the registry catches.
FIELDS: dict[str, str] = {
    "instance_name": "POSTULO_INSTANCE_NAME",
    "tagline": "POSTULO_INSTANCE_TAGLINE",
    "motto": "POSTULO_INSTANCE_MOTTO",
    "default_language": "POSTULO_LANGUAGE_DEFAULT",
    "offered_languages": "POSTULO_LANGUAGES_OFFERED",
    "default_paper": "POSTULO_PAPER_DEFAULT",
    "email_sign_in": "POSTULO_EMAIL_CODE_SIGN_IN",
    "geocoder_consent": "POSTULO_GEOCODER_CONSENT",
    "retention_days": "POSTULO_RETENTION_SECONDS",
    "privacy_notice": "POSTULO_PRIVACY_NOTICE",
    "identifier_schemes": "POSTULO_IDENTIFIER_SCHEMES",
    "backup_schedule": "POSTULO_BACKUP_SCHEDULE",
    "backup_hour": "POSTULO_BACKUP_HOUR",
    "backup_weekday": "POSTULO_BACKUP_WEEKDAY",
    "backup_keep": "POSTULO_BACKUP_KEEP",
    "backup_recipient": "POSTULO_BACKUP_RECIPIENT",
    "backup_remote_keep": "POSTULO_BACKUP_REMOTE_KEEP",
    "backup_destination": "POSTULO_BACKUP_DESTINATION",
    "backup_destination_config": "POSTULO_BACKUP_DESTINATION_CONFIG",
    "email_transport": "POSTULO_EMAIL_TRANSPORT",
    "transport_config": "POSTULO_EMAIL_TRANSPORT_CONFIG",
    "text_transport": "POSTULO_TEXT_TRANSPORT",
    "text_config": "POSTULO_TEXT_TRANSPORT_CONFIG",
    "register_keys": "POSTULO_REGISTER_KEYS",
}

#: A configuration given whole replaces the plugin's secrets as well as its plain settings:
#: the variable holds both, so the encrypted column is read as empty.
SECRET_COLUMNS = {
    "backup_destination_config": "backup_destination_secrets_encrypted",
    "transport_config": "transport_secrets_encrypted",
    "text_config": "text_secrets_encrypted",
    "register_keys": "register_keys_encrypted",
}

#: The columns that hold a value the field does not: what the row keeps for the field it
#: stands in for, and is given back at `save`.
_HELD = "_stored_under_pins"


def is_pinned(field: str) -> bool:
    """Whether the environment holds this field's variable."""
    name = FIELDS.get(field)
    return bool(name) and configuration.is_set(name)


def value_of(field: str):
    """The field's value as the variable gives it, in the shape the column holds."""
    value = configuration.read(FIELDS[field])
    if field == "retention_days":
        return -(-int(value) // 86_400) if value else None
    if field == "offered_languages":
        return list(value or [])
    return value


def apply(row) -> None:
    """Lay the environment over a row that was just read, remembering what it held.

    Idempotent, and for a field the row has loaded: a deferred one is left alone.
    """
    if hasattr(row, _HELD):
        return
    held: dict[str, object] = {}
    for field, name in FIELDS.items():
        if not configuration.is_set(name):
            continue
        if field == "register_keys":
            column = SECRET_COLUMNS[field]
            if column not in row.__dict__:
                continue
            held[column] = row.__dict__[column]
            row.register_keys = value_of(field)
            continue
        if field not in row.__dict__:
            continue
        held[field] = row.__dict__[field]
        setattr(row, field, value_of(field))
        column = SECRET_COLUMNS.get(field)
        if column and column in row.__dict__:
            held[column] = row.__dict__[column]
            setattr(row, column, "")
    object.__setattr__(row, _HELD, held)


def stored(row) -> dict[str, object]:
    """What the row holds in the database for the fields the environment is covering."""
    return dict(getattr(row, _HELD, {}))


def keep(row, values: dict[str, object]) -> None:
    """Take the stored values from a fresh read, after `refresh_from_db` read the row again."""
    object.__setattr__(row, _HELD, dict(values))


def laid_over(row) -> dict[str, object]:
    """The values the environment laid over the row, by column, to put back after a save."""
    return {column: row.__dict__[column] for column in stored(row) if column in row.__dict__}


def restore(row) -> dict[str, object]:
    """Put the stored values back, for a save; returns what was laid over to put back after."""
    over = laid_over(row)
    for column, value in stored(row).items():
        row.__dict__[column] = value
    return over


def reapply(row, over: dict[str, object]) -> None:
    for column, value in over.items():
        row.__dict__[column] = value
