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


PAGE_IDS = ["overview", "accuracy", "payment", "triage", "about"]


def _state(entries, sid="default"):
    return next(e["properties"] for e in entries if e.get("selector", {}).get("id") == sid)


def test_pages_are_in_order(model):
    _, pages = model
    assert [p.name for p in pages] == PAGE_IDS


def test_theme_is_light():
    t = pb.theme()
    assert t["visualStyles"]["page"]["*"]["background"][0]["color"]["solid"]["color"] == "#F4F6F9"
    assert t["dataColors"][:4] == ["#2F5F96", "#16917F", "#C26B12", "#6E52A3"]


def test_every_page_has_a_rail_button_for_every_page(model):
    _, pages = model
    for page in pages:
        buttons = {v["name"]: v for v in page.visuals if v["name"].startswith("nav_")}
        assert set(buttons) == {f"nav_{n}" for n in PAGE_IDS}, page.name
        for name, v in buttons.items():
            assert v["visual"]["visualType"] == "actionButton"
            link = v["visual"]["visualContainerObjects"]["visualLink"][0]["properties"]
            assert link["type"]["expr"]["Literal"]["Value"] == "'PageNavigation'"
            assert link["navigationSection"]["expr"]["Literal"]["Value"] == f"'{name[4:]}'"


def test_current_page_button_is_highlighted(model):
    _, pages = model
    for page in pages:
        buttons = [v for v in page.visuals if v["name"].startswith("nav_")]
        assert len(buttons) == len(PAGE_IDS), page.name
        for v in buttons:
            fill = _state(v["visual"]["objects"]["fill"])
            want = "'#2F5F96'" if v["name"] == f"nav_{page.name}" else "'#1F3A5F'"
            assert fill["fillColor"]["solid"]["color"]["expr"]["Literal"]["Value"] == want


def test_rail_button_integers_are_typed_as_integers(model):
    # Power BI ignores a button's formatting when an integer property is written as a decimal ("12D").
    _, pages = model
    for page in pages:
        for v in page.visuals:
            if v["name"].startswith("nav_"):
                objs = v["visual"]["objects"]
                assert _state(objs["text"])["leftMargin"]["expr"]["Literal"]["Value"].endswith("L")
                assert objs["shape"][0]["properties"]["roundEdge"]["expr"]["Literal"]["Value"].endswith("L")


def test_rail_button_show_flags_apply_to_every_state(model):
    # In a button, "show" lives in an entry without a state selector; Power BI ignores it inside a state.
    _, pages = model
    for page in pages:
        for v in page.visuals:
            if v["name"].startswith("nav_"):
                for obj, entries in v["visual"]["objects"].items():
                    for e in entries:
                        if "show" in e["properties"]:
                            assert "selector" not in e, (v["name"], obj)


def test_headline_cards_use_multi_row_card_properties(model):
    # An unknown property ("outline" instead of "outlineStyle") makes Power BI drop all of a visual's formatting.
    known = {"barColor", "barShow", "barWeight", "cardBackground", "cardPadding", "outlineColor", "outlineStyle", "outlineWeight"}
    _, pages = model
    for page in pages:
        for v in page.visuals:
            if v["visual"]["visualType"] == "multiRowCard":
                card = v["visual"]["objects"]["card"][0]["properties"]
                assert set(card) <= known, (page.name, v["name"], set(card) - known)
                assert card["outlineStyle"]["expr"]["Literal"]["Value"].endswith("L")


def test_headline_text_is_styled_as_the_card_title(model):
    # A multi-row card draws a text value as its card title, so the headline style must go on cardTitle.
    _, pages = model
    for page in pages:
        hl = next(v for v in page.visuals if v["name"] == "hdr_headline")
        if hl["visual"]["visualType"] != "multiRowCard":
            continue
        title = hl["visual"]["objects"]["cardTitle"][0]["properties"]
        assert title["fontSize"]["expr"]["Literal"]["Value"] == "18D", page.name
        assert title["color"]["solid"]["color"]["expr"]["Literal"]["Value"] == "'#1B1F24'", page.name


def test_fonts_are_ones_power_bi_knows(model):
    # An unknown font name ("Segoe UI Bold") falls back to a serif face in cards.
    import re
    known = {"Segoe UI", "Segoe UI Light", "Segoe UI Semibold", "Segoe (Bold)"}
    _, pages = model
    for page in pages:
        blob = json.dumps([v["visual"] for v in page.visuals])
        used = set(re.findall(r'"fontFamily": \{"expr": \{"Literal": \{"Value": "\'([^\']+)\'"', blob))
        used |= set(re.findall(r'"fontFamily": "([^"]+)"', blob))
        assert used <= known, (page.name, used - known)


