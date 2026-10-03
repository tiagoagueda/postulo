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

**A saved view is a name for a query string** (#259). "Everything I have not heard back on
in three weeks" is a question a search asks every week, and every week the same boxes were
filled in from memory. The filtered, sorted view *is* its address already, so keeping one is
storing that address under a name -- beside the column widths, on the profile, where it
leaves with the person's data and comes back with it. A view carries the columns it was
saved with too, so choosing one restores filters, sort and columns together; and it
degrades rather than fails when the table it was saved against has changed, which is the
whole of the risk and where the tests are.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import cached_property

from django.db.models import F, Q
from django.http import QueryDict
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

#: What a column may be dragged to. Narrower than the lower bound is a column nobody can
#: read and nobody can grab again; wider than the upper one is a table that scrolls sideways
#: for one column's sake (#136).
MIN_WIDTH = 64
MAX_WIDTH = 900

PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50

#: The query parameter that names a saved view (#259). Not `view`: the applications page
#: already spends that on its shape, table or board.
SAVED = "saved"
#: The value that means *the plain table, whatever the default view is* -- the way out of a
#: default, which *Clear* has to offer or a default view would be a table nobody can leave.
PLAIN = "none"
#: How many views one table keeps. Enough for a week's questions; a menu of forty is a menu
#: nobody reads, and a limit is what keeps a stuck script from filling a profile.
MAX_VIEWS = 20
#: The parameters a view never stores: the page, because a saved page three is a saved
#: nothing, and the view's own name.
NOT_SAVED = ("page", SAVED)


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
    #: ``text``, ``choice``, ``date``, ``number`` or empty for a column that does not
    #: narrow. A date takes a from and a to; a number takes a least and a most (#173).
    filter: str = ""
    #: For a ``date`` filter on a column that is a moment rather than a day: narrow by the
    #: day the moment falls on, so "to the 13th" includes the 13th (#173).
    datetime: bool = False
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
    #: The form field this cell edits, where it can be edited at all. Empty means the value
    #: is drawn and nothing else -- a count is not editable because it is a count, a date
    #: read off a posting belongs to the posting, and a status goes through a service that
    #: writes a timeline entry, so a cell that skipped it would be worse than no cell (#135).
    editable: str = ""
    #: What the pencil that opens the editor is called, with ``%(what)s`` standing for the
    #: row: *Rename %(what)s*. Required wherever ``editable`` is, because the pencil is an
    #: icon and an icon is not a name -- and a table of controls all called *Edit* is a list
    #: of links that all say the same thing (#252).
    edit_label: str = ""

    def __post_init__(self) -> None:
        if self.editable and not self.edit_label:
            raise ValueError(
                f"column {self.key!r} can be edited in place, but its pencil has no name"
            )

    @property
    def name(self) -> str:
        return self.param or self.key

    @property
    def sortable(self) -> bool:
        return bool(self.sort)


@dataclass(frozen=True)
class ExtraFilter:
    """A question a table narrows by that is not one column's own value (#314).

    *Status*, *Outcome*, *Tag* and *Gone quiet* on the applications table. Each is a query
    parameter the page's view reads, and each had a control in a form above the table. A
    column's header is where a table is narrowed since #253, so the control moves into the
    header of the column that shows what it asks about -- and since a column can be taken
    off the table, it names more than one in order of preference and goes to the first that
    is showing. With none of them showing it is still reachable, in the *Narrow* block
    (`Table.loose_controls`).

    **The table draws it and does not apply it.** What the parameter means is the view's
    to say: the board narrows by the same four and has no headers, so the narrowing stays
    where both shapes share it. This is where the control goes and what it offers.
    """

    #: The query parameter, and what the control's id is made from: ``filter-<name>``.
    name: str
    #: What the control is called wherever it needs a name of its own.
    label: str
    #: ``choice`` for a list, ``flag`` for a yes or a no -- a tick box that sends ``1``.
    kind: str = "choice"
    #: What a list offers, as (value, label) pairs. A table whose choices depend on who is
    #: asking -- a person's own tags -- answers `Table.choices_for` instead.
    choices: tuple = ()
    #: The columns whose header may hold it, the first one showing being the one that does.
    columns: tuple[str, ...] = ()


@dataclass
class Control:
    """One filter control as a template draws it: in a header, or under *Narrow* (#314).

    A column's own filter and a question the table carries for it (`ExtraFilter`) are the
    same thing to whoever draws them: a kind, a name to post under, an id, a label and a
    value. One shape for both is what lets one component, `<c-table.filter>`, draw every
    filter there is, and lets a header hold more than one.
    """

    #: ``text``, ``choice``, ``flag``, ``date`` or ``number``.
    kind: str
    #: The query parameter. A date posts ``<name>_from`` and ``<name>_to``, a number
    #: ``<name>_min`` and ``<name>_max``.
    name: str
    #: What its id is made from: ``filter-<key>``.
    key: str
    #: What it is called: the column's name for a column's own filter, the question's
    #: for one it carries.
    label: str
    choices: tuple = ()
    value: str = ""
    value_from: str = ""
    value_to: str = ""
    #: Whether a header writes its label above it. A column's own filter sits under the
    #: column's name and needs no other; *Outcome* in the status column's header does, or it
    #: is a second unnamed list under the first.
    titled: bool = False
    #: Whether no header holds it: a question none of whose columns is showing, or the
    #: filter in force of a column that is not. The *Narrow* block is where it is reached,
    #: at every width.
    loose: bool = False

    @property
    def input_id(self) -> str:
        return f"filter-{self.key}"

    @property
    def filter_label(self) -> str:
        return str(_("Filter by %(column)s") % {"column": str(self.label).lower()})

    @property
    def given(self) -> tuple[tuple[str, str], ...]:
        """What it posts, as (name, value) pairs: one, or a pair for a range."""
        if self.kind == "date":
            return (
                (f"{self.name}_from", self.value_from),
                (f"{self.name}_to", self.value_to),
            )
        if self.kind == "number":
            return (
                (f"{self.name}_min", self.value_from),
                (f"{self.name}_max", self.value_to),
            )
        return ((self.name, self.value),)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _value in self.given)

    @property
    def input_ids(self) -> tuple[str, ...]:
        """The ids of its inputs in a header: one, or a pair."""
        if self.kind == "date":
            return (f"{self.input_id}-from", f"{self.input_id}-to")
        if self.kind == "number":
            return (f"{self.input_id}-min", f"{self.input_id}-max")
        return (self.input_id,)

    @property
    def active(self) -> bool:
        """Whether it is narrowing the list right now."""
        return bool(self.value or self.value_from or self.value_to)


