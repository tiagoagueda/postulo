"""Small template helpers used across the interface."""

from django import template
from django.forms import BoundField

register = template.Library()


@register.filter
def add_class(field: BoundField, css_classes: str) -> BoundField:
    """Append CSS classes to a form widget.

    Django offers no way to add a class to a widget from a template, and forking
    every form definition to set ``attrs`` is worse than a four-line filter.
    """
    existing = field.field.widget.attrs.get("class", "")
    merged = f"{existing} {css_classes}".strip()
    return field.as_widget(attrs={"class": merged})


@register.simple_tag(takes_context=True)
def nav_active(context, *url_names: str, css_class: str = "nav-link-active") -> str:
    """Return ``css_class`` when the current view matches one of ``url_names``."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "nav-link"
    current = f"{match.app_name}:{match.url_name}" if match.app_name else match.url_name
    return css_class if current in url_names else "nav-link"