def test_content_clears_the_rail(model):
    _, pages = model
    for page in pages:
        for v in page.visuals:
            if v["name"].startswith(("nav_", "rail_")):
                assert v["position"]["x"] + v["position"]["width"] <= pb.RAIL_W, v["name"]
            else:
                assert v["position"]["x"] >= pb.RAIL_W, (page.name, v["name"])


def test_headline_box_is_wide_enough(model):
    _, pages = model
    for page in pages:
        hl = next(v for v in page.visuals if v["name"] == "hdr_headline")
        assert hl["position"]["width"] >= pb.CONTENT_W - 10, page.name


NEW_MEASURES = ["Previous Actual Rate", "Latest Rate Change bps", "Overview Headline",
                "Accuracy Headline", "Payment Headline", "Triage Headline"]


def _expr(tables, name):
    return next(m[1] for m in tables["measures_calc"].measures if m[0] == name)


def test_new_measures_exist(model):
    tables, _ = model
    names = {m[0] for m in tables["measures_calc"].measures}
    assert set(NEW_MEASURES) <= names


def test_payment_headline_handles_a_fall(model):
    # A renewal rate below the old rate lowers the payment: the headline must say "saves", not "adds -$".
    tables, _ = model
    e = _expr(tables, "Payment Headline")
    assert '"adds"' in e and '"saves"' in e and "ABS(" in e


def test_payment_headline_says_an_before_eight(model):
    # "An $800k renewal", read as "an eight hundred k".
    tables, _ = model
    e = _expr(tables, "Payment Headline")
    assert '"An "' in e and '"8"' in e


def test_rate_caption_handles_no_change_and_blank(model):
    tables, _ = model
    e = _expr(tables, "Latest Rate Caption")
    assert "no change" in e and "ISBLANK(" in e and "▲" in e and "▼" in e


def test_overview_headline_names_the_scenario(model):
    tables, _ = model
    e = _expr(tables, "Overview Headline")
    assert "50 bps lower" in e and "50 bps higher" in e


def test_accuracy_headline_handles_ets_winning(model):
    # Today the bond-yield model wins at both horizons; new data could change that.
    tables, _ = model
    e = _expr(tables, "Accuracy Headline")
    assert "less than ETS" in e and "more than ETS" in e


def test_triage_headline_handles_no_gain(model):
    tables, _ = model
    e = _expr(tables, "Triage Headline")
    assert "more at-risk borrowers than calling by date" in e and "calling by date reaches" in e


def test_tile_slicers_have_room_for_every_option(model):
    # Power BI won't shrink a tile below about 66 px; a narrower slicer hides options behind an arrow.
    tables, pages = model
    for page in pages:
        for v in page.visuals:
            vis = v["visual"]
            if vis["visualType"] != "slicer":
                continue
            if vis["objects"]["data"][0]["properties"]["mode"]["expr"]["Literal"]["Value"] == "'Dropdown'":
                continue
            table = vis["query"]["queryState"]["Values"]["projections"][0]["field"]["Column"]["Expression"]["SourceRef"]["Entity"]
            n = tables[table].m_source.count('{"')  # one row literal per option in the #table source
            assert n >= 2, table
            assert v["position"]["width"] >= 70 * n, (page.name, v["name"], n)


def test_kpi_values_set_display_units(model):
    # The card's default "Auto" units turn $2,059.72 into $2.06K.
    _, pages = model
    for page in pages:
        for v in page.visuals:
            if v["name"].startswith("kpi_") and v["name"].endswith("_value"):
                assert "labelDisplayUnits" in v["visual"]["objects"]["labels"][0]["properties"], v["name"]


def test_analysis_pages_have_a_data_headline(model):
    _, pages = model
    want = {"overview": "Overview Headline", "accuracy": "Accuracy Headline",
            "payment": "Payment Headline", "triage": "Triage Headline"}
    for page in pages:
        if page.name in want:
            hl = next(v for v in page.visuals if v["name"] == "hdr_headline")
            prop = hl["visual"]["query"]["queryState"]["Values"]["projections"][0]["field"]["Measure"]["Property"]
            assert prop == want[page.name]


def test_about_page_explains_the_project(model):
    _, pages = model
    about = next(p for p in pages if p.name == "about")
    text = json.dumps([v["visual"] for v in about.visuals], ensure_ascii=False)
    for must in ["V122667786", "BD.CDN.5YR.DQ.YLD", "ETS", "Diebold-Mariano", "5,000",
                 "https://github.com/neonguyen888/Canada-mortgage-renewal-forecast"]:
        assert must in text, must
    assert "—" not in text  # no em dashes


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


def test_visual_names_are_unique_on_each_page(model):
    # Each visual is saved as visuals/<name>/visual.json, so a repeated name overwrites the first.
    _, pages = model
    for page in pages:
        names = [v["name"] for v in page.visuals]
        assert len(names) == len(set(names)), page.name


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
