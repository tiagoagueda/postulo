"""The search page: one box, results grouped by kind."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import redirect, render

from . import search as searching


@login_required
def search_page(request: HttpRequest):
    # This page reads `q` and nothing else, and its address says so. With scripts off, the
    # masthead's *Search everything* on a table page is a second submit button on the form
    # that narrows the table (#313), and that form carries the table's filters and its sort
    # so that Enter keeps them -- so they arrive here too, where they mean nothing:
    # `/search/?q=unit&location=ohio&sort=-name`. The answer was right and the address was
    # not, and an address is what gets bookmarked and sent to somebody. A redirect rather
    # than a script taking the fields out, because without a script is exactly when it
    # happens; to the words alone, or to the bare page where there were none.
    query = searching.clean_query(request.GET.get("q", ""))
    if any(name != "q" for name in request.GET):
        return redirect(f"{request.path}?{urlencode({'q': query})}" if query else request.path)
    groups = searching.search(request.user, query)
    return render(
        request,
        "core/search.html",
        {
            "query": query,
            "groups": groups,
            "too_short": 0 < len(query) < searching.MIN_QUERY_LENGTH,
            "total": sum(group.total for group in groups),
        },
    )
