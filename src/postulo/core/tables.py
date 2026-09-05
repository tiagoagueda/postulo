"""Tables a person can sort, narrow and arrange.

One implementation serves every list that is really a table — applications, companies,
and whatever comes next. A table declares its columns: what each is called, how it sorts,
how it narrows, and whether it shows by default. From that the view gets a validated
ordering and filter to apply, the template gets headers with sort links and filter
inputs, and the person gets a *Columns* control whose choices follow the account.

Two kinds of state, kept apart on purpose. **Sort and filters live in the URL**: they are
a question, and a question should be shareable, bookmarkable and safe with the back
button. **Which columns show, in what order, and how many rows a page holds live on the
profile**: they are a preference, and a preference should follow the person to every
device rather than clutter every link.

Nothing here trusts the query string. A sort key or filter that is not declared is
ignored, and every filter only ever narrows the owner-scoped queryset it is given.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import cached_property

from django.db.models import F, Q
from django.http import QueryDict
from django.utils.translation import gettext_lazy as _

PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True)
class Column:
    """One column: a label, and what the table may do with it."""

    key: str
    label: str
    #: ORM expressions to order by ascending. Empty means the column cannot be sorted.
    sort: tuple[str, ...] = ()
    #: Whether the first click sorts descending — right for dates, where newest first is
    #: what people mean.
    newest_first: bool = False
    #: ``text``, ``choice``, ``date`` or empty for a column that does not narrow.
    filter: str = ""
    #: Lookups the filter applies. Text matches any of them; choice and date use the first.
    lookups: tuple[str, ...] = ()
    #: The choices a ``choice`` filter offers, as (value, label) pairs.
    choices: tuple = ()
    #: The query parameter, when it should differ from the key.
    param: str = ""
    #: Shown before the person has chosen anything.
    default: bool = False
    #: Numbers sit on the right.
    numeric: bool = False
    #: Extra classes for the header and cells (a minimum width, say).
    css: str = ""

    @property
    def name(self) -> str:
        return self.param or self.key

    @property
    def sortable(self) -> bool:
        return bool(self.sort)


@dataclass
class Header:
    """A visible column as the template sees it: its sort state and its filter's value."""

    column: Column
    state: str = ""  # "asc", "desc" or ""
    next_sort: str = ""
    value: str = ""
    value_from: str = ""
    value_to: str = ""

    @property
    def key(self) -> str:
        return self.column.key

    @property
    def label(self) -> str:
        return str(self.column.label)

    @property
    def input_id(self) -> str:
        return f"filter-{self.column.key}"

    @property
    def filter_label(self) -> str:
        return str(_("Filter by %(column)s") % {"column": str(self.column.label).lower()})


@dataclass
class ChooserRow:
    column: Column
    shown: bool
    first: bool = False
    last: bool = False


