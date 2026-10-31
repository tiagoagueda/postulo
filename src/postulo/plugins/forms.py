"""The connection form, drawn from what a plugin says it needs."""

from __future__ import annotations

import logging

from django import forms
from django.utils.translation import gettext_lazy as _

from . import discovery
from .base import CONNECTION_ONLY_TYPES, FieldSpec, call_with_user
from .models import Connection, PluginRepository

logger = logging.getLogger(__name__)


class PluginRepositoryForm(forms.ModelForm):
    """A catalogue an administrator adds or edits.

    The key is the whole of the trust. `catalogue.py` is blunt about it -- *"Without the key
    there is no catalogue: an unsigned list of URLs to run code from is not something
    Postulo will offer"* -- so a row without one is saved, and simply never offered.

    Changing a key is not an edit like changing a label. It replaces the only thing standing
    between an index and arbitrary code running inside Postulo, and a form beside a URL box
    makes it look like a preference. So a change is called out, and written to the log with
    who did it.
    """

    class Meta:
        model = PluginRepository
        fields = ("name", "url", "public_key", "enabled")
        labels = {"url": _("Index address")}
        help_texts = {
            "name": _(
                "Recorded against everything installed from here, so it cannot change "
                "later without orphaning what it installed."
            ),
            "url": _("Where the signed index is published. Its signature sits beside it."),
        }

    def __init__(self, *args, pinned: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.pinned = pinned
        if self.instance.pk:
            # The name is what an installed plugin's `origin` remembers.
            self.fields["name"].disabled = True
        if pinned:
            for field in self.fields.values():
                field.disabled = True

    def clean(self):
        cleaned = super().clean()
        if self.pinned:
            raise forms.ValidationError(
                _("The environment sets “%(name)s”, so it cannot be changed here.")
                % {"name": self.instance.name}
            )
        return cleaned

    def clean_public_key(self) -> str:
        key = (self.cleaned_data.get("public_key") or "").strip()
        if not key:
            return key
        from .catalogue import decode_public_key

        try:
            decode_public_key(key)
        except Exception as error:
            # `decode_public_key` already says this in words. Repeating it here would be
            # the same sentence translated twice into twenty-three languages.
            raise forms.ValidationError(str(error)) from error
        return key

    @property
    def key_changed(self) -> bool:
        return "public_key" in self.changed_data and bool(self.initial.get("public_key"))


#: Shown in place of a stored secret. Submitting it back means "leave it as it is".
SECRET_PLACEHOLDER = "••••••••"  # noqa: S105 - a display placeholder, not a credential


def kind_specs(plugin) -> list[FieldSpec]:
    """Fields every connection of a kind carries, whatever the plugin: a notifier's events.

    Handed the plugin rather than its kind, because a notifier's switches default as the
    plugin says (#240): the same seven events, on for a person and off for a machine or the
    other way round.
    """
    kind = getattr(plugin, "kind", plugin)
    if kind == "notifier":
        from postulo.notifications.base import event_specs

        return event_specs(plugin if not isinstance(plugin, str) else None)
    if kind == "store":
        from postulo.documents.stores import kind_specs as document_kind_specs

        return document_kind_specs()
    if kind == "sync":
        from .syncing import kind_specs as sync_kind_specs

        return sync_kind_specs()
    return []


def form_field_for(spec: FieldSpec, *, has_value: bool = False) -> forms.Field:
    common = {"label": spec.label, "help_text": spec.help, "required": spec.required}
    if spec.type in CONNECTION_ONLY_TYPES:
        # Drawn here only by a server page -- a transport's or a backup destination's -- since
        # a connection draws its found options as a table of its own (#332). There is no
        # service of the person's to look on, so the field is a sentence and keeps nothing.
        return forms.CharField(
            label=spec.label,
            required=False,
            disabled=True,
            help_text=_(
                "Not offered here: this is chosen from what a person's connection finds on "
                "its service, and a server setting has no such service to look on."
            ),
        )
    if spec.type == "password" or spec.secret:
        # A stored secret is never shown back. The field is optional while one exists,
        # because leaving it blank means keeping it. A secret may be several lines —
        # Apprise takes a list of URLs with the credentials inside — in which case it is
        # a text area that is just as blank on the way back.
        attrs = {
            "placeholder": SECRET_PLACEHOLDER if has_value else "",
            "autocomplete": "off",
        }
        if spec.type == "textarea":
            widget = forms.Textarea(attrs={"rows": 4, **attrs})
        else:
            widget = forms.PasswordInput(render_value=False, attrs=attrs)
        hint = str(_("Stored; leave blank to keep the current value.")) if has_value else ""
        return forms.CharField(
            widget=widget,
            label=spec.label,
            help_text=" ".join(str(part) for part in (spec.help, hint) if part),
            required=spec.required and not has_value,
            max_length=20_000 if spec.type == "textarea" else 2000,
        )
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
    return forms.CharField(max_length=500, **common)


class OptionTable:
    """A ``discovered`` field as the form draws it: one select per option found (#332).

    Not a Django field, because what it holds is not one value: each row is a field of its
    own, ``plugin_<name>__<key>``, so that each select has a label naming its option and the
    page works without a script. `ConnectionForm` puts the rows back together on save.
    """

    #: What the template asks to tell this from a bound field.
    is_option_table = True

    def __init__(self, form: ConnectionForm, spec: FieldSpec, rows: list[discovery.Row]):
        self.spec = spec
        self.name = spec.name
        self.label = spec.label
        self.help = spec.help
        self.rows = [(row, form[f"plugin_{spec.name}__{row.key}"]) for row in rows]
        config = form.instance.config if form.instance.pk else {}
        self.found_at = discovery.found_at(config)
        #: Not saved yet, so there is nothing to look on the server with.
        self.unsaved = not form.instance.pk


class ConnectionForm(forms.ModelForm):
    """Label and switch from the model, everything else from the plugin's field specs."""

    class Meta:
        model = Connection
        fields = ("label", "enabled")

    def __init__(self, plugin, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plugin = plugin
        owner = getattr(self.instance, "owner", None)
        self.specs: list[FieldSpec] = list(
            call_with_user(plugin.config_fields, user=owner)
        ) + kind_specs(plugin)
        existing_config = dict(self.instance.config) if self.instance.pk else {}
        existing_secrets = self.instance.secrets if self.instance.pk else {}
        #: Each ``discovered`` field's rows, by the field's name (#332).
        self.option_rows: dict[str, list[discovery.Row]] = {}
        for spec in self.specs:
            if spec.type == "discovered":
                self._add_option_rows(spec, existing_config)
                continue
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

    def _add_option_rows(self, spec: FieldSpec, config: dict) -> None:
        """One select per option found, *Not used* first, then the modes the option allows."""
        rows = discovery.rows(spec, config)
        self.option_rows[spec.name] = rows
        for row in rows:
            choices = [("", _("Not used")), *row.modes]
            field = forms.ChoiceField(
                choices=choices, label=row.label, required=False, initial=row.mode
            )
            field.widget.attrs["class"] = "select"
            self.fields[f"plugin_{spec.name}__{row.key}"] = field

    def plugin_fields(self):
        """What the plugin asked for, in the order it asked: bound fields and option tables."""
        return [
            OptionTable(self, spec, self.option_rows[spec.name])
            if spec.type == "discovered"
            else self[f"plugin_{spec.name}"]
            for spec in self.specs
        ]

    @property
    def can_look(self) -> bool:
        """Whether the form offers *Look on the server*: a plugin with ``discover``, saved."""
        return bool(self.instance.pk) and discovery.can_look(self.plugin, self.specs)

    def _merged(self) -> tuple[dict, dict]:
        """Configuration and secrets as they will be stored: what was typed over what is kept."""
        connection = self.instance
        config = dict(connection.config) if connection.pk else {}
        secrets = connection.secrets if connection.pk else {}
        for spec in self.specs:
            if spec.type == "discovered":
                # The chosen options and their modes; a row on *Not used* is not kept (#332).
                config[spec.name] = {
                    row.value: mode
                    for row in self.option_rows[spec.name]
                    if (mode := self.cleaned_data.get(f"plugin_{spec.name}__{row.key}"))
                }
                continue
            value = self.cleaned_data.get(f"plugin_{spec.name}")
            if spec.secret:
                if value:
                    secrets[spec.name] = value
                # blank: keep what is stored
            else:
                config[spec.name] = value if value is not None else ""
        return discovery.keep_only_chosen_gone(config, self.specs), secrets

    def clean(self) -> dict:
        """Let the plugin look at the whole configuration once every field is in order.

        A plugin that can tell a malformed address from a good one says so here, on the
        form, against the field concerned. It sees configuration and secrets together,
        the stored secret standing in for a blank field, so it checks what will run.
        """
        cleaned = super().clean()
        validate = getattr(self.plugin, "validate", None)
        if validate is None or self.errors:
            return cleaned
        config, secrets = self._merged()
        try:
            problems = validate({**config, **secrets}) or {}
        except Exception as error:
            logger.exception("Plugin %r failed to validate a connection", self.plugin.name)
            raise forms.ValidationError(
                _("%(plugin)s could not check these settings: %(error)s")
                % {"plugin": self.plugin.label, "error": f"{type(error).__name__}: {error}"}
            ) from error
        for name, messages in problems.items():
            field = f"plugin_{name}" if name and f"plugin_{name}" in self.fields else None
            for message in [messages] if isinstance(messages, str) else messages:
                self.add_error(field, message)
        return cleaned

    def save(self, commit: bool = True) -> Connection:
        connection = super().save(commit=False)
        connection.kind = self.plugin.kind
        connection.plugin = self.plugin.name
        connection.config, connection.secrets = self._merged()
        if commit:
            connection.save()
        return connection
