"""`manage.py fetch_earnings`: Eurostat's tables reduced to the earnings plugin's file (#1257).

No test here reaches the network. The answers are JSON-stat 2.0 documents built in the shape
Eurostat's API sends (values keyed by position, the last dimension fastest), small enough to
read, and handed to the command through a client that records what it was asked.
"""

from __future__ import annotations

import datetime as dt
import io
import itertools
import json

import pytest
from django.core.management.base import CommandError

from postulo.plugins.earnings import eurostat, reader
from postulo.plugins.management.commands import fetch_earnings

#: Twenty-one countries with a 2022 mean, the fewest the survey has in the file's check.
COUNTRIES = "AT BE BG CY CZ DE DK EE EL ES FI FR HR HU IE IT LT LU LV MT NL".split()


def jsonstat(dimensions: dict[str, list[str]], values: dict[tuple, float], updated="2026-02-09"):
    """A JSON-stat 2.0 dataset over ``dimensions`` (in order), with ``values`` by their codes."""
    names = list(dimensions)
    sizes = [len(codes) for codes in dimensions.values()]
    positions = {}
    for combination in itertools.product(*dimensions.values()):
        if combination in values:
            position = 0
            for name, size, code in zip(names, sizes, combination, strict=True):
                position = position * size + dimensions[name].index(code)
            positions[str(position)] = values[combination]
    return {
        "version": "2.0",
        "class": "dataset",
        "updated": updated,
        "id": names,
        "size": sizes,
        "dimension": {
            name: {"category": {"index": {code: at for at, code in enumerate(codes)}}}
            for name, codes in dimensions.items()
        },
        "value": positions,
    }


def means(period_scale: float, geos=(*COUNTRIES, "UK", "EU27_2020")) -> dict:
    dimensions = {
        "isco08": ["TOTAL", "OC1-5", "OC2"],
        "unit": ["EUR", "NAC"],
        "geo": list(geos),
        "time": ["2022"],
    }
    values = {}
    for geo in geos:
        for group, base in (("TOTAL", 30000), ("OC1-5", 35000), ("OC2", 50000)):
            amount = round(base * period_scale, 2)
            values[(group, "EUR", geo, "2022")] = amount
            values[(group, "NAC", geo, "2022")] = round(amount * (7.5 if geo == "HR" else 1), 2)
    return jsonstat(dimensions, values)


def main_cube() -> dict:
    geos = (*COUNTRIES, "UK", "TR")
    dimensions = {
        "isco08": ["TOTAL", "OC1-5", "OC2"],
        "indic_se": ["MED_E_EUR", "D1_E_EUR", "D9_E_EUR"],
        "geo": list(geos),
        "time": ["2018", "2022"],
    }
    values = {}
    for geo, group, indicator, time in itertools.product(
        geos, *list(dimensions.values())[:2], ["2018", "2022"]
    ):
        if group == "OC2" and time == "2022":
            continue  # as Eurostat: no single-group median for the 2022 wave
        if geo == "TR" and time == "2022":
            continue
        values[(group, indicator, geo, time)] = {"MED_E_EUR": 20000, "D1_E_EUR": 10000}.get(
            indicator, 40000
        )
    return jsonstat(dimensions, values)


def minimum() -> dict:
    geos = ["BG", "EL", "HR", "CZ", "US", "UK"]
    values = {}
    for geo in geos:
        values[("EUR", geo, "2026-S2")] = 900
        values[("NAC", geo, "2026-S2")] = 22400 if geo == "CZ" else (1257 if geo == "US" else 900)
    return jsonstat(
        {"currency": ["EUR", "NAC"], "geo": geos, "time": ["2026-S2"]}, values, "2026-07-31"
    )


def answers() -> dict:
    scale = {"year": 1, "month": 1 / 14, "hour": 1 / 1680}
    found = {code: means(scale[period]) for period, code in eurostat.detailed(2022).items()}
    for code, _worktime in eurostat.MAIN.values():
        found[code] = main_cube()
    found[eurostat.MINIMUM] = minimum()
    return found


class Response:
    def __init__(self, document):
        self.document = document

    def raise_for_status(self):
        if self.document is None:
            raise fetch_earnings.httpx.HTTPStatusError("503", request=None, response=None)

    def json(self):
        return self.document


class FakeClient:
    def __init__(self, served):
        self.served = served
        self.asked = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, address):
        self.asked.append(address)
        code = address.removeprefix(eurostat.API).partition("?")[0]
        return Response(self.served.get(code))


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "data_dir", lambda: tmp_path)
    reader.forget()
    yield tmp_path
    reader.forget()


