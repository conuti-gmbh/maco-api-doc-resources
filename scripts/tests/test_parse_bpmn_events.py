"""Tests for parse_bpmn_events.py and scripts.bpmn.parser."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts import parse_bpmn_events as cli
from scripts.bpmn.parser import parse_bpmn_xml


FIXTURES = Path(__file__).parent / "fixtures" / "bpmn"


# ----------------------------- parser unit tests -----------------------------


def test_parse_simple_extracts_topic_and_pruefis_with_empty_paths() -> None:
    entry = parse_bpmn_xml(
        (FIXTURES / "T_MINI_SIMPLE.bpmn").read_bytes(),
        "maco-lf-processes/202604/T_PROZESSE/T_MINI_SIMPLE.bpmn",
    )
    assert entry is not None
    assert entry.process_id == "LF-202604-T_MINI_SIMPLE"
    assert entry.topic == "MINI_SIMPLE"
    assert entry.name_raw.startswith("MINI_SIMPLE:")
    assert [p.id for p in entry.pruefis] == [11111, 22222]
    # No gateways → single empty AND-path each.
    for p in entry.pruefis:
        assert p.paths == ((),)


def test_parse_branched_extracts_and_conjunctive_condition_paths() -> None:
    entry = parse_bpmn_xml(
        (FIXTURES / "T_MINI_BRANCHED.bpmn").read_bytes(),
        "maco-lf-processes/202604/T_PROZESSE/T_MINI_BRANCHED.bpmn",
    )
    assert entry is not None
    by_id = {p.id: p for p in entry.pruefis}

    assert by_id[44001].paths == ((
        '${sparte=="GAS"}',
    ),)
    assert by_id[55001].paths == ((
        '${sparte=="STROM"}',
        '${energierichtung=="AUSSP"}',
    ),)
    assert by_id[55077].paths == ((
        '${sparte=="STROM"}',
        '${energierichtung=="EINSP"}',
    ),)


def test_parse_returns_none_on_xml_without_process(tmp_path: Path) -> None:
    no_process = tmp_path / "T_EMPTY.bpmn"
    no_process.write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"/>'
    )
    assert parse_bpmn_xml(no_process.read_bytes(), "irrelevant") is None


def test_topic_is_token_before_colon() -> None:
    entry = parse_bpmn_xml(
        (FIXTURES / "T_MINI_SIMPLE.bpmn").read_bytes(),
        "irrelevant",
    )
    assert entry is not None
    # name_raw = "MINI_SIMPLE: Test process with no branching"
    # topic = token before colon
    assert entry.topic == "MINI_SIMPLE"
    assert ":" in entry.name_raw


# ----------------------------- CLI integration tests -----------------------------


def _populate_corpus(
    root: Path,
    *,
    role: str = "lf",
    format_version: str = "202604",
    fixtures: list[str] | None = None,
) -> Path:
    """Build a maco-<role>-processes/<format>/T_PROZESSE/ layout under root."""
    fixtures = fixtures or ["T_MINI_SIMPLE.bpmn", "T_MINI_BRANCHED.bpmn"]
    target = root / f"maco-{role}-processes" / format_version / "T_PROZESSE"
    target.mkdir(parents=True)
    for name in fixtures:
        shutil.copy(FIXTURES / name, target / name)
    return root


def _load_output(out_path: Path) -> dict:
    return json.loads(out_path.read_text(encoding="utf-8"))


def test_cli_emits_event_mapping_with_expected_structure(tmp_path: Path) -> None:
    processes_root = _populate_corpus(tmp_path)
    output = tmp_path / "event-mapping.json"

    exit_code = cli.main(
        ["--processes-root", str(processes_root), "--output", str(output)]
    )

    assert exit_code == cli.EXIT_OK
    document = _load_output(output)
    assert set(document.keys()) == {"_provenance", "events"}
    events = document["events"]
    assert list(events.keys()) == ["202604"]
    assert list(events["202604"].keys()) == ["LF"]
    topics = events["202604"]["LF"]
    assert set(topics.keys()) == {"MINI_SIMPLE", "MINI_BRANCHED"}
    branched = topics["MINI_BRANCHED"]
    assert branched["process_id"] == "LF-202604-T_MINI_BRANCHED"
    assert branched["source"].endswith("T_MINI_BRANCHED.bpmn")
    by_id = {p["id"]: p for p in branched["pruefis"]}
    assert by_id[44001]["paths"] == [['${sparte=="GAS"}']]
    assert by_id[55001]["paths"] == [[
        '${sparte=="STROM"}',
        '${energierichtung=="AUSSP"}',
    ]]


def test_cli_output_is_deterministic_across_runs(tmp_path: Path) -> None:
    processes_root = _populate_corpus(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert cli.main(["--processes-root", str(processes_root), "--output", str(first)]) == cli.EXIT_OK
    assert cli.main(["--processes-root", str(processes_root), "--output", str(second)]) == cli.EXIT_OK

    assert first.read_bytes() == second.read_bytes()


def test_cli_filter_format_drops_other_formats(tmp_path: Path) -> None:
    _populate_corpus(tmp_path, format_version="202604")
    _populate_corpus(tmp_path, format_version="202610", fixtures=["T_MINI_SIMPLE.bpmn"])
    output = tmp_path / "event-mapping.json"

    exit_code = cli.main(
        [
            "--processes-root", str(tmp_path),
            "--filter-format", "202610",
            "--output", str(output),
        ]
    )

    assert exit_code == cli.EXIT_OK
    events = _load_output(output)["events"]
    assert list(events.keys()) == ["202610"]


def test_cli_filter_role_drops_other_roles(tmp_path: Path) -> None:
    _populate_corpus(tmp_path, role="lf")
    _populate_corpus(tmp_path, role="nb", fixtures=["T_MINI_SIMPLE.bpmn"])
    output = tmp_path / "event-mapping.json"

    exit_code = cli.main(
        [
            "--processes-root", str(tmp_path),
            "--filter-role", "nb",
            "--output", str(output),
        ]
    )

    assert exit_code == cli.EXIT_OK
    events = _load_output(output)["events"]
    assert list(events["202604"].keys()) == ["NB"]


def test_cli_exits_with_error_when_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    output = tmp_path / "event-mapping.json"

    exit_code = cli.main(
        ["--processes-root", str(missing), "--output", str(output)]
    )

    assert exit_code == cli.EXIT_ERROR
    assert not output.exists()


def test_cli_exits_with_no_files_when_corpus_empty(tmp_path: Path) -> None:
    # Empty repo dir with no T_PROZESSE/ inside
    (tmp_path / "maco-lf-processes" / "202604").mkdir(parents=True)
    output = tmp_path / "event-mapping.json"

    exit_code = cli.main(
        ["--processes-root", str(tmp_path), "--output", str(output)]
    )

    assert exit_code == cli.EXIT_NO_FILES
    assert not output.exists()


def test_cli_warns_and_skips_duplicate_topic(tmp_path: Path, capsys) -> None:
    target = tmp_path / "maco-lf-processes" / "202604" / "T_PROZESSE"
    target.mkdir(parents=True)
    # Two distinct files with the *same* topic name → second should warn + skip.
    shutil.copy(FIXTURES / "T_MINI_SIMPLE.bpmn", target / "T_MINI_SIMPLE.bpmn")
    shutil.copy(
        FIXTURES / "T_MINI_SIMPLE.bpmn", target / "T_MINI_SIMPLE_DUPLICATE.bpmn"
    )
    output = tmp_path / "event-mapping.json"

    exit_code = cli.main(
        ["--processes-root", str(tmp_path), "--output", str(output)]
    )

    assert exit_code == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "duplicate topic" in captured.err
