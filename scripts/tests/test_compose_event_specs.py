"""Tests for compose_event_specs.py (Skript 3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

# Import the script as a module (matches the style of the other Skript tests).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compose_event_specs as comp  # noqa: E402


def _yaml() -> YAML:
    return YAML(typ="rt")


def _write_bauteil(root: Path, fmt: str, scope: str, pid: int) -> None:
    """Write a minimal but structurally valid event-bauteil PI spec."""
    path = root / fmt / scope / f"PI_{pid}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""openapi: 3.1.0
info:
  title: PI_{pid}
  version: '{fmt}'
  x-bo4e-schema-version: 1.6.5
  x-templater-sha: cafe1234
components:
  schemas:
    PI_{pid}:
      type: object
      properties:
        stammdaten:
          $ref: '#/components/schemas/PI_{pid}__stammdaten'
      required:
        - stammdaten
    PI_{pid}__stammdaten:
      type: object
""",
        encoding="utf-8",
    )


def _mapping(role: str, topic: str, pruefi_ids: list[int], fmt: str = "202604") -> dict:
    return {
        "_provenance": {f"maco-{role.lower()}-processes": "deadbeef"},
        "events": {
            fmt: {
                role: {
                    topic: {
                        "process_id": f"{role}-{fmt}-T_{topic}",
                        "process_name_raw": f"{topic}: test",
                        "source": f"maco-{role.lower()}-processes/{fmt}/T_PROZESSE/T_{topic}.bpmn",
                        "pruefis": [{"id": pid, "paths": [[]]} for pid in pruefi_ids],
                    }
                }
            }
        },
    }


def _required(
    role: str,
    topic: str,
    required_td: list[str],
    pruefi_source: str | None = None,
    fmt: str = "202604",
    *,
    common_core: list[str] | None = None,
    include_event: bool = True,
) -> dict:
    events: dict = {}
    if include_event:
        events = {
            fmt: {
                role: {
                    topic: {
                        "required_transaktionsdaten": required_td,
                        "required_zusatzdaten": ["erpEvent"],
                        "stammdaten_reads": ["MARKTLOKATION"],
                        "pruefidentifikator_source": pruefi_source,
                        "description": None,
                        "jsonpaths": {},
                    }
                }
            }
        }
    return {
        "_provenance": {f"maco-{role.lower()}-processes": "deadbeef"},
        "_aggregate": {
            "common_core_transaktionsdaten": common_core
            or ["absender", "empfaenger", "sparte"],
        },
        "events": events,
    }


def _run(tmp_path: Path, mapping: dict, required: dict, **kw) -> tuple[int, int, list[str]]:
    return comp.compose(
        bauteil_dir=tmp_path / "event-bauteil",
        mapping=mapping,
        required_doc=required,
        target=tmp_path / "event",
        filter_format=kw.get("filter_format"),
        filter_role=kw.get("filter_role"),
        filter_topic=kw.get("filter_topic"),
        verbose=kw.get("verbose", False),
    )


def _load_out(tmp_path: Path, fmt: str, role: str, topic: str) -> dict:
    path = tmp_path / "event" / fmt / f"[{role}]_{topic}.yaml"
    with path.open("r", encoding="utf-8") as fh:
        return _yaml().load(fh)


# ----------------------------- agnostic multi-scope -----------------------------


