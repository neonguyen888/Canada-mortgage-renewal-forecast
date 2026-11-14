"""Tests for the Power BI project generator. Author: Neo Nguyen

Power BI Desktop isn't available on a Mac, so these check what can go wrong before the file is
opened on Windows: a measure clashing with a column name, a visual pointing at a field that
doesn't exist, or a visual hanging off the edge of the page.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "powerbi"))
import build_pbip as pb  # noqa: E402


@pytest.fixture(scope="module")
def model():
    tables = pb.build_tables()
    return {t.name: t for t in tables}, pb.build_pages()


def test_measure_names_do_not_clash_with_tables_or_columns(model):
    tables, _ = model
    pb.check_names(list(tables.values()))


def test_every_visual_field_exists_in_the_model(model):
    tables, pages = model
    measures = {m[0] for t in tables.values() for m in t.measures}
    for page in pages:
        for v in page.visuals:
            for state in v["visual"].get("query", {}).get("queryState", {}).values():
                for p in state["projections"]:
                    f = p["field"]
                    if "Measure" in f:
                        assert f["Measure"]["Property"] in measures, (page.name, v["name"])
                    else:
                        table = f["Column"]["Expression"]["SourceRef"]["Entity"]
                        assert f["Column"]["Property"] in {c.name for c in tables[table].cols}, (page.name, v["name"])


def test_visuals_fit_on_the_page(model):
    _, pages = model
    for page in pages:
        for v in page.visuals:
            pos = v["position"]
            assert pos["x"] >= 0 and pos["y"] >= 0, v["name"]
            assert pos["x"] + pos["width"] <= pb.PAGE_W + 0.5, (page.name, v["name"])
            assert pos["y"] + pos["height"] <= pb.PAGE_H + 0.5, (page.name, v["name"])


def test_slicer_defaults_are_real_options(model):
    # A default that isn't one of the choices would leave the slicer empty on first open.
    tables, pages = model
    options = {}
    for t in tables.values():
        if t.m_source and "#table" in t.m_source:
            options[t.name] = t.m_source
    for page in pages:
        for v in page.visuals:
            if v["visual"]["visualType"] != "slicer":
                continue
            where = v["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]["Where"][0]
            default = where["Condition"]["In"]["Values"][0][0]["Literal"]["Value"].strip("'")
            table = v["visual"]["query"]["queryState"]["Values"]["projections"][0]["field"]["Column"]["Expression"]["SourceRef"]["Entity"]
            assert json.dumps(default) in options[table], (v["name"], default)
