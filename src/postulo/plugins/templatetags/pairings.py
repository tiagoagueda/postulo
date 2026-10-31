"""The pairing line on a record's page (#327): `{% pairings_of record as pairing %}`."""

from __future__ import annotations

from django import template

from postulo.plugins import pairing

register = template.Library()


@register.simple_tag(takes_context=True)
def pairings_of(context, record) -> dict:
    """What the line under a record says: its pairings, and where to pair it with an item.

    From `SyncLink` and the person's own connections alone -- a record's page never waits on
    a server. Nothing for a record that is not the viewer's own: a shared company's
    pairings are its owner's business, and pairing is the owner's to do.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if record is None or user is None or not pairing.is_own(user, record):
        return {}
    kind = pairing.kind_of(record)
    if not kind:
        return {}
    connections = pairing.pairable_connections(user)
    links = pairing.lines_for(user, record)
    if not connections and not links:
        return {}
    pairable = {connection.pk for connection in connections}
    return {
        # Unpaired from here only where the page exists to do it from.
        "links": [(link, link.connection_id in pairable) for link in links],
        "connections": connections,
        "for": f"{kind}-{record.pk}",
        "here": request.get_full_path(),
        "name": pairing.name_of(record),
    }