@dataclass
class Header:
    """A visible column as the template sees it: its sort state and the filters it holds."""

    column: Column
    state: str = ""  # "asc", "desc" or ""
    #: What the header links to next. ``None`` clears the sort, which is the third state:
    #: there was no way to undo a sort except by editing the address (#136).
    next_sort: str | None = ""
    #: What clicking the header would do, in words. The arrow says it to somebody who can
    #: see it; this says it to everybody else.
    hint: str = ""
    #: How wide this person likes the column, or 0 for *let it size itself* (#136).
    width: int = 0
    #: Whether one of this header's filter controls asked for the table being drawn
    #: (#626): see `open`.
    asked: bool = False
    #: The filters this header holds: the column's own first, then the questions the table
    #: put here (#314). Empty for a column that narrows nothing.
    controls: list[Control] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.column.key

    @property
    def label(self) -> str:
        return str(self.column.label)

    @property
    def filtered(self) -> bool:
        """Whether anything in this header is narrowing the list right now.

        Since #253 the filter is folded away until somebody opens it, and a filter nobody
        can see is a filter they forget: this is what keeps the column's own header open and
        marked, so "where did my companies go" has an answer on screen rather than in the
        address bar.
        """
        return any(control.active for control in self.controls)

    @property
    def input_ids(self) -> tuple[str, ...]:
        """The ids of every filter input in this header."""
        return tuple(ident for control in self.controls for ident in control.input_ids)

    @property
    def open(self) -> bool:
        """Whether the header's filters are drawn unfolded: narrowing, or being used (#626).

        A filter in force comes back open. So does the one whose control asked for this
        table, whatever it holds now: somebody who empties a box to try another word, or
        chooses *Any*, is still using it, and a header drawn shut around the control they
        are in takes the control off the screen and the focus with it.
        """
        return self.filtered or self.asked

    @property
    def filtering_label(self) -> str:
        """The name of the mark on a narrowed column, and only on a narrowed one.

        A `<th>`'s text is the name every cell under it is announced with, so anything put
        here is repeated down the whole column. That is worth paying when the column is
        narrowed -- the list is not what it looks like, and saying so once per cell is the
        cheap end of that -- and is not worth paying to announce a control that is simply
        there, which the disclosure's own collapsed state already says.
        """
        return str(_("%(column)s is filtered") % {"column": str(self.column.label)})

    @property
    def cell_label(self) -> str:
        """What the header cell itself is called: the column's name, and that it is
        filtered while it is.

        Said outright, as the cell's `aria-label`, because the cell's text stopped being
        that when the filters moved into it (#314). A cell with no name of its own takes
        one from everything inside it, and an open header holds its controls and what they
        are set to: every cell of the status column was announced under "Status Status is
        filtered Applied Outcome Still live Gone quiet Sort by status, lowest first". The
        name is the column's again. Whether it is narrowed is kept, for the reason
        `filtering_label` gives; what it is narrowed by is the controls' to say, and each
        of them still has its own name.
        """
        return self.filtering_label if self.filtered else self.label

    @property
    def sort_icon(self) -> str:
        """The glyph for this column's sort state, now that the control is icon-only (#253).

        Three states and three pictures. #136 made the unsorted state draw nothing, which
        read well while the label beside it was the control; an icon-only button with nothing
        to be is not a button anybody can find, so the neutral state has a glyph of its own.
        """
        return {"asc": "arrow-up", "desc": "arrow-down"}.get(self.state, "chevrons-up-down")