def run(monkeypatch, served, **options):
    client = FakeClient(served)
    monkeypatch.setattr(fetch_earnings, "public_only_client", lambda **kw: client)
    command = fetch_earnings.Command()
    command.stdout = io.StringIO()
    command.handle(wave=options.get("wave", 2022))
    return client, command.stdout.getvalue()


def written(directory) -> dict:
    return json.loads((directory / reader.FILE).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------- reading


def test_cells_read_a_row_major_array_with_the_last_dimension_fastest():
    document = jsonstat({"a": ["x", "y"], "b": ["1", "2", "3"]}, {("y", "1"): 5, ("x", "3"): 7})

    assert document["value"] == {"3": 5, "2": 7}
    found = {(tuple(sorted(key.items())), value) for key, value in eurostat.cells(document)}

    assert found == {((("a", "y"), ("b", "1")), 5), ((("a", "x"), ("b", "3")), 7)}


@pytest.mark.parametrize("broken", [{}, {"id": ["a"], "size": [2], "dimension": {}, "value": {}}])
def test_what_is_not_json_stat_is_refused(broken):
    with pytest.raises(eurostat.Refused):
        list(eurostat.cells(broken))


# -------------------------------------------------------------------- the command


def test_seven_requests_to_one_host_and_a_file_the_reader_reads(monkeypatch, data_dir):
    client, said = run(monkeypatch, answers())

    assert len(client.asked) == 7
    assert all(address.startswith(eurostat.API) for address in client.asked)
    assert "22 countries, wave 2022, minimum wage 2026-S2" in said
    document = written(data_dir)
    assert eurostat.problems(document) == []
    assert document["retrieved"] == dt.date.today().isoformat()
    assert reader.document() == document, "the newer file is the one read"
    assert {f.measure for f in reader.figures("2512", "DE")} == {"mean", "median", "p10", "p90"}


def test_the_united_kingdom_the_united_states_and_the_aggregates_are_left_out(
    monkeypatch, data_dir
):
    run(monkeypatch, answers())
    countries = written(data_dir)["countries"]

    assert {"UK", "GB", "US", "EU27_2020", "EL"}.isdisjoint(countries)
    assert "GR" in countries, "Eurostat's EL is stored as GR"


def test_amounts_are_kept_unchanged_under_the_currency_they_were_published_in(
    monkeypatch, data_dir
):
    run(monkeypatch, answers())
    countries = written(data_dir)["countries"]

    assert countries["HR"]["groups"]["2"]["year"]["mean"]["2022"] == {
        "EUR": 50000,
        "HRK": 375000,
    }, "Croatia's 2022 national figure is kuna"
    assert countries["DE"]["groups"]["2"]["year"]["mean"]["2022"] == {"EUR": 50000}
    assert countries["DE"]["groups"]["1-5"]["year"]["median"] == {
        "2018": {"EUR": 20000},
        "2022": {"EUR": 20000},
    }
    assert countries["CZ"]["minimum"] == {"when": "2026-S2", "month": {"EUR": 900, "CZK": 22400}}
    assert countries["BG"]["minimum"]["month"] == {"EUR": 900}, (
        "a national figure equal to the euro"
    )


def test_a_country_with_only_an_earlier_wave_is_kept_and_not_declared(monkeypatch, data_dir):
    run(monkeypatch, answers())

    assert "TR" in written(data_dir)["countries"]
    assert "TR" not in reader.countries()


def test_a_short_answer_is_refused_and_the_file_left_as_it_was(monkeypatch, data_dir):
    (data_dir / reader.FILE).write_text("kept", encoding="utf-8")
    served = answers()
    for code in eurostat.detailed(2022).values():
        served[code] = means(1, geos=("DE", "FR"))

    with pytest.raises(CommandError, match="only 2 countries"):
        run(monkeypatch, served)

    assert (data_dir / reader.FILE).read_text(encoding="utf-8") == "kept"


def test_a_table_that_does_not_answer_writes_nothing(monkeypatch, data_dir):
    served = answers()
    served[eurostat.MINIMUM] = None

    with pytest.raises(CommandError, match="earn_mw_cur"):
        run(monkeypatch, served)

    assert not (data_dir / reader.FILE).exists()


def test_a_negative_amount_is_refused(monkeypatch, data_dir):
    served = answers()
    served[eurostat.MINIMUM]["value"]["0"] = -1

    with pytest.raises(CommandError, match="not a positive amount"):
        run(monkeypatch, served)


def test_the_wave_names_the_detailed_tables(monkeypatch):
    asked = eurostat.queries(2026)

    assert {"earn_ses26_28", "earn_ses26_21", "earn_ses26_14"} <= set(asked)
    assert "time=2022&time=2026" in asked["earn_ses_annual"]
    assert len(asked) == 7
