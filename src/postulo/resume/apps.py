from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ResumeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.resume"
    label = "resume"
    verbose_name = _("Resume")
