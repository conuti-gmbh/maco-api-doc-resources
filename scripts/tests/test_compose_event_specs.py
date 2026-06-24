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
    td_reads: dict[str, list[str]] | None = None,
) -> dict:
    events: dict = {}
    if include_event:
        events = {
            fmt: {
                role: {
                    topic: {
                        "required_transaktionsdaten": required_td,
                        "transaktionsdaten_reads": td_reads or {},
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


def _write_td_atom(bo4e: Path, field: str, *, bo: str | None = None) -> None:
    """Write bo4e/fields/cdoc/Transaktionsdaten/<field>.yaml.

    With ``bo`` (e.g. "bo/Marktteilnehmer") the atom is a $ref to that BO so the
    resolver can locate sub-field atoms; otherwise a scalar leaf.
    """
    path = bo4e / "fields" / "cdoc" / "Transaktionsdaten" / f"{field}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if bo:
        leaf = bo.split("/")[-1]
        body = f"      $ref: ../../../{bo}.yaml#/components/schemas/{leaf}"
    else:
        body = "      type: string"
    path.write_text(
        f"components:\n  schemas:\n    {field}:\n{body}\n", encoding="utf-8"
    )


def _write_bo_subfield(bo4e: Path, tier: str, bo: str, seg: str) -> None:
    path = bo4e / "fields" / tier / bo / f"{seg}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"components:\n  schemas:\n    {seg}:\n      type: string\n", encoding="utf-8"
    )


def _provision_atoms(bo4e: Path, required: dict, skip: set[str]) -> None:
    """Create the cdoc/Transaktionsdaten field atoms the required doc references.

    Nested-read fields get a $ref to bo/Marktteilnehmer plus the read sub-field
    atoms (matches the real model: absender/empfaenger → Marktteilnehmer). Fields
    in ``skip`` are intentionally left without an atom (unresolved-case test).
    """
    done: set[str] = set()

    def provide(field: str, reads: dict[str, list[str]]) -> None:
        if field in skip or field in done:
            return
        done.add(field)
        segs = reads.get(field)
        if segs:
            _write_td_atom(bo4e, field, bo="bo/Marktteilnehmer")
            for seg in segs:
                _write_bo_subfield(bo4e, "bo", "Marktteilnehmer", seg)
        else:
            _write_td_atom(bo4e, field)

    for by_role in required.get("events", {}).values():
        for by_event in by_role.values():
            for entry in by_event.values():
                reads = entry.get("transaktionsdaten_reads", {})
                for field in entry.get("required_transaktionsdaten", []):
                    provide(field, reads)
    for field in required.get("_aggregate", {}).get(
        "common_core_transaktionsdaten", []
    ):
        provide(field, {})


def _run(tmp_path: Path, mapping: dict, required: dict, **kw) -> tuple[int, int, list[str]]:
    bo4e = tmp_path / "bo4e"
    _provision_atoms(bo4e, required, kw.get("skip_atoms", set()))
    return comp.compose(
        bauteil_dir=tmp_path / "event-bauteil",
        mapping=mapping,
        required_doc=required,
        target=tmp_path / "event",
        bo4e_dir=bo4e,
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
    # Variante B: single object, no allOf — only the used fields as visible props.
    assert "allOf" not in td
    assert td["type"] == "object"
    assert td["required"] == ["absender", "empfaenger", "sparte", "transaktionsgrund"]
    assert "pruefidentifikator" not in td["required"]
    # Scalar reads → $ref to the cdoc/Transaktionsdaten field atom.
    assert td["properties"]["sparte"]["$ref"] == (
        "../../bo4e/fields/cdoc/Transaktionsdaten/sparte.yaml#/components/schemas/sparte"
    )
    pruefi = td["properties"]["pruefidentifikator"]
    assert "enum" not in pruefi
    assert pruefi["examples"] == ["44001", "55001"]
    assert "dynamisch" in pruefi["description"]
    assert "44001, 55001" in pruefi["description"]

    zus = schema["properties"]["zusatzdaten"]
    assert zus["properties"]["eventname"]["const"] == "START_LIEFERBEGINN"
    assert zus["properties"]["eventname"]["default"] == "START_LIEFERBEGINN"

    one_of = schema["properties"]["stammdaten"]["oneOf"]
    assert [r["$ref"] for r in one_of] == [
        "../../event-bauteil/202604/UTILMD_GAS/PI_44001.yaml#/components/schemas/PI_44001__stammdaten",
        "../../event-bauteil/202604/UTILMD/PI_55001.yaml#/components/schemas/PI_55001__stammdaten",
    ]

    info = doc["info"]
    assert info["title"] == "[LF] START_LIEFERBEGINN"
    assert info["version"] == "202604"
    assert info["x-bpmn-source-sha"] == "deadbeef"
    assert info["x-bo4e-schema-version"] == "1.6.5"
    assert info["x-templater-sha"] == "cafe1234"


# ----------------------------- GAS suffix heuristic -----------------------------


def test_duplicate_pruefi_id_is_deduplicated(tmp_path: Path) -> None:
    """Same pruefi on multiple tasks/paths → one oneOf ref, one pending entry."""
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    # 55001 twice (resolved), 55077 twice (pending).
    mapping = _mapping("LF", "TOPIC", [55001, 55001, 55077, 55077])
    required = _required("LF", "TOPIC", ["absender"])
    _run(tmp_path, mapping, required)

    schema = _load_out(tmp_path, "202604", "LF", "TOPIC")["components"]["schemas"][
        "[LF] TOPIC"
    ]
    one_of = schema["properties"]["stammdaten"]["oneOf"]
    assert [r["$ref"] for r in one_of] == [
        "../../event-bauteil/202604/UTILMD/PI_55001.yaml#/components/schemas/PI_55001__stammdaten"
    ]
    assert schema["x-pending-pruefis"] == ["55077"]  # deduped


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
    td = doc["components"]["schemas"]["[LF] START_VERSAND_ANTWORT_NNA"][
        "properties"
    ]["transaktionsdaten"]
    assert "allOf" not in td
    assert "pruefidentifikator" in td["required"]
    pruefi = td["properties"]["pruefidentifikator"]
    assert pruefi["enum"] == ["33001", "33002", "33003", "33004"]
    assert "examples" not in pruefi
    assert "Pflichtfeld" in pruefi["description"]


# ----------------------------- Variante B: nested + unresolved -----------------------------


def test_nested_read_becomes_focused_subobject(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    mapping = _mapping("LF", "TOPIC", [55001])
    required = _required(
        "LF",
        "TOPIC",
        ["absender", "sparte"],
        td_reads={"absender": ["rollencodenummer", "ansprechpartner"]},
    )
    seen, written, warnings = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)
    assert warnings == []

    td = _load_out(tmp_path, "202604", "LF", "TOPIC")["components"]["schemas"][
        "[LF] TOPIC"
    ]["properties"]["transaktionsdaten"]
    # nested field → focused object over only the read sub-fields, as bo atoms
    absender = td["properties"]["absender"]
    assert absender["type"] == "object"
    assert set(absender["properties"]) == {"rollencodenummer", "ansprechpartner"}
    assert absender["properties"]["rollencodenummer"]["$ref"] == (
        "../../bo4e/fields/bo/Marktteilnehmer/rollencodenummer.yaml"
        "#/components/schemas/rollencodenummer"
    )
    # scalar field stays a whole-field $ref to the cdoc atom
    assert td["properties"]["sparte"]["$ref"].endswith(
        "cdoc/Transaktionsdaten/sparte.yaml#/components/schemas/sparte"
    )
    assert td["required"] == ["absender", "sparte"]


def test_field_without_atom_goes_to_x_unresolved(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    mapping = _mapping("NB", "START_BERECHNUNGSFORMEL", [55001])
    # lokationsTyp is read by the DMN but has no cdoc/Transaktionsdaten atom.
    required = _required("NB", "START_BERECHNUNGSFORMEL", ["absender", "lokationsTyp"])
    seen, written, warnings = _run(
        tmp_path, mapping, required, skip_atoms={"lokationsTyp"}
    )
    assert (seen, written) == (1, 1)

    td = _load_out(tmp_path, "202604", "NB", "START_BERECHNUNGSFORMEL")["components"][
        "schemas"
    ]["[NB] START_BERECHNUNGSFORMEL"]["properties"]["transaktionsdaten"]
    assert "lokationsTyp" not in td["properties"]
    assert "lokationsTyp" not in td["required"]
    assert td["required"] == ["absender"]
    assert td["x-unresolved-transaktionsdaten"] == ["lokationsTyp"]
    assert any("lokationsTyp" in w for w in warnings)


def test_field_with_miscased_atom_schema_is_unresolved(tmp_path: Path) -> None:
    # DMN reads 'angebotsReferenz' (capital R) but the atom defines the schema
    # 'angebotsreferenz' (lowercase). On a case-insensitive FS the file "exists",
    # so resolution must hinge on the exact-case schema name, not file presence.
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    bo4e = tmp_path / "bo4e"
    _write_td_atom(bo4e, "absender")  # resolves normally
    miscased = bo4e / "fields" / "cdoc" / "Transaktionsdaten" / "angebotsReferenz.yaml"
    miscased.parent.mkdir(parents=True, exist_ok=True)
    miscased.write_text(
        "components:\n  schemas:\n    angebotsreferenz:\n      type: string\n",
        encoding="utf-8",
    )

    mapping = _mapping("NB", "START_REKLAMATION_WERTE", [55001])
    required = _required("NB", "START_REKLAMATION_WERTE", ["absender", "angebotsReferenz"])
    seen, written, warnings = comp.compose(
        bauteil_dir=bauteil, mapping=mapping, required_doc=required,
        target=tmp_path / "event", bo4e_dir=bo4e, filter_format=None,
        filter_role=None, filter_topic=None, verbose=False,
    )
    assert (seen, written) == (1, 1)

    td = _load_out(tmp_path, "202604", "NB", "START_REKLAMATION_WERTE")["components"][
        "schemas"
    ]["[NB] START_REKLAMATION_WERTE"]["properties"]["transaktionsdaten"]
    assert "angebotsReferenz" not in td["properties"]
    assert td["required"] == ["absender"]
    assert td["x-unresolved-transaktionsdaten"] == ["angebotsReferenz"]


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

    td = _load_out(tmp_path, "202604", "LF", "SOME_TOPIC")["components"][
        "schemas"
    ]["[LF] SOME_TOPIC"]["properties"]["transaktionsdaten"]
    assert "allOf" not in td
    assert td["required"] == ["absender", "empfaenger"]


# ----------------------------- missing bauteile -----------------------------


def test_missing_bauteil_becomes_pending(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    # 55077 has no bauteil → recorded as pending, not in oneOf.

    mapping = _mapping("LF", "TOPIC", [55001, 55077])
    required = _required("LF", "TOPIC", ["absender"])
    seen, written, _ = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)

    schema = _load_out(tmp_path, "202604", "LF", "TOPIC")["components"]["schemas"][
        "[LF] TOPIC"
    ]
    assert schema["x-pending-pruefis"] == ["55077"]
    one_of = schema["properties"]["stammdaten"]["oneOf"]
    assert [r["$ref"] for r in one_of] == [
        "../../event-bauteil/202604/UTILMD/PI_55001.yaml#/components/schemas/PI_55001__stammdaten"
    ]
    # The pending pruefi still appears in the Beauskunftung (full topic pool).
    pruefi = schema["properties"]["transaktionsdaten"]["properties"][
        "pruefidentifikator"
    ]
    assert pruefi["examples"] == ["55001", "55077"]


def test_all_missing_emits_stub(tmp_path: Path) -> None:
    # Format 202604 is in the snapshot (dir exists) but the pruefi has no
    # bauteil → stub: envelope + x-pending, no oneOf body validation.
    (tmp_path / "event-bauteil" / "202604" / "UTILMD").mkdir(parents=True)
    mapping = _mapping("LF", "TOPIC", [99999])
    required = _required("LF", "TOPIC", ["absender"])
    seen, written, _ = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)

    schema = _load_out(tmp_path, "202604", "LF", "TOPIC")["components"]["schemas"][
        "[LF] TOPIC"
    ]
    assert schema["x-pending-pruefis"] == ["99999"]
    assert "allOf" not in schema  # stub: no oneOf
    assert schema["required"] == ["stammdaten", "transaktionsdaten", "zusatzdaten"]
    assert (
        schema["properties"]["zusatzdaten"]["properties"]["eventname"]["const"]
        == "TOPIC"
    )


def test_uncovered_wip_format_emits_stub(tmp_path: Path) -> None:
    # event-bauteil only has 202604. A 202610 event (WIP FUM, Templater pruefis
    # not produced yet) still gets a spec: a stub listing all its pruefis as
    # pending — no format-level skip.
    _write_bauteil(tmp_path / "event-bauteil", "202604", "UTILMD", 55001)
    mapping = _mapping("LF", "TOPIC", [55001, 44001], fmt="202610")
    required = _required("LF", "TOPIC", ["absender"], fmt="202610")
    seen, written, _ = _run(tmp_path, mapping, required)
    assert (seen, written) == (1, 1)

    schema = _load_out(tmp_path, "202610", "LF", "TOPIC")["components"]["schemas"][
        "[LF] TOPIC"
    ]
    assert schema["x-pending-pruefis"] == ["44001", "55001"]  # all pruefis TBD
    assert "allOf" not in schema  # stub — no oneOf yet
    assert (
        schema["properties"]["zusatzdaten"]["properties"]["eventname"]["const"]
        == "TOPIC"
    )


# ----------------------------- determinism -----------------------------


def test_output_is_deterministic(tmp_path: Path) -> None:
    bauteil = tmp_path / "event-bauteil"
    _write_bauteil(bauteil, "202604", "UTILMD", 55001)
    _write_bauteil(bauteil, "202604", "UTILMD_GAS", 44001)
    mapping = _mapping("LF", "START_LIEFERBEGINN", [55001, 44001])
    required = _required("LF", "START_LIEFERBEGINN", ["absender", "sparte"])
    bo4e = tmp_path / "bo4e"
    _provision_atoms(bo4e, required, set())

    comp.compose(
        bauteil_dir=bauteil, mapping=mapping, required_doc=required,
        target=tmp_path / "out_a", bo4e_dir=bo4e, filter_format=None,
        filter_role=None, filter_topic=None, verbose=False,
    )
    comp.compose(
        bauteil_dir=bauteil, mapping=mapping, required_doc=required,
        target=tmp_path / "out_b", bo4e_dir=bo4e, filter_format=None,
        filter_role=None, filter_topic=None, verbose=False,
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


def test_en_dirs_emit_en_refs(tmp_path: Path) -> None:
    """EN run (--bauteil-dir/--bo4e-dir with -en names) must emit $refs into the
    EN trees, not the DE defaults. Regression: event-en specs pointed at the DE
    event-bauteil/ + bo4e/ (CI: 19 broken refs)."""
    bauteil_en = tmp_path / "event-bauteil-en"
    _write_bauteil(bauteil_en, "202604", "UTILMD", 55001)
    bo4e_en = tmp_path / "bo4e-en"
    mapping = _mapping("LF", "START_LIEFERBEGINN", [55001])
    required = _required("LF", "START_LIEFERBEGINN", ["sparte"])
    _provision_atoms(bo4e_en, required, set())

    seen, written, warnings = comp.compose(
        bauteil_dir=bauteil_en,
        mapping=mapping,
        required_doc=required,
        target=tmp_path / "event-en",
        bo4e_dir=bo4e_en,
        filter_format=None,
        filter_role=None,
        filter_topic=None,
        verbose=False,
    )
    assert (seen, written) == (1, 1)

    path = tmp_path / "event-en" / "202604" / "[LF]_START_LIEFERBEGINN.yaml"
    schema = _yaml().load(path.read_text(encoding="utf-8"))["components"]["schemas"][
        "[LF] START_LIEFERBEGINN"
    ]
    # stammdaten oneOf -> event-bauteil-en/ (not event-bauteil/)
    assert schema["properties"]["stammdaten"]["oneOf"][0]["$ref"] == (
        "../../event-bauteil-en/202604/UTILMD/PI_55001.yaml"
        "#/components/schemas/PI_55001__stammdaten"
    )
    # transaktionsdaten field -> bo4e-en/ (not bo4e/)
    assert schema["properties"]["transaktionsdaten"]["properties"]["sparte"]["$ref"] == (
        "../../bo4e-en/fields/cdoc/Transaktionsdaten/sparte.yaml#/components/schemas/sparte"
    )
