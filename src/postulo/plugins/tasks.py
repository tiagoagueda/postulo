"""The plugin update check, as a task a worker runs (#1187).

Kept beside the code it calls, since the queue stores a task by its dotted path and this one
is not core's: the scheduler enqueues it where a worker is expected, so that its pass never
waits on a catalogue's server.
"""

from __future__ import annotations

from django.tasks import task


@task()
def plugin_update_check() -> None:
    """Fetch each catalogue's signed index, keep what it says, and tell the administrators."""
    from . import updates

    updates.check()