class Table:
    """A configurable table. Subclass, declare ``name`` and ``columns``, register."""

    #: The key the person's choices are stored under, and the settings view's address.
    name: str = ""
    columns: tuple[Column, ...] = ()
    #: The sort applied when the request names none, with ``-`` for descending.
    default_sort: str = ""
    #: Query parameters outside the columns that also narrow the list (a search box, a
    #: status select). They count as filters for the empty state and the *Clear* link.
    extra_params: tuple[str, ...] = ()
    #: What a row is called, for the live count: ("application", "applications").
    noun: tuple[str, str] = ("row", "rows")

    def __init__(self, request, settings: dict | None = None):
        self.request = request
        self.params: QueryDict = request.GET
        self.settings = settings if isinstance(settings, dict) else {}
        self.by_key = {column.key: column for column in self.columns}

    # ------------------------------------------------------------- preferences

    @classmethod
    def default_columns(cls) -> list[str]:
        return [column.key for column in cls.columns if column.default]

    @cached_property
    def visible(self) -> list[Column]:
        """The columns to show, in the person's order; the defaults when they chose none."""
        keys = self.settings.get("columns")
        if not isinstance(keys, list):
            keys = self.default_columns()
        chosen = [self.by_key[key] for key in keys if key in self.by_key]
        return chosen or [self.by_key[key] for key in self.default_columns()]

    @property
    def visible_keys(self) -> set[str]:
        return {column.key for column in self.visible}

    @property
    def page_size(self) -> int:
        size = self.settings.get("page_size")
        return size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE

    @property
    def is_customised(self) -> bool:
        return bool(self.settings)

    @property
    def chooser(self) -> list[ChooserRow]:
        """Every column, shown ones first in their order, for the *Columns* control."""
        shown = list(self.visible)
        hidden = [column for column in self.columns if column not in shown]
        rows = [ChooserRow(column, True) for column in shown]
        rows += [ChooserRow(column, False) for column in hidden]
        if rows:
            rows[0].first = True
            rows[-1].last = True
        return rows

    # -------------------------------------------------------------------- sort

    @cached_property
    def sort(self) -> str:
        """The sort in force: the request's if it names a sortable column, else the default."""
        wanted = self.params.get("sort", "").strip()
        key = wanted.removeprefix("-")
        column = self.by_key.get(key)
        if column is None or not column.sortable:
            return self.default_sort
        return wanted

    def ordering(self) -> list:
        """Order-by expressions for the sort in force, with a stable tiebreak."""
        sort = self.sort
        descending = sort.startswith("-")
        column = self.by_key.get(sort.removeprefix("-"))
        expressions = []
        for expression in column.sort if column else ():
            flip = expression.startswith("-")
            name = expression.removeprefix("-")
            down = descending != flip
            expressions.append(
                F(name).desc(nulls_last=True) if down else F(name).asc(nulls_last=True)
            )
        expressions.append(F("pk").desc() if descending else F("pk").asc())
        return expressions

    def sort_state(self, column: Column) -> str:
        if self.sort.removeprefix("-") != column.key:
            return ""
        return "desc" if self.sort.startswith("-") else "asc"

    def next_sort(self, column: Column) -> str:
        """What clicking this header should sort by: the other direction if it is active."""
        state = self.sort_state(column)
        if state == "asc":
            return f"-{column.key}"
        if state == "desc":
            return column.key
        return f"-{column.key}" if column.newest_first else column.key

    # ----------------------------------------------------------------- filters

    def filter(self, queryset):
        """Narrow by every declared column filter present in the request."""
        for column in self.columns:
            if column.filter == "text":
                value = self.params.get(column.name, "").strip()
                if value:
                    condition = Q()
                    for lookup in column.lookups:
                        condition |= Q(**{f"{lookup}__icontains": value})
                    queryset = queryset.filter(condition)
            elif column.filter == "choice":
                value = self.params.get(column.name, "").strip()
                if value and value in {str(choice) for choice, _label in column.choices}:
                    queryset = queryset.filter(**{column.lookups[0]: value})
            elif column.filter == "date":
                start = _date(self.params.get(f"{column.name}_from", ""))
                end = _date(self.params.get(f"{column.name}_to", ""))
                if start:
                    queryset = queryset.filter(**{f"{column.lookups[0]}__gte": start})
                if end:
                    queryset = queryset.filter(**{f"{column.lookups[0]}__lte": end})
        return queryset

    def apply(self, queryset):
        return self.filter(queryset).order_by(*self.ordering())

    @property
    def filters_active(self) -> bool:
        """Whether anything in the request narrows the list."""
        names = list(self.extra_params)
        for column in self.columns:
            if column.filter == "date":
                names += [f"{column.name}_from", f"{column.name}_to"]
            elif column.filter:
                names.append(column.name)
        return any(self.params.get(name, "").strip() for name in names)

    @property
    def clear_url(self) -> str:
        """The list with every filter removed and the sort kept."""
        path = self.request.path
        if self.sort and self.sort != self.default_sort:
            return f"{path}?sort={self.sort}"
        return path

    # ---------------------------------------------------------------- template

    @cached_property
    def headers(self) -> list[Header]:
        headers = []
        for column in self.visible:
            header = Header(
                column=column,
                state=self.sort_state(column),
                next_sort=self.next_sort(column),
            )
            if column.filter in ("text", "choice"):
                header.value = self.params.get(column.name, "")
            elif column.filter == "date":
                header.value_from = self.params.get(f"{column.name}_from", "")
                header.value_to = self.params.get(f"{column.name}_to", "")
            headers.append(header)
        return headers

    @property
    def has_filter_row(self) -> bool:
        return any(header.column.filter for header in self.headers)

    # ---------------------------------------------------------------- settings

    @classmethod
    def clean_settings(cls, data: QueryDict, current: dict | None = None) -> dict:
        """Turn the *Columns* form into a stored preference.

        The form posts every column in its current order (``order``), which ones are
        ticked (``show``), at most one move (``move`` as ``up:key`` or ``down:key``) and a
        page size. Anything not declared is dropped.
        """
        order = []
        for key in data.getlist("order"):
            if key in {column.key for column in cls.columns} and key not in order:
                order.append(key)
        for column in cls.columns:
            if column.key not in order:
                order.append(column.key)

        move = data.get("move", "")
        if ":" in move:
            direction, key = move.split(":", 1)
            if key in order:
                index = order.index(key)
                if direction == "up" and index > 0:
                    order[index - 1], order[index] = order[index], order[index - 1]
                elif direction == "down" and index < len(order) - 1:
                    order[index + 1], order[index] = order[index], order[index + 1]

        shown = set(data.getlist("show"))
        columns = [key for key in order if key in shown]
        if not columns:
            columns = cls.default_columns()

        try:
            page_size = int(data.get("page_size", ""))
        except ValueError:
            page_size = (current or {}).get("page_size", DEFAULT_PAGE_SIZE)
        if page_size not in PAGE_SIZES:
            page_size = DEFAULT_PAGE_SIZE

        return {"columns": columns, "page_size": page_size}


def _date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


# ------------------------------------------------------------------- registry

TABLES: dict[str, type[Table]] = {}


def register(table: type[Table]) -> type[Table]:
    """Make a table known to the settings view. Used as a class decorator."""
    if not table.name:
        raise ValueError(f"{table.__name__} needs a name to be registered.")
    TABLES[table.name] = table
    return table


def settings_for(user, name: str) -> dict:
    """The person's stored choices for one table, or nothing."""
    profile = getattr(user, "profile", None)
    stored = getattr(profile, "table_settings", None) or {}
    value = stored.get(name)
    return value if isinstance(value, dict) else {}


def save_settings(user, name: str, value: dict | None) -> None:
    """Store, or with ``None`` forget, the person's choices for one table."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return
    stored = dict(profile.table_settings or {})
    if value is None:
        stored.pop(name, None)
    else:
        stored[name] = value
    profile.table_settings = stored
    profile.save(update_fields=["table_settings", "updated_at"])
