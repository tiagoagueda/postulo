"""Insights: what the record says, as numbers an agent can read."""

import dataclasses
from decimal import Decimal

from ninja import Router

from postulo.applications.analytics import build

from ..auth import scope

router = Router(tags=["insights"], auth=scope("read"))


def _plain(value):
    """Dataclasses to dicts, lazy strings to strings, decimals to floats: JSON, in short."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)


@router.get("", response=dict, summary="The figures the Insights page shows")
def get_insights(request):
    insights = build(request.auth.owner)
    data = _plain(insights)
    data["response_rate"] = insights.response_rate
    data["selectivity"] = insights.selectivity
    data["sample_is_small"] = insights.sample_is_small
    return data
