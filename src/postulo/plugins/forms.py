"""The connection form, drawn from what a plugin says it needs."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .base import FieldSpec
from .models import Connection

#: Shown in place of a stored secret. Submitting it back means "leave it as it is".
SECRET_PLACEHOLDER = "••••••••"  # noqa: S105 - a display placeholder, not a credential


def kind_specs(kind: str) -> list[FieldSpec]:
    """Fields every connection of a kind carries, whatever the plugin: a notifier's events."""
    if kind == "notifier":
        from postulo.notifications.base import event_specs

        return event_specs()
    return []


def form_field_for(spec: FieldSpec, *, has_value: bool = False) -> forms.Field:
    common = {"label": spec.label, "help_text": spec.help, "required": spec.required}
    if spec.type == "boolean":
        return forms.BooleanField(label=spec.label, help_text=spec.help, required=False)
    if spec.type == "integer":
        return forms.IntegerField(**common)
    if spec.type == "choice":
        return forms.ChoiceField(choices=list(spec.choices), **common)
    if spec.type == "url":
        return forms.URLField(max_length=500, **common)
    if spec.type == "email":
        return forms.EmailField(**common)
    if spec.type == "textarea":
        return forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), **common)
    if spec.type == "password" or spec.secret:
        # A stored secret is never shown back. The field is optional while one exists,
        # because leaving it blank means keeping it.
        return forms.CharField(
            widget=forms.PasswordInput(
                render_value=False,
                attrs={
                    "placeholder": SECRET_PLACEHOLDER if has_value else "",
                    "autocomplete": "off",
                },
            ),
            label=spec.label,
            help_text=spec.help
            or (str(_("Stored; leave blank to keep the current value.")) if has_value else ""),
            required=spec.required and not has_value,
            max_length=2000,
        )
    return forms.CharField(max_length=500, **common)


class ConnectionForm(forms.ModelForm):
    """Label and switch from the model, everything else from the plugin's field specs."""

    class Meta:
        model = Connection
        fields = ("label", "enabled")

    def __init__(self, plugin, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plugin = plugin
        self.specs: list[FieldSpec] = list(plugin.config_fields()) + kind_specs(plugin.kind)
        existing_config = dict(self.instance.config) if self.instance.pk else {}
        existing_secrets = self.instance.secrets if self.instance.pk else {}
        for spec in self.specs:
            has_value = spec.name in existing_secrets if spec.secret else False
            field = form_field_for(spec, has_value=has_value)
            if not spec.secret:
                field.initial = existing_config.get(spec.name, spec.default)
            elif spec.default is not None and not has_value:
                field.initial = spec.default
            self.fields[f"plugin_{spec.name}"] = field
        if not self.instance.pk and not self.initial.get("label"):
            # The instance's blank label sits in self.initial and would win over the
            # field's own initial; the plugin's label is the sensible starting point.
            self.initial["label"] = plugin.label

    def plugin_fields(self):
        """The bound fields the plugin asked for, in the order it asked."""
        return [self[f"plugin_{spec.name}"] for spec in self.specs]

    def save(self, commit: bool = True) -> Connection:
        connection = super().save(commit=False)
        connection.kind = self.plugin.kind
        connection.plugin = self.plugin.name
        config = dict(connection.config) if connection.pk else {}
        secrets = connection.secrets if connection.pk else {}
        for spec in self.specs:
            value = self.cleaned_data.get(f"plugin_{spec.name}")
            if spec.secret:
                if value:
                    secrets[spec.name] = value
                # blank: keep what is stored
            else:
                config[spec.name] = value if value is not None else ""
        connection.config = config
        connection.secrets = secrets
        if commit:
            connection.save()
        return connection
