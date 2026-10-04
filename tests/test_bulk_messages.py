"""The sentence after a bulk action is one plural form per table (#390)."""

import pytest
from django.utils.functional import Promise

from postulo.applications.tables import ApplicationsTable
from postulo.core import bulk
from postulo.core.tables import Table
from postulo.jobs.tables import CompaniesTable, ListingsTable

TABLES = (ApplicationsTable, CompaniesTable, ListingsTable, Table)


@pytest.mark.parametrize("table", TABLES)
def test_each_table_carries_its_sentence_as_a_plural_form_on_count(table):
    message = table.changed_message
    assert isinstance(message, Promise)
    # A lazy ngettext resolves its plural form from the "count" entry of the mapping it is
    # formatted with, so one that formats both ways proves the number is named "count".
    one, many = str(message % {"count": 1}), str(message % {"count": 2})
    assert one.startswith("1 ")
    assert many.startswith("2 ")
    assert one != many
    assert not hasattr(table, "noun")


@pytest.mark.parametrize(
    ("table", "one", "many"),
    [
        (ApplicationsTable, "1 application changed.", "5 applications changed."),
        (CompaniesTable, "1 company changed.", "5 companies changed."),
        (ListingsTable, "1 listing changed.", "5 listings changed."),
    ],
)
def test_english_reads_as_it_did(table, one, many):
    assert bulk.changed(1, table) == one
    assert bulk.changed(5, table) == many
    assert bulk.changed(0, table) == many.replace("5", "0")
