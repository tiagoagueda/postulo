"""The schema.org values Postulo has its own word for.

Here rather than beside the sources because a board recipe needs them too: LinkedIn writes
the employment type as a word in a list rather than as a field, and a recipe that tried to
state "Full-time" directly would hand Postulo a value it refuses.
"""

from __future__ import annotations

#: schema.org employmentType values mapped onto Postulo's own.
EMPLOYMENT_TYPES = {
    "FULL_TIME": "full_time",
    "PART_TIME": "part_time",
    "CONTRACTOR": "contract",
    "CONTRACT": "contract",
    "TEMPORARY": "contract",
    "INTERN": "internship",
    "INTERNSHIP": "internship",
    "APPRENTICESHIP": "apprenticeship",
}

#: schema.org QuantitativeValue unitText values Postulo has a period for.
SALARY_PERIODS = {"YEAR": "year", "MONTH": "month", "DAY": "day", "HOUR": "hour"}


def employment_type(raw: str) -> str:
    """Postulo's word for an employment type, or "" for anything it does not know.

    Takes the spellings boards actually use -- "FULL_TIME", "Full-time", "full time" -- and
    nothing else. A board writing it in the reader's language ("Temps plein") gets an empty
    field and a person filling it in, which is the right answer: the alternative is a table
    of every employment word in every language Postulo speaks, maintained against boards
    that change theirs without telling anybody.
    """
    return EMPLOYMENT_TYPES.get(
        str(raw or "").strip().upper().replace("-", "_").replace(" ", "_"), ""
    )