@dataclass
class ChooserRow:
    column: Column
    shown: bool
    first: bool = False
    last: bool = False


@dataclass(frozen=True)
class View:
    """One saved view: a name, the query string it stands for, the columns it was saved
    with, and whether the table opens as it (#259)."""

    name: str
    slug: str
    query: str
    columns: tuple[str, ...] = ()
    default: bool = False

    @classmethod
    def from_stored(cls, raw) -> View | None:
        """A view out of the profile's JSON, or nothing for a row that is not one.

        Read defensively: the JSON was written by an older Postulo, or restored from an
        archive, and a row missing its name is a row rather than an error.
        """
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()[:60]
        slug = str(raw.get("slug") or slugify(name, allow_unicode=True))[:60]
        if not name or not slug:
            return None
        columns = raw.get("columns")
        return cls(
            name=name,
            slug=slug,
            query=str(raw.get("query") or ""),
            columns=tuple(str(key) for key in columns) if isinstance(columns, list) else (),
            default=bool(raw.get("default")),
        )

    def stored(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "query": self.query,
            "columns": list(self.columns),
            "default": self.default,
        }


class Table:
    """A configurable table. Subclass, declare ``name`` and ``columns``, register."""

    #: The key the person's choices are stored under, and the settings view's address.
    name: str = ""
    #: What the table is, for its caption (#260): a screen reader asked to list the tables
    #: on a page gets this, and somebody landing inside it by keyboard hears it first.
    label: str = ""
    columns: tuple[Column, ...] = ()
    #: The sort applied when the request names none, with ``-`` for descending.
    default_sort: str = ""
    #: Query parameters outside the columns that also narrow the list and have no control
    #: this table draws (the masthead's search box, a tab). They count as filters for the
    #: empty state and the *Clear* link.
    extra_params: tuple[str, ...] = ()
    #: The questions outside the columns that the table does draw a control for, in one of
    #: its headers (#314). They count as filters too, and a saved view may hold them.
    extra_filters: tuple[ExtraFilter, ...] = ()
    #: What a row is called, for the live count: ("application", "applications").
    noun: tuple[str, str] = ("row", "rows")
    #: The shapes the page can take, the first being the usual one: the applications page
    #: is a table or a board of the same rows under the same filters (#102). Empty for a
    #: page that is a table and nothing else.
    shapes: tuple[str, ...] = ()
    #: What the masthead's search box is called on this table's page, where the box narrows
    #: the table by `q` instead of searching everything (#313): *Search companies*. Its name
    #: and its placeholder both. The page says it wants that by filling `base.html`'s
    #: `site_search` block; this is only the words.
    search_label: str = ""

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
        """The columns to show, in the person's order; the defaults when they chose none.

        A saved view named in the address brings its own columns (#259): choosing a view is
        meant to restore filters, sort and columns together, and the first two travel in the
        query string already. Only the columns it names that still exist are shown; a view
        whose every column has gone falls back to the person's usual ones rather than to
        nothing, and `view_gaps` says what was dropped.
        """
        keys = self.settings.get("columns")
        if not isinstance(keys, list):
            keys = self.default_columns()
        applied = self.applied_view
        if applied is not None and applied.columns:
            keys = [key for key in applied.columns if key in self.by_key] or keys
        chosen = [self.by_key[key] for key in keys if key in self.by_key]
        return chosen or [self.by_key[key] for key in self.default_columns()]

    # ------------------------------------------------------------- saved views

    @cached_property
    def views(self) -> list[View]:
        """This person's saved views of this table, as stored."""
        rows = self.settings.get("views")
        if not isinstance(rows, list):
            return []
        found = [View.from_stored(row) for row in rows]
        return [view for view in found if view is not None]

    @property
    def default_view(self) -> View | None:
        return next((view for view in self.views if view.default), None)

    @cached_property
    def applied_view(self) -> View | None:
        """The saved view the address names, if it names one that exists."""
        slug = self.params.get(SAVED, "").strip()
        if not slug or slug == PLAIN:
            return None
        return next((view for view in self.views if view.slug == slug), None)

    @property
    def view_rows(self) -> list[tuple[View, str, bool]]:
        """(view, its address, whether it is the one applied), for the *Views* control."""
        applied = self.applied_view
        return [(view, self.view_url(view), view == applied) for view in self.views]

    @property
    def applied_gaps(self) -> tuple[list[str], list[str]]:
        """`view_gaps` of the applied view, or nothing: what the page says it left out."""
        applied = self.applied_view
        return self.view_gaps(applied) if applied is not None else ([], [])

    def view_url(self, view: View, path: str | None = None) -> str:
        """The address a saved view stands for: the path, its query, and its own name.

        The name is on the address so that the columns it carries can be applied without
        being stored -- a link, not a change of state, and a bookmark that still works.
        ``path`` is for the one caller whose request is not the table's page: the view that
        keeps a view answers a POST to its own address and sends the person to the table's.
        """
        query = QueryDict(view.query, mutable=True)
        query[SAVED] = view.slug
        return f"{path or self.request.path}?{query.urlencode()}"

    @property
    def plain_url(self) -> str:
        """The table with no view applied, said explicitly: the way out of a default."""
        return f"{self.request.path}?{SAVED}={PLAIN}"

    @property
    def opening_url(self) -> str | None:
        """Where a bare address should go instead: the default view's, if there is one.

        Only for an address with no query at all. Anything with a parameter -- a filter, a
        sort, `saved=none` -- is somebody asking a question, and a default view answers the
        one they did not ask.
        """
        if self.params:
            return None
        default = self.default_view
        return self.view_url(default) if default is not None else None

    @classmethod
    def known_params(cls) -> set[str]:
        """Every query parameter this table reads: what a saved view may legitimately hold."""
        return {"sort", "page", SAVED, *cls.filter_names()}

    @classmethod
    def filter_names(cls) -> list[str]:
        """Every query parameter that narrows this table: the ones with no control here,
        the questions a header carries, and each column's own."""
        names = [*cls.extra_params, *(extra.name for extra in cls.extra_filters)]
        for column in cls.columns:
            if column.filter == "date":
                names += [f"{column.name}_from", f"{column.name}_to"]
            elif column.filter == "number":
                names += [f"{column.name}_min", f"{column.name}_max"]
            elif column.filter:
                names.append(column.name)
        return names

    def view_gaps(self, view: View) -> tuple[list[str], list[str]]:
        """What a saved view asks for that this table no longer has: (columns, parameters).

        The whole of the risk in keeping a view. A filter on a column that was removed, a
        sort on a field that was renamed, a column that went away: the view has to degrade to
        what it can still honour and *say so*, never fail -- and `Table` ignoring anything
        undeclared is what makes the first half free. This is the second half.
        """
        columns = [key for key in view.columns if key not in self.by_key]
        known = self.known_params()
        params = sorted(name for name in QueryDict(view.query) if name not in known)
        sort = QueryDict(view.query).get("sort", "").removeprefix("-")
        column = self.by_key.get(sort)
        if sort and (column is None or not column.sortable):
            params.append(f"sort={sort}")
        return columns, params

    @classmethod
    def save_view(cls, current: dict | None, name: str, query: str, columns: list[str]) -> dict:
        """``current`` with a view called ``name`` holding ``query``: added, or replaced if
        the name was already taken. The page number and the view's own name are dropped from
        the query, because neither is part of the question."""
        name = " ".join((name or "").split())[:60]
        slug = slugify(name, allow_unicode=True)[:60]
        if not name or not slug:
            return dict(current or {})
        kept = QueryDict(mutable=True)
        for key, values in QueryDict(query).lists():
            if key not in NOT_SAVED:
                kept.setlist(key, values)
        views = [view for view in cls._views_of(current) if view.slug != slug]
        was_default = any(view.default for view in cls._views_of(current) if view.slug == slug)
        views.append(
            View(
                name=name,
                slug=slug,
                query=kept.urlencode(),
                columns=tuple(key for key in columns if key in {c.key for c in cls.columns}),
                default=was_default,
            )
        )
        return cls._with_views(current, views[-MAX_VIEWS:])

    @classmethod
    def forget_view(cls, current: dict | None, slug: str) -> dict:
        return cls._with_views(current, [v for v in cls._views_of(current) if v.slug != slug])

    @classmethod
    def make_default(cls, current: dict | None, slug: str) -> dict:
        """Make one view the table's opening one, or with an unknown slug, make none."""
        views = [
            View(v.name, v.slug, v.query, v.columns, default=(v.slug == slug))
            for v in cls._views_of(current)
        ]
        return cls._with_views(current, views)

    @staticmethod
    def _views_of(current: dict | None) -> list[View]:
        rows = (current or {}).get("views")
        if not isinstance(rows, list):
            return []
        return [view for view in (View.from_stored(row) for row in rows) if view is not None]

    @staticmethod
    def _with_views(current: dict | None, views: list[View]) -> dict:
        return {**(current or {}), "views": [view.stored() for view in views]}

    def width_of(self, column: Column) -> int:
        """How wide this person likes this column, in pixels, or 0 for *let it size itself*.

        A width is a preference rather than a question, so it lives on the profile beside
        which columns show and how many rows a page holds -- and follows the person to every
        device rather than cluttering every link (#136).
        """
        widths = self.settings.get("widths")
        value = widths.get(column.key) if isinstance(widths, dict) else None
        return value if isinstance(value, int) and MIN_WIDTH <= value <= MAX_WIDTH else 0

    @property
    def visible_keys_in_order(self) -> list[str]:
        """The shown columns' keys in their order: what a saved view records (#259)."""
        return [column.key for column in self.visible]

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
    def shape(self) -> str:
        """The shape this person chose, or the usual one; empty for a page with one shape."""
        if not self.shapes:
            return ""
        chosen = self.settings.get("shape")
        return chosen if chosen in self.shapes else self.shapes[0]

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

    def next_sort(self, column: Column) -> str | None:
        """What clicking this header sorts by next, cycling back to the table's own order.

        Three states rather than two: a click sorted one way, a click sorted the other, and
        a click that gave up and went back. There was no way to undo a sort except editing
        the address, which is a real gap however small the fix (#136).

        ``None`` means *no sort of mine* — the template drops the parameter, and `sort`
        falls back to `default_sort`. A column that **is** the default sort has no such
        state to return to, so it keeps cycling between the two directions rather than
        offering a third click that changes nothing.
        """
        first = f"-{column.key}" if column.newest_first else column.key
        second = column.key if column.newest_first else f"-{column.key}"
        if not self.sort_state(column):
            return first
        if self.sort == first:
            return second
        if self.default_sort.removeprefix("-") == column.key:
            return first
        return None

    def sort_hint(self, column: Column) -> str:
        """What clicking would do, in words, for the link nobody can see an arrow on."""
        wanted = self.next_sort(column)
        if wanted is None:
            return str(_("Stop sorting by %(column)s") % {"column": str(column.label).lower()})
        if wanted.startswith("-"):
            return str(
                _("Sort by %(column)s, highest first") % {"column": str(column.label).lower()}
            )
        return str(_("Sort by %(column)s, lowest first") % {"column": str(column.label).lower()})

    # ----------------------------------------------------------------- filters

    def given(self, name: str) -> str:
        """The value of one filter: the first non-empty answer under that name.

        A filter is drawn twice -- in its column's header, and in the *Narrow* block a
        phone reaches -- and until #622 both copies belonged to one form and were posted
        together, under one name, on the understanding that the copy nobody typed in was
        empty. It was not, once a page had been loaded with a filter: the *Narrow* copy
        still held that value and came first, so the header's box could not change it.
        Each copy belongs to a form of its own now and a name is posted once. The first
        non-empty answer is still what is read, because the addresses written before
        then -- bookmarks, saved views -- carry every filter twice, one of them empty.
        """
        return next((value.strip() for value in self.params.getlist(name) if value.strip()), "")

    def filter(self, queryset):
        """Narrow by every declared column filter present in the request."""
        for column in self.columns:
            if column.filter == "text":
                value = self.given(column.name)
                if value:
                    condition = Q()
                    for lookup in column.lookups:
                        condition |= Q(**{f"{lookup}__icontains": value})
                    queryset = queryset.filter(condition)
            elif column.filter == "choice":
                value = self.given(column.name)
                if value and value in {str(choice) for choice, _label in column.choices}:
                    queryset = queryset.filter(**{column.lookups[0]: value})
            elif column.filter == "date":
                start = _date(self.given(f"{column.name}_from"))
                end = _date(self.given(f"{column.name}_to"))
                lookup = f"{column.lookups[0]}__date" if column.datetime else column.lookups[0]
                if start:
                    queryset = queryset.filter(**{f"{lookup}__gte": start})
                if end:
                    queryset = queryset.filter(**{f"{lookup}__lte": end})
            elif column.filter == "number":
                least = _number(self.given(f"{column.name}_min"))
                most = _number(self.given(f"{column.name}_max"))
                if least is not None:
                    queryset = queryset.filter(**{f"{column.lookups[0]}__gte": least})
                if most is not None:
                    queryset = queryset.filter(**{f"{column.lookups[0]}__lte": most})
        return queryset

    def apply(self, queryset):
        return self.filter(queryset).order_by(*self.ordering())

    @property
    def filters_active(self) -> bool:
        """Whether anything in the request narrows the list."""
        return any(self.given(name) for name in self.filter_names())

    @property
    def search(self) -> str:
        """What the table's search box holds: `q`, as the views that narrow by it read it."""
        return self.params.get("q", "")

    @property
    def search_keeps(self) -> list[tuple[str, str]]:
        """The rest of the question, for a search typed in the masthead with scripts off.

        The box lives in the masthead, outside the page's filter form, so pressing Enter in
        it without a script submits the masthead's form and nothing else. Without these, a
        search typed over a filtered, sorted table would throw the filters and the sort away,
        which the page's own box -- inside the filter form -- never did (#313). So that form
        carries every other parameter the page was asked with, except the page number (a
        new search starts at the first page) and the saved view's name (the question is no
        longer the one saved). Empty values are left out; they narrow nothing.

        With scripts on none of this is sent: the template puts it in a `<noscript>`, and
        htmx includes the page's filter form instead, as it always did for the page's box.
        """
        return [
            (name, value)
            for name, values in self.params.lists()
            if name not in ("q", "page", SAVED)
            for value in values
            if value.strip()
        ]

    @property
    def narrow_names(self) -> set[str]:
        """The names the *Narrow* block's own controls post: one per filter it draws."""
        return {name for control in self.narrow_controls for name in control.names}

    @property
    def narrow_keeps(self) -> list[tuple[str, str]]:
        """The rest of the question, for the *Narrow* block's form (#622).

        The block is a form of its own: its fields are second copies of the headers', and
        two copies in one form post one name twice. So it carries, hidden, everything the
        page was asked with that it has no field for -- the sort, the search, a tab, a
        shape -- or pressing its button would narrow by its fields and forget the rest.
        Not the page number, which a new question starts again from, nor a saved view's
        name, which the question no longer is; and nothing empty, which narrows nothing.
        The same rule as `search_keeps`, for the same reason.
        """
        own = self.narrow_names
        return [
            (name, value)
            for name, values in self.params.lists()
            if name not in own and name not in NOT_SAVED
            for value in values
            if value.strip()
        ]

    @property
    def clear_url(self) -> str:
        """The list with every filter removed and the sort kept.

        Said as the plain table where a default view exists (#259): the bare address opens
        as the default view, so *Clear* pointing at it would put the filters straight back.
        """
        path = self.request.path
        query = QueryDict(mutable=True)
        if self.sort and self.sort != self.default_sort:
            query["sort"] = self.sort
        if self.default_view is not None:
            query[SAVED] = PLAIN
        return f"{path}?{query.urlencode()}" if query else path

    # ---------------------------------------------------------------- template

    @property
    def asked_by(self) -> str:
        """The id of the control that asked for this table, where htmx says which (#626).

        htmx names the element that made a request in `HX-Trigger`. Only a live control
        sends it, so a page load, a bookmark and scripts off all answer nothing here, and
        every header is drawn as its filters alone say.
        """
        return self.request.headers.get("HX-Trigger", "")

    def choices_for(self, extra: ExtraFilter) -> tuple:
        """What a list among `extra_filters` offers, as (value, label) pairs.

        The declared choices, unless the table knows better: a subclass answers here for a
        list that depends on who is asking. Nothing to offer means the control is not drawn.
        """
        return extra.choices

    def _own_control(self, column: Column) -> Control | None:
        """A column's own filter with the value the address gives it, or nothing."""
        if not column.filter:
            return None
        control = Control(
            kind=column.filter,
            name=column.name,
            key=column.key,
            label=str(column.label),
            choices=column.choices,
        )
        if column.filter == "date":
            control.value_from = self.given(f"{column.name}_from")
            control.value_to = self.given(f"{column.name}_to")
        elif column.filter == "number":
            control.value_from = self.given(f"{column.name}_min")
            control.value_to = self.given(f"{column.name}_max")
        else:
            control.value = self.given(column.name)
        return control

    def _extra_control(self, extra: ExtraFilter) -> Control | None:
        """One of `extra_filters` with its value, or nothing for a list with no choices.

        The value is read as the view that narrows by it reads it -- the parameter itself,
        not the first copy that is not empty -- so the control cannot say one thing while
        the table does another.
        """
        choices = self.choices_for(extra) if extra.kind == "choice" else ()
        if extra.kind == "choice" and not choices:
            return None
        return Control(
            kind=extra.kind,
            name=extra.name,
            key=extra.name,
            label=str(extra.label),
            choices=tuple(choices),
            value=self.params.get(extra.name, "").strip(),
        )

    @cached_property
    def _placed(self) -> tuple[dict[str, list[Control]], list[Control]]:
        """Where every filter is drawn: by the key of the header that holds it, and the
        ones no header holds.

        A column's own filter goes in its own header. A question the table carries goes to
        the first of its columns that is showing. What is left is loose: a question none of
        whose columns is showing, which still has to be reachable, and the filter *in force*
        of a column that is not showing, which has to be seen and be clearable -- before,
        it narrowed the table with nothing on the page to say so, and was dropped by the
        next request. An unused filter of a hidden column is not offered: showing the
        column is how it is asked for.
        """
        held: dict[str, list[Control]] = {column.key: [] for column in self.visible}
        loose: list[Control] = []
        hidden: list[Control] = []
        for column in self.columns:
            control = self._own_control(column)
            if control is None:
                continue
            if column.key in held:
                held[column.key].append(control)
            elif control.active:
                control.loose = True
                hidden.append(control)
        for extra in self.extra_filters:
            control = self._extra_control(extra)
            if control is None:
                continue
            home = next((key for key in extra.columns if key in held), None)
            if home is None:
                control.loose = True
                loose.append(control)
                continue
            # A tick box carries its word beside it. A list needs its name written above it
            # unless it is the column's own word: *Status* under *Status* says nothing.
            control.titled = control.kind != "flag" and control.label != str(
                self.by_key[home].label
            )
            held[home].append(control)
        return held, loose + hidden

    @cached_property
    def headers(self) -> list[Header]:
        asked_by = self.asked_by
        held, _loose = self._placed
        headers = []
        for column in self.visible:
            header = Header(
                column=column,
                state=self.sort_state(column),
                next_sort=self.next_sort(column),
                hint=self.sort_hint(column),
                width=self.width_of(column),
                controls=held[column.key],
            )
            header.asked = bool(asked_by) and asked_by in header.input_ids
            headers.append(header)
        return headers

    @property
    def loose_controls(self) -> list[Control]:
        """The filters no header holds (#314): see `_placed`. The *Narrow* block draws them
        at every width, since it is the only place they are."""
        return self._placed[1]

    @property
    def loose_active(self) -> bool:
        """Whether a filter no header holds is narrowing the list: the *Narrow* block comes
        back open then, as a narrowed column's header does."""
        return any(control.active for control in self.loose_controls)

    @property
    def loose_kept(self) -> list[tuple[str, str]]:
        """What the loose filters hold, for the page's filter form to carry hidden.

        Their controls belong to the *Narrow* block's form (#622), so a header's control,
        which sends the page's form, would send the question without them.
        """
        return [
            (name, value)
            for control in self.loose_controls
            for name, value in control.given
            if value
        ]

    @property
    def narrow_controls(self) -> list[Control]:
        """Every filter there is a control for, in the order the *Narrow* block draws them:
        the headers' in the columns' order, then the ones no header holds."""
        return [
            *(control for header in self.headers for control in header.controls),
            *self.loose_controls,
        ]

    @property
    def narrow_marks(self) -> list[tuple[int, str]]:
        """How many of the *Narrow* block's fields are narrowing the list, for the mark on
        the word that opens it: (how many, where the block holds that many).

        On a phone the block is folded, and the headers that would say a filter is in
        force are a sideways scroll away inside the table: `?status=applied&tag=remote`
        showed a short list with nothing on the screen to say why (#314). The word carries
        the mark a narrowed column carries, and a count. For a filter a header holds the
        block stays folded: it holds every filter there, and opening it over the table
        for one of them would put the table off the screen instead. (One no header holds
        still opens it, `loose_active`: the block is the only place that one is.)

        The block holds different fields at different widths, so the count is of the
        fields it shows where it is read: every filter below `md` (``"phone"``), and from
        `md` up only the ones no header holds (``"desktop"``), since the rest are in their
        headers there and marked there. One entry with nowhere named when the two agree,
        and none at all while nothing narrows.
        """
        everywhere = sum(control.active for control in self.narrow_controls)
        loose = sum(control.active for control in self.loose_controls)
        if not everywhere:
            return []
        if loose == everywhere:
            return [(everywhere, "")]
        return [(everywhere, "phone"), *([(loose, "desktop")] if loose else [])]

    @property
    def has_filters(self) -> bool:
        """Whether the table has any filter to draw.

        Called `has_filter_row` until #253, when the row it was named after stopped
        existing: the filters live in the headers themselves now, one disclosure per column.
        What the question is actually for is unchanged -- whether to draw the block a phone
        reaches, which is still a block and still folded under one word.
        """
        return bool(self.narrow_controls)

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
        if "order" not in data and "show" not in data and "move" not in data:
            # A width drag posts neither: it says nothing about the columns, and under an
            # applied saved view the page it came from shows the view's, not the person's
            # own (#503). Keep what is stored.
            kept = [key for key in (current or {}).get("columns") or [] if key in order]
            columns = kept or columns
        if not columns:
            columns = cls.default_columns()

        try:
            page_size = int(data.get("page_size", ""))
        except ValueError:
            page_size = (current or {}).get("page_size", DEFAULT_PAGE_SIZE)
        if page_size not in PAGE_SIZES:
            page_size = DEFAULT_PAGE_SIZE

        widths = dict((current or {}).get("widths") or {})
        # One column at a time, because a width arrives from a drag rather than from a form
        # somebody filled in: `width` names the column and `px` says how wide.
        key = data.get("width", "")
        if key in {column.key for column in cls.columns}:
            try:
                pixels = int(data.get("px", ""))
            except ValueError:
                pixels = 0
            if pixels:
                widths[key] = max(MIN_WIDTH, min(MAX_WIDTH, pixels))
            else:
                # Zero is *let it size itself again*, which has to be reachable or a column
                # dragged too narrow once is too narrow for ever.
                widths.pop(key, None)

        cleaned = {"columns": columns, "page_size": page_size, "widths": widths}
        # The shape is chosen by its own control, not by the Columns form: carried across
        # a save of the columns rather than lost with it (#102).
        shape = (current or {}).get("shape")
        if shape in cls.shapes:
            cleaned["shape"] = shape
        # Saved views are kept by their own controls and live in the same dictionary: a
        # save of the columns is not a reason to lose them (#503).
        if isinstance((current or {}).get("views"), list):
            cleaned["views"] = current["views"]
        return cleaned


