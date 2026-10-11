"""What a deletion would take with it, asked of the view that does the deleting (#854).

A delete used to be a page of its own: *Are you sure?*, and under it the counted list of what
goes too (#217), what is kept, and sometimes a choice (#666). Putting the same question in a
dialog on the object's own page needs the same facts, and the delete views already work them
out in ``get_context_data``. This asks them rather than working them out a second time, so
the dialog and the page cannot disagree about what a deletion costs.
"""

from __future__ import annotations

#: What a confirmation shows beyond the name: the counted consequences, what is kept, and a
#: choice the person may make.
KEYS = ("consequences", "kept", "choice")


def facts_for(view_class, request, obj) -> dict:
    """The consequences, the kept things and the choice of deleting ``obj`` with ``view_class``.

    The view is set up on ``request`` and given ``obj``, as ``DeleteView.get`` would, and its
    context read; nothing is deleted and nothing is written.
    """
    view = view_class()
    view.setup(request, pk=obj.pk)
    view.object = obj
    context = view.get_context_data()
    return {key: context[key] for key in KEYS if context.get(key)}
