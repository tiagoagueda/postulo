from django import forms

from postulo.jobs.forms import OwnerScopedModelForm

from .models import CaptureToken


class CaptureTokenForm(OwnerScopedModelForm):
    class Meta:
        model = CaptureToken
        fields = ("name",)
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Firefox on the laptop"})}
