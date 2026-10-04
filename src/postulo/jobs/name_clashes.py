"""Companies, and departments of one company, whose names are one name (#546).

The unique constraint on a company's name reads `name_key` -- the name in one case, its
spacing collapsed -- so two rows that differ only in capitals can no longer exist. Rows
recorded before the key may already be two. This finds them, for the migration that adds the
constraint and for `manage.py company_name_clashes`, which an operator can run first.

Takes the models as arguments because the migration passes its historical ones, and reads
the name and never `name_key`, so it also runs before that column exists.
"""

from __future__ import annotations

from collections import defaultdict

from postulo.core.slugs import name_key


def find(company_model, department_model) -> list[str]:
    """One line per clash: whose, and which rows (id and name) collide."""
    lines: list[str] = []
    by_key = defaultdict(list)
    for pk, owner_id, name in company_model.objects.values_list("pk", "owner_id", "name"):
        by_key[(owner_id, name_key(name))].append((pk, name))
    for (owner_id, _key), rows in sorted(by_key.items()):
        if len(rows) > 1:
            lines.append(f"owner {owner_id}, companies {_listed(rows)}")
    by_key = defaultdict(list)
    values = department_model.objects.values_list("pk", "owner_id", "company_id", "name")
    for pk, owner_id, company_id, name in values:
        by_key[(owner_id, company_id, name_key(name))].append((pk, name))
    for (owner_id, company_id, _key), rows in sorted(by_key.items()):
        if len(rows) > 1:
            lines.append(f"owner {owner_id}, departments of company {company_id} {_listed(rows)}")
    return lines


def _listed(rows) -> str:
    return ", ".join(f'#{pk} "{name}"' for pk, name in sorted(rows))
