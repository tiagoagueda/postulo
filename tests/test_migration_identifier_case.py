"""Jobs 0014 folds identifiers that collide in case instead of refusing to run (#570).

It saved each folded value while the old case-sensitive constraint was still in place, so
the first `q95` beside a `Q95` raised IntegrityError and the instance could not migrate; and
two *Other* identifiers differing only in case made the new constraint fail to be created.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("jobs", "0013_company_kind")]
AFTER = [("jobs", "0014_remove_companyidentifier_one_company_per_identifier_per_owner_and_more")]


@pytest.mark.django_db(transaction=True)
def test_colliding_identifiers_are_folded_and_the_migration_completes():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps

    user = old.get_model("accounts", "User").objects.create(username="folder")
    company = old.get_model("jobs", "Company")
    identifier = old.get_model("jobs", "CompanyIdentifier")
    first = company.objects.create(owner=user, name="First")
    second = company.objects.create(owner=user, name="Second")
    identifier.objects.create(owner=user, company=first, scheme="wikidata", value="Q95")
    identifier.objects.create(owner=user, company=second, scheme="wikidata", value="q95")
    identifier.objects.create(owner=user, company=first, scheme="other", label="VAT", value="pt123")
    identifier.objects.create(owner=user, company=first, scheme="other", label="vat", value="PT123")

    executor = MigrationExecutor(connection)
    executor.migrate(AFTER)
    new = executor.loader.project_state(AFTER).apps
    rows = new.get_model("jobs", "CompanyIdentifier").objects

    assert list(rows.filter(scheme="wikidata").values_list("value", "company__name")) == [
        ("Q95", "First")
    ]
    assert rows.filter(scheme="other").count() == 1
    assert new.get_model("jobs", "Company").objects.count() == 2

    # Leave the database at the latest migration for whatever runs next.
    MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
