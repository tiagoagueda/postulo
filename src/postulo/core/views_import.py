"""Importing a spreadsheet through the web interface: upload, map and preview, import."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import csv_import

SECTION = {"section_title": _("Your data")}


@login_required
def import_csv(request: HttpRequest):
    """Step one: the file. Step two, on the same address once a file is held: the mapping."""
    sheet = csv_import.unstash(request.session)

    if request.method == "POST" and "file" in request.FILES:
        upload = request.FILES["file"]
        if upload.size > csv_import.MAX_BYTES:
            messages.error(request, _("The file is over 2 MB. A job search is not that big."))
            return redirect("core:import_csv")
        data = upload.read()
        try:
            csv_import.read_sheet(data, upload.name)
        except csv_import.SheetError as error:
            messages.error(request, str(error))
            return redirect("core:import_csv")
        csv_import.stash(request.session, data, upload.name)
        return redirect("core:import_csv")

    if request.method == "POST" and request.POST.get("start_over"):
        csv_import.forget(request.session)
        return redirect("core:import_csv")

    if sheet is None:
        return render(request, "core/import_csv.html", {**SECTION})

    if request.method == "POST":
        mapping = csv_import.clean_mapping(
            [request.POST.get(f"column_{index}", "ignore") for index in range(len(sheet.headers))],
            sheet.headers,
        )
        day_first = request.POST.get("date_order", "day_first") == "day_first"
    else:
        mapping = csv_import.guess_mapping(sheet.headers)
        day_first = True

    if request.method == "POST" and request.POST.get("action") == "import":
        if not any(key == "company" for key in mapping) or not any(
            key == "role" for key in mapping
        ):
            messages.error(request, _("Map a column to Company and one to Role first."))
        else:
            report = csv_import.perform(request.user, sheet, mapping, day_first=day_first)
            csv_import.forget(request.session)
            return render(request, "core/import_csv_done.html", {**SECTION, "report": report})

    parsed = csv_import.parse_rows(sheet, mapping, day_first=day_first)
    return render(
        request,
        "core/import_csv_map.html",
        {
            **SECTION,
            "sheet": sheet,
            "columns": list(zip(range(len(sheet.headers)), sheet.headers, mapping, strict=True)),
            "fields": csv_import.FIELDS,
            "day_first": day_first,
            "preview": parsed[: csv_import.PREVIEW_ROWS],
            "would_import": sum(1 for row in parsed if row.becomes == "application"),
            "would_list": sum(1 for row in parsed if row.becomes == "listing"),
            "would_skip": sum(1 for row in parsed if row.becomes == "skipped"),
        },
    )


@login_required
def import_csv_template(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(csv_import.template_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="postulo-applications.csv"'
    return response


@require_POST
@login_required
def import_csv_forget(request: HttpRequest) -> HttpResponse:
    csv_import.forget(request.session)
    return redirect("core:import_csv")
