"""The search page: one box, results grouped by kind."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render

from . import search as searching


@login_required
def search_page(request: HttpRequest):
    query = searching.clean_query(request.GET.get("q", ""))
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
