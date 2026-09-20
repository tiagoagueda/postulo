"""The one function the queue knows about (#247).

`django_tasks_db` stores a task as a dotted path and a list of arguments, so everything
enqueued has to be importable from a worker that shares no memory with the web process.
One entry point rather than one per kind: the kind is a column on the errand, the handler
is looked up from the registry inside the worker, and adding a sixth kind of slow work
means registering a handler rather than teaching the queue about it.

An integer is the whole argument, for the same reason: the row is the only thing the two
processes share, and a pickled model instance would be a copy of a row that has since
moved on.
"""

from __future__ import annotations

from django.tasks import task


@task()
def perform_errand(errand_id: int) -> None:
    from . import errands

    errands.perform(errand_id)