def test_agnostic_event_optional_pruefi_with_description_and_examples(
    tmp_path: Path,
) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    _write_bauteil(bauteil, "202604", "UTILMD_GAS", 44001)

    mapping = _mapping("LF", "START_LIEFERBEGINN", [55001, 44001])
    required = _required(
        "LF", "START_LIEFERBEGINN", ["absender", "empfaenger", "sparte", "transaktionsgrund"]
    )
    seen, written, warnings = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)
    assert warnings == []

    doc = _load_out(tmp_path, "202604", "LF", "START_LIEFERBEGINN")
    schemas = doc["components"]["schemas"]
    # No GAS suffix because the pool mixes 55xxx + 44xxx.
    assert "[LF] START_LIEFERBEGINN" in schemas
    schema = schemas["[LF] START_LIEFERBEGINN"]

    assert schema["required"] == ["stammdaten", "transaktionsdaten", "zusatzdaten"]

    td = schema["properties"]["transaktionsdaten"]
    assert td["allOf"][0]["$ref"] == comp.TRANSAKTIONSDATEN_REF
    override = td["allOf"][1]
    assert override["required"] == ["absender", "empfaenger", "sparte", "transaktionsgrund"]
    assert "pruefidentifikator" not in override["required"]
    pruefi = override["properties"]["pruefidentifikator"]
    assert "enum" not in pruefi
    assert pruefi["examples"] == ["44001", "55001"]
    assert "dynamisch" in pruefi["description"]
    assert "44001, 55001" in pruefi["description"]

    zus = schema["properties"]["zusatzdaten"]
    assert zus["properties"]["eventname"]["const"] == "START_LIEFERBEGINN"
    assert zus["properties"]["eventname"]["default"] == "START_LIEFERBEGINN"

    one_of = schema["allOf"][0]["oneOf"]
    assert [r["$ref"] for r in one_of] == [
        "../../event-bauteil/202604/UTILMD_GAS/PI_44001.yaml#/components/schemas/PI_44001",
        "../../event-bauteil/202604/UTILMD/PI_55001.yaml#/components/schemas/PI_55001",
    ]

    info = doc["info"]
    assert info["title"] == "[LF] START_LIEFERBEGINN"
    assert info["version"] == "202604"
    assert info["x-bpmn-source-sha"] == "deadbeef"
    assert info["x-bo4e-schema-version"] == "1.6.5"
    assert info["x-templater-sha"] == "cafe1234"


# ----------------------------- GAS suffix heuristic -----------------------------


def test_gas_only_pool_gets_gas_suffix(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD_GAS", 44112)
    _write_bauteil(bauteil, "202604", "UTILMD_GAS", 44001)

    mapping = _mapping("NB", "START_ANFRAGE_SD", [44112, 44001])
    required = _required("NB", "START_ANFRAGE_SD", ["absender", "empfaenger", "sparte"])
    _run(tmp_path, mapping, required)

    doc = _load_out(tmp_path, "202604", "NB", "START_ANFRAGE_SD")
    assert "[NB] START_ANFRAGE_SD GAS" in doc["components"]["schemas"]


# ----------------------------- NNA special case -----------------------------


def test_nna_pruefi_required_and_enum(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    for pid in (33001, 33002, 33003, 33004):
        _write_bauteil(bauteil, "202604", "REMADV", pid)

    mapping = _mapping("LF", "START_VERSAND_ANTWORT_NNA", [33001, 33002, 33003, 33004])
    required = _required(
        "LF",
        "START_VERSAND_ANTWORT_NNA",
        ["absender", "empfaenger", "kategorie", "sparte"],
        pruefi_source="transaktionsdaten",
    )
    _run(tmp_path, mapping, required)

    doc = _load_out(tmp_path, "202604", "LF", "START_VERSAND_ANTWORT_NNA")
    override = doc["components"]["schemas"]["[LF] START_VERSAND_ANTWORT_NNA"][
        "properties"
    ]["transaktionsdaten"]["allOf"][1]
    assert "pruefidentifikator" in override["required"]
    pruefi = override["properties"]["pruefidentifikator"]
    assert pruefi["enum"] == ["33001", "33002", "33003", "33004"]
    assert "examples" not in pruefi
    assert "Pflichtfeld" in pruefi["description"]


# ----------------------------- fallback to aggregate -----------------------------


def test_missing_required_entry_falls_back_to_common_core(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)

    mapping = _mapping("LF", "SOME_TOPIC", [55001])
    required = _required(
        "LF",
        "SOME_TOPIC",
        [],
        common_core=["absender", "empfaenger"],
        include_event=False,  # no DMN entry for this topic
    )
    seen, written, warnings = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)
    assert any("Common-Core" in w for w in warnings)

    override = _load_out(tmp_path, "202604", "LF", "SOME_TOPIC")["components"][
        "schemas"
    ]["[LF] SOME_TOPIC"]["properties"]["transaktionsdaten"]["allOf"][1]
    assert override["required"] == ["absender", "empfaenger"]


# ----------------------------- missing bauteile -----------------------------


def test_missing_bauteil_drops_pruefi_with_warning(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    # 55077 has no bauteil → dropped.

    mapping = _mapping("LF", "TOPIC", [55001, 55077])
    required = _required("LF", "TOPIC", ["absender"])
    seen, written, warnings = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)
    assert any("55077" in w for w in warnings)

    one_of = _load_out(tmp_path, "202604", "LF", "TOPIC")["components"]["schemas"][
        "[LF] TOPIC"
    ]["allOf"][0]["oneOf"]
    assert [r["$ref"] for r in one_of] == [
        "../../event-bauteil/202604/UTILMD/PI_55001.yaml#/components/schemas/PI_55001"
    ]


def test_event_skipped_when_no_bauteile_resolve(tmp_path: Path) -> None:
    (tmp_path / "event-bauteil" / "202604" / "UTILMD").mkdir(parents=True)
    mapping = _mapping("LF", "TOPIC", [99999])
    required = _required("LF", "TOPIC", ["absender"])
    seen, written, warnings = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 0)
    assert any("no resolvable bauteile" in w for w in warnings)


