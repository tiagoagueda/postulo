"""Search, for agents: the same groups the page shows, as JSON."""

from ninja import Query, Router

from postulo.core import search as searching

from ..auth import scope
from ..schemas import SearchGroupOut

router = Router(tags=["search"], auth=scope("read"))


@router.get("", response=list[SearchGroupOut], summary="Search everything the owner has")
def search_everything(
    request,
    q: str = Query(..., description="At least two characters; matched case-insensitively"),
    limit: int = Query(searching.GROUP_LIMIT, ge=1, le=50, description="Hits per group"),
):
    groups = searching.search(request.auth.owner, q, limit=limit)
    return [
        {
            "kind": group.kind,
            "label": group.label,
            "total": group.total,
            "hits": [
                {
                    "id": hit.id,
                    "title": hit.title,
                    "subtitle": hit.subtitle,
                    "excerpt": hit.excerpt,
                    "web_url": request.build_absolute_uri(hit.url),
                }
                for hit in group.hits
            ],
        }
        for group in groups
    ]
