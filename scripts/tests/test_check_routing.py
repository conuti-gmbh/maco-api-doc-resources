"""Tests for check_routing.py — the routing invariant gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_routing as cr  # noqa: E402


def _mapping(tmp_path: Path, conditions: list[str], pid: int = 55001) -> Path:
    path = tmp_path / "event-mapping.json"
    path.write_text(
        json.dumps(
            {
                "events": {
                    "202604": {
                        "LF": {
                            "START_X": {
                                "pruefis": [{"id": pid, "paths": [conditions]}]
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _required(tmp_path: Path, jsonpaths: dict, required_td: list[str]) -> Path:
    path = tmp_path / "event-required-fields.json"
    path.write_text(
        json.dumps(
            {
                "_aggregate": {"common_core_transaktionsdaten": []},
                "events": {
                    "202604": {
                        "LF": {
                            "START_X": {
                                "required_transaktionsdaten": required_td,
                                "jsonpaths": jsonpaths,
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _spec(tmp_path: Path, schema_body: str) -> Path:
    path = tmp_path / "event" / "202604" / "[LF]_START_X.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "openapi: 3.1.0\ncomponents:\n  schemas:\n    '[LF] START_X':\n" + schema_body,
        encoding="utf-8",
    )
    return tmp_path / "event"


def _run(tmp_path: Path, mapping: Path, required: Path, event_dir: Path) -> int:
    argv = [
        "check_routing.py",
        "--event-mapping", str(mapping),
        "--required-fields", str(required),
        "--event-dir", str(event_dir),
    ]
    old, sys.argv = sys.argv, argv
    try:
        return cr.main()
    finally:
        sys.argv = old


UNRESOLVED_SPEC = """      type: object
      x-unresolved-routing:
        - variable: foo
          discriminates: true
"""

BARE_SPEC = """      type: object
      properties:
        transaktionsdaten:
          type: object
          required:
            - absender
"""


def test_variable_without_any_trace_is_a_violation(tmp_path: Path) -> None:
    mapping = _mapping(tmp_path, ['${foo=="X"}'])
    required = _required(tmp_path, {"foo": ["$.zusatzdaten.erpEvent.foo"]}, ["absender"])
    event_dir = _spec(tmp_path, BARE_SPEC)
    assert _run(tmp_path, mapping, required, event_dir) == cr.EXIT_VIOLATION


def test_unresolved_annotation_satisfies_the_invariant(tmp_path: Path) -> None:
    """WIP data must not fail the run — being recorded is enough."""
    mapping = _mapping(tmp_path, ['${foo=="X"}'])
    required = _required(tmp_path, {}, ["absender"])
    event_dir = _spec(tmp_path, UNRESOLVED_SPEC)
    assert _run(tmp_path, mapping, required, event_dir) == cr.EXIT_OK


def test_nested_transaktionsdaten_path_counts_as_covered(tmp_path: Path) -> None:
    """marktrolle resolves to absender.marktrolle — the names differ, and
    comparing them would report a defect where there is none."""
    mapping = _mapping(tmp_path, ['${marktrolle=="LF"}'])
    required = _required(
        tmp_path, {"marktrolle": ["$.transaktionsdaten.absender.marktrolle"]}, ["absender"]
    )
    event_dir = _spec(tmp_path, BARE_SPEC)
    assert _run(tmp_path, mapping, required, event_dir) == cr.EXIT_OK


def test_transaktionsdaten_path_outside_required_set_is_a_violation(
    tmp_path: Path,
) -> None:
    mapping = _mapping(tmp_path, ['${freitext=="X"}'])
    required = _required(tmp_path, {"freitext": ["$.transaktionsdaten.freitext"]}, ["absender"])
    event_dir = _spec(tmp_path, BARE_SPEC)
    assert _run(tmp_path, mapping, required, event_dir) == cr.EXIT_VIOLATION


def test_missing_inputs_return_no_input(tmp_path: Path) -> None:
    mapping = _mapping(tmp_path, ['${foo=="X"}'])
    required = _required(tmp_path, {}, [])
    assert _run(tmp_path, mapping, required, tmp_path / "nope") == cr.EXIT_NO_INPUT