# ----------------------------- determinism -----------------------------


def test_output_is_deterministic(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    _write_bauteil(bauteil, "202604", "UTILMD_GAS", 44001)
    mapping = _mapping("LF", "START_LIEFERBEGINN", [55001, 44001])
    required = _required("LF", "START_LIEFERBEGINN", ["absender", "sparte"])

    comp.compose(
        bauteil_dir=bauteil, mapping=mapping, required_doc=required,
        target=tmp_path / "out_a", filter_format=None, filter_role=None,
        filter_topic=None, verbose=False,
    )
    comp.compose(
        bauteil_dir=bauteil, mapping=mapping, required_doc=required,
        target=tmp_path / "out_b", filter_format=None, filter_role=None,
        filter_topic=None, verbose=False,
    )
    a = (tmp_path / "out_a" / "202604" / "[LF]_START_LIEFERBEGINN.yaml").read_bytes()
    b = (tmp_path / "out_b" / "202604" / "[LF]_START_LIEFERBEGINN.yaml").read_bytes()
    assert a == b


# ----------------------------- filters -----------------------------


def test_filter_role_is_case_insensitive(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    mapping = _mapping("LF", "TOPIC", [55001])
    required = _required("LF", "TOPIC", ["absender"])
    # main() uppercases the role filter; compose() expects upper already.
    seen, written, _ = _run(tmp_path, mapping, required, filter_role="LF")
    assert (seen, written) == (1, 1)
    seen2, written2, _ = _run(tmp_path, mapping, required, filter_role="NB")
    assert (seen2, written2) == (0, 0)


# ----------------------------- CLI / exit codes -----------------------------


def test_main_no_input_file_returns_no_input(tmp_path: Path) -> None:
    (tmp_path / "event-bauteil").mkdir()
    rc = comp.main([
        "--bauteil-dir", str(tmp_path / "event-bauteil"),
        "--event-mapping", str(tmp_path / "missing.json"),
        "--required-fields", str(tmp_path / "missing2.json"),
        "--target", str(tmp_path / "event"),
    ])
    assert rc == comp.EXIT_NO_INPUT


def test_main_missing_bauteil_dir_returns_error(tmp_path: Path) -> None:
    rc = comp.main([
        "--bauteil-dir", str(tmp_path / "does-not-exist"),
        "--event-mapping", str(tmp_path / "m.json"),
        "--required-fields", str(tmp_path / "r.json"),
    ])
    assert rc == comp.EXIT_ERROR


def test_main_empty_events_returns_no_input(tmp_path: Path) -> None:
    (tmp_path / "event-bauteil").mkdir()
    mapping_path = tmp_path / "event-mapping.json"
    required_path = tmp_path / "event-required-fields.json"
    mapping_path.write_text(json.dumps({"_provenance": {}, "events": {}}), encoding="utf-8")
    required_path.write_text(
        json.dumps({"_aggregate": {"common_core_transaktionsdaten": []}, "events": {}}),
        encoding="utf-8",
    )
    rc = comp.main([
        "--bauteil-dir", str(tmp_path / "event-bauteil"),
        "--event-mapping", str(mapping_path),
        "--required-fields", str(required_path),
        "--target", str(tmp_path / "event"),
    ])
    assert rc == comp.EXIT_NO_INPUT


def test_main_happy_path_returns_ok(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    mapping_path = tmp_path / "event-mapping.json"
    required_path = tmp_path / "event-required-fields.json"
    mapping_path.write_text(json.dumps(_mapping("LF", "TOPIC", [55001])), encoding="utf-8")
    required_path.write_text(
        json.dumps(_required("LF", "TOPIC", ["absender"])), encoding="utf-8"
    )
    rc = comp.main([
        "--bauteil-dir", str(bauteil),
        "--event-mapping", str(mapping_path),
        "--required-fields", str(required_path),
        "--target", str(tmp_path / "event"),
    ])
    assert rc == comp.EXIT_OK
    assert (tmp_path / "event" / "202604" / "[LF]_TOPIC.yaml").is_file()
