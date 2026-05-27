"""Tests for extract_required_from_dmn.py and scripts.dmn.parser."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts import extract_required_from_dmn as cli
from scripts.dmn.parser import parse_dmn_xml


FIXTURES = Path(__file__).parent / "fixtures" / "dmn"


# ----------------------------- parser unit tests -----------------------------


def test_parse_dmn_extracts_decision_metadata() -> None:
    table = parse_dmn_xml(
        (FIXTURES / "S_MINI.dmn").read_bytes(),
        "fixtures/S_MINI.dmn",
    )
    assert table is not None
    assert table.decision_id == "MINI-S_EVENT_VARIABLEN"
    assert table.hit_policy == "FIRST"
    assert table.input_label == "eventName"
    assert table.output_columns == (
        "absender",
        "empfaenger",
        "sparte",
        "kategorie",
        "transaktionsgrund",
        "pruefidentifikator",
        "marktlokationsId",
    )
    assert len(table.rules) == 3


def test_parse_dmn_extracts_rules_with_event_names() -> None:
    table = parse_dmn_xml((FIXTURES / "S_MINI.dmn").read_bytes(), "x")
    assert table is not None
    names = [r.event_name for r in table.rules]
    assert names == ["COMMON_CORE_EVENT", "START_VERSAND_ANTWORT_NNA", "MINIMAL_EVENT"]


def test_parse_dmn_returns_none_on_xml_without_decision(tmp_path: Path) -> None:
    empty = tmp_path / "empty.dmn"
    empty.write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"/>'
    )
    assert parse_dmn_xml(empty.read_bytes(), "irrelevant") is None


def test_parse_dmn_strips_outer_quotes_and_handles_empty_cells() -> None:
    table = parse_dmn_xml((FIXTURES / "S_MINI.dmn").read_bytes(), "x")
    assert table is not None
    cc = next(r for r in table.rules if r.event_name == "COMMON_CORE_EVENT")
    # Quotes around eventName stripped:
    assert cc.event_name == "COMMON_CORE_EVENT"
    by_col = dict(cc.outputs)
    # Quoted FN-string starts with FN: (outer quotes stripped):
    assert by_col["absender"].startswith("FN:GetDataFromInbound")
    # Empty <text></text> cells become None:
    assert by_col["transaktionsgrund"] is None
    assert by_col["pruefidentifikator"] is None
    # Description stripped:
    assert cc.description.startswith("Common-Core")


# ----------------------------- jsonpath classification -----------------------------


def test_classify_path_recognises_blocks() -> None:
    assert cli.classify_path("$.transaktionsdaten.sparte") == ("transaktionsdaten", "sparte")
    assert cli.classify_path("$.stammdaten.MARKTLOKATION[0].marktlokationsId") == (
        "stammdaten",
        "MARKTLOKATION",
    )
    assert cli.classify_path("$.zusatzdaten.erpEvent.zusatzdaten.prozessId") == (
        "zusatzdaten",
        "erpEvent",
    )
    assert cli.classify_path("$.something.else") == ("other", None)


def test_extract_jsonpaths_handles_primary_and_alt() -> None:
    value = (
        "FN:GetDataFromInbound("
        "jsonPath=$.transaktionsdaten.vertragsbeginn,"
        "altJsonPath=$.stammdaten.MARKTLOKATION[0].vertragsbeginn)"
    )
    paths = cli.extract_jsonpaths(value)
    assert paths == [
        "$.transaktionsdaten.vertragsbeginn",
        "$.stammdaten.MARKTLOKATION[0].vertragsbeginn",
    ]


def test_extract_jsonpaths_skips_literal_outputs() -> None:
    assert cli.extract_jsonpaths('"STROM"') == []
    assert cli.extract_jsonpaths("") == []


# ----------------------------- rule analysis -----------------------------


def test_analyze_rule_common_core() -> None:
    table = parse_dmn_xml((FIXTURES / "S_MINI.dmn").read_bytes(), "x")
    assert table is not None
    cc = next(r for r in table.rules if r.event_name == "COMMON_CORE_EVENT")
    entry = cli.analyze_rule(cc)
    assert entry["required_transaktionsdaten"] == [
        "absender",
        "empfaenger",
        "kategorie",
        "sparte",
    ]
    assert entry["stammdaten_reads"] == ["MARKTLOKATION"]
    assert entry["required_zusatzdaten"] == []
    assert entry["pruefidentifikator_source"] is None


def test_analyze_rule_nna_marks_pruefi_source_transaktionsdaten() -> None:
    table = parse_dmn_xml((FIXTURES / "S_MINI.dmn").read_bytes(), "x")
    assert table is not None
    nna = next(r for r in table.rules if r.event_name == "START_VERSAND_ANTWORT_NNA")
    entry = cli.analyze_rule(nna)
    assert entry["pruefidentifikator_source"] == "transaktionsdaten"
    assert "pruefidentifikator" in entry["required_transaktionsdaten"]


def test_analyze_rule_minimal_marks_pruefi_source_erp_event() -> None:
    table = parse_dmn_xml((FIXTURES / "S_MINI.dmn").read_bytes(), "x")
    assert table is not None
    rule = next(r for r in table.rules if r.event_name == "MINIMAL_EVENT")
    entry = cli.analyze_rule(rule)
    assert entry["pruefidentifikator_source"] == "erpEvent.eventName"
    # pruefi-Pfad ist im zusatzdaten-Block, NICHT in transaktionsdaten:
    assert "pruefidentifikator" not in entry["required_transaktionsdaten"]
    assert "erpEvent" in entry["required_zusatzdaten"]


# ----------------------------- CLI integration tests -----------------------------


def _populate_corpus(
    root: Path,
    *,
    role: str = "lf",
    format_version: str = "202604",
) -> Path:
    target = (
        root / f"maco-{role}-processes" / format_version / "S_TABELLEN"
    )
    target.mkdir(parents=True)
    shutil.copy(FIXTURES / "S_MINI.dmn", target / "S_EVENT_VARIABLEN.dmn")
    return root


def _load_output(out_path: Path) -> dict:
    return json.loads(out_path.read_text(encoding="utf-8"))


def test_cli_emits_event_required_fields_with_expected_structure(
    tmp_path: Path,
) -> None:
    processes_root = _populate_corpus(tmp_path)
    output = tmp_path / "event-required-fields.json"

    exit_code = cli.main(
        ["--processes-root", str(processes_root), "--output", str(output)]
    )

    assert exit_code == cli.EXIT_OK
    doc = _load_output(output)
    assert set(doc.keys()) == {"_provenance", "_aggregate", "events"}
    assert list(doc["events"]) == ["202604"]
    assert list(doc["events"]["202604"]) == ["LF"]
    by_event = doc["events"]["202604"]["LF"]
    assert set(by_event) == {"COMMON_CORE_EVENT", "START_VERSAND_ANTWORT_NNA", "MINIMAL_EVENT"}
    assert by_event["COMMON_CORE_EVENT"]["required_transaktionsdaten"] == [
        "absender",
        "empfaenger",
        "kategorie",
        "sparte",
    ]


def test_cli_aggregate_identifies_common_core(tmp_path: Path) -> None:
    processes_root = _populate_corpus(tmp_path)
    output = tmp_path / "out.json"
    # Field frequencies across the 3 fixture rules:
    #   absender   3/3 (100%)  ← in all rules
    #   empfaenger 3/3 (100%)  ← in all rules
    #   kategorie  2/3 (66%)   ← Common-Core + NNA
    #   sparte     1/3 (33%)   ← Common-Core only
    # With threshold 0.50: absender + empfaenger + kategorie qualify.
    cli.main(
        [
            "--processes-root", str(processes_root),
            "--output", str(output),
            "--common-core-threshold", "0.5",
        ]
    )
    doc = _load_output(output)
    assert doc["_aggregate"]["event_role_combo_count"] == 3
    assert doc["_aggregate"]["common_core_transaktionsdaten"] == [
        "absender",
        "empfaenger",
        "kategorie",
    ]


def test_cli_output_is_deterministic_across_runs(tmp_path: Path) -> None:
    processes_root = _populate_corpus(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert cli.main(["--processes-root", str(processes_root), "--output", str(first)]) == cli.EXIT_OK
    assert cli.main(["--processes-root", str(processes_root), "--output", str(second)]) == cli.EXIT_OK

    assert first.read_bytes() == second.read_bytes()


def test_cli_filter_role_drops_other_roles(tmp_path: Path) -> None:
    _populate_corpus(tmp_path, role="lf")
    _populate_corpus(tmp_path, role="nb")
    output = tmp_path / "out.json"

    cli.main(
        [
            "--processes-root", str(tmp_path),
            "--filter-role", "nb",
            "--output", str(output),
        ]
    )

    events = _load_output(output)["events"]
    assert list(events["202604"]) == ["NB"]


def test_cli_filter_format_drops_other_formats(tmp_path: Path) -> None:
    _populate_corpus(tmp_path, format_version="202604")
    _populate_corpus(tmp_path, format_version="202610")
    output = tmp_path / "out.json"

    cli.main(
        [
            "--processes-root", str(tmp_path),
            "--filter-format", "202610",
            "--output", str(output),
        ]
    )

    events = _load_output(output)["events"]
    assert list(events) == ["202610"]


def test_cli_exits_with_error_when_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    output = tmp_path / "out.json"

    assert cli.main(
        ["--processes-root", str(missing), "--output", str(output)]
    ) == cli.EXIT_ERROR
    assert not output.exists()


def test_cli_exits_with_no_files_when_corpus_empty(tmp_path: Path) -> None:
    (tmp_path / "maco-lf-processes" / "202604").mkdir(parents=True)
    output = tmp_path / "out.json"

    assert cli.main(
        ["--processes-root", str(tmp_path), "--output", str(output)]
    ) == cli.EXIT_NO_FILES
    assert not output.exists()