def _date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


def _number(text: str) -> Decimal | None:
    """A bound for a number filter, or nothing: what is not a number narrows nothing."""
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


# ------------------------------------------------------------------- registry

TABLES: dict[str, type[Table]] = {}


def register(table: type[Table]) -> type[Table]:
    """Make a table known to the settings view. Used as a class decorator."""
    if not table.name:
        raise ValueError(f"{table.__name__} needs a name to be registered.")
    # A question a header carries shares the header's markup with the column's own filter,
    # so it may not take a column's parameter or the id made from a column's key, and the
    # columns it names have to be ones the table has (#314).
    keys = {column.key for column in table.columns}
    taken = {column.key for column in table.columns if column.filter}
    taken |= {column.name for column in table.columns if column.filter}
    taken |= {"sort", "page", SAVED, *table.extra_params}
    for extra in table.extra_filters:
        if extra.name in taken:
            raise ValueError(f"{table.__name__}: the filter {extra.name!r} is already taken.")
        if extra.kind not in ("choice", "flag"):
            raise ValueError(f"{table.__name__}: {extra.name!r} is neither a list nor a flag.")
        unknown = [key for key in extra.columns if key not in keys]
        if unknown:
            raise ValueError(f"{table.__name__}: {extra.name!r} names no column {unknown}.")
        taken.add(extra.name)
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
