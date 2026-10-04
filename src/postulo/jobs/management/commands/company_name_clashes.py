"""List the companies and departments that the name rule of #546 would refuse, read-only.

Run it before upgrading from a release that allowed *Acme* beside *acme*: the migration that
adds the rule stops at the first clash, so it is kinder to find them all first and merge each
pair with the company's *Merge* page. Changes nothing.
"""

from django.core.management.base import BaseCommand, CommandError

from postulo.jobs import name_clashes
from postulo.jobs.models import Company, Department


class Command(BaseCommand):
    help = "List companies (and departments of one company) whose names differ only in case."

    def handle(self, *args, **options):
        lines = name_clashes.find(Company, Department)
        if not lines:
            self.stdout.write("No clashes: the upgrade will go through.")
            return
        for line in lines:
            self.stdout.write(line)
        raise CommandError(
            f"{len(lines)} clash(es). Merge each pair (company page, Merge) before upgrading."
        )
