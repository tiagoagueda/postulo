"""gunicorn's access log, with the one-time secrets in addresses taken out (#754).

``--logger-class postulo.config.gunicorn_logging.RedactingLogger`` in the image's command.
gunicorn is installed in the image and not in a development environment, so the redaction
itself lives in `core.logredact`, where the tests reach it.
"""

from __future__ import annotations

try:
    from gunicorn.glogging import Logger
except ImportError:  # gunicorn is in the image, not in a development environment
    Logger = object  # type: ignore[assignment,misc]

from postulo.core.logredact import redact

#: The access-log atoms that can hold an address: the request line, the path, the query string
#: and the referer.
_ATOMS = ("r", "U", "q", "f", "{referer}i")


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for name in _ATOMS:
            if isinstance(atoms.get(name), str):
                atoms[name] = redact(atoms[name])
        return atoms
