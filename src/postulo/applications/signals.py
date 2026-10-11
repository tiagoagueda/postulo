"""What the applications app does when something else announces itself (#887)."""

from django.dispatch import receiver

from postulo.plugins.signals import plugin_removed

from .services import retire_reminders_of


@receiver(plugin_removed)
def retire_what_a_removed_plugin_placed(sender, name: str, **kwargs) -> None:
    """The calendar entries a plugin placed are ended, not deleted: kept, neither drawn nor
    announced, and brought back if the plugin places them again."""
    retire_reminders_of(name)
