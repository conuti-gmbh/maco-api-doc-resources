#!/usr/bin/env python3
"""Verify that no pruefi-routing obligation is lost between BPMN and event spec.

Two levels, deliberately separated because the process repos are worked on
continuously and a doc run must not fail on unfinished process work:

**Hard (exit 1) — the invariant.** Every Camunda variable a T_ process gates a
pruefi send on must leave one of four traces in the generated spec: required in
its ``oneOf`` branch, ``x-pending-routing``, ``x-unresolved-routing``, or
already required inside ``transaktionsdaten``. A variable with no trace at all
means the generator dropped it — that is a code defect, never a data state, so
failing the run is safe even against WIP process repos.

**Soft (never fails) — coverage.** Counts how much of the routing is actually
backed by a required field versus recorded as pending or unresolved. This is
where unfinished process work shows up; it is reported so the trend is visible
across runs, and it never blocks a sync.

Inputs are the artefacts the sync produces anyway: ``event-mapping.json``
(gate conditions per pruefi), ``event-required-fields.json`` (variable →
payload path) and the generated ``event/`` tree.

Coverage via transaktionsdaten is decided on the resolved path, not the
variable name: ``marktrolle`` reads ``$.transaktionsdaten.absender.marktrolle``
and is covered by ``absender`` being required — the names differ, and comparing
them would report a defect where there is none.

Usage:
    python3 scripts/check_routing.py --event-mapping event-mapping.json \
        --required-fields event-required-fields.json \
        --event-dir event [--filter-format 202610] [-v]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from ruamel.yaml import YAML

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_NO_INPUT = 2

CONDITION_VAR_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9_]*)\s*(?:==|!=)")
TRANSAKTIONSDATEN_PREFIX = "$.transaktionsdaten."


def covered_by_transaktionsdaten(paths, required_fields) -> bool:
    """Mirrors compose_event_specs.covered_by_transaktionsdaten."""
    required = set(required_fields or [])
    for path in paths or []:
        if not path.startswith(TRANSAKTIONSDATEN_PREFIX):
            continue
        top = path[len(TRANSAKTIONSDATEN_PREFIX):].split(".", 1)[0].split("[", 1)[0]
        if top in required:
            return True
    return False


def load_yaml(path: Path, yaml: YAML) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.load(fh) or {}


def gate_variables(entry: dict) -> dict[str, set[str]]:
    """{variable: pruefi ids} from a topic's BPMN condition paths."""
    out: dict[str, set[str]] = defaultdict(set)
    for pruefi in entry.get("pruefis", []):
        for path in pruefi.get("paths", []):
            for condition in path:
                for var in CONDITION_VAR_RE.findall(condition):
                    out[var].add(str(pruefi["id"]))
    return out


def traces_in_spec(schema: dict) -> tuple[set[str], set[str], set[str], set[str]]:
    """(required, pending, unresolved, transaktionsdaten-required) variable names.

    ``required`` is read back from the x-process-routing annotation rather than
    from the required lists: the annotation is what states *which variable* an
    obligation belongs to, and it sits next to the field it made mandatory.
    """
    required: set[str] = set()
    stammdaten = (schema.get("properties", {}) or {}).get("stammdaten", {}) or {}
    for branch in stammdaten.get("oneOf", []) or []:
        for member in (branch.get("allOf", []) or [])[1:]:
            for node in (member.get("properties", {}) or {}).values():
                items = node.get("items", {}) or {}
                for spec in (items.get("properties", {}) or {}).values():
                    for annotation in spec.get("x-process-routing", []) or []:
                        required.add(annotation["variable"])

    pending: set[str] = set()
    for record in schema.get("x-pending-routing", []) or []:
        for fields in (record.get("required", {}) or {}).values():
            pending.update(fields)

    unresolved = {
        record["variable"] for record in schema.get("x-unresolved-routing", []) or []
    }
    td = set(
        ((schema.get("properties", {}) or {}).get("transaktionsdaten", {}) or {}).get(
            "required", []
        )
        or []
    )
    return required, pending, unresolved, td


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--event-mapping", required=True, type=Path)
    ap.add_argument("--required-fields", required=True, type=Path)
    ap.add_argument("--event-dir", required=True, type=Path)
    ap.add_argument("--filter-format")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.event_mapping.is_file():
        print(f"error: {args.event_mapping} not found", file=sys.stderr)
        return EXIT_NO_INPUT
    if not args.required_fields.is_file():
        print(f"error: {args.required_fields} not found", file=sys.stderr)
        return EXIT_NO_INPUT
    if not args.event_dir.is_dir():
        print(f"error: {args.event_dir} is not a directory", file=sys.stderr)
        return EXIT_NO_INPUT

    yaml = YAML(typ="safe")
    with args.event_mapping.open("r", encoding="utf-8") as fh:
        mapping = json.load(fh)
    with args.required_fields.open("r", encoding="utf-8") as fh:
        required_doc = json.load(fh)
    common_core = (required_doc.get("_aggregate") or {}).get(
        "common_core_transaktionsdaten", []
    )
    # Same repo-wide fallback the composer applies: a DMN column is defined
    # once and reused, so an event whose own row omits a variable is not
    # thereby of unknown origin.
    known_paths: dict[str, set[str]] = defaultdict(set)
    for _fmt_events in (required_doc.get("events") or {}).values():
        for _role_events in (_fmt_events or {}).values():
            for _entry in (_role_events or {}).values():
                for _var, _paths in (_entry.get("jsonpaths") or {}).items():
                    known_paths[_var].update(_paths)

    violations: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    unresolved_vars: dict[str, set[str]] = defaultdict(set)
    topics_with_gates = 0
    missing_specs = 0

    for fmt, roles in (mapping.get("events") or {}).items():
        if args.filter_format and fmt != args.filter_format:
            continue
        for role, topics in (roles or {}).items():
            for topic, entry in (topics or {}).items():
                gates = gate_variables(entry)
                if not gates:
                    continue
                topics_with_gates += 1
                spec_path = args.event_dir / fmt / f"[{role}]_{topic}.yaml"
                if not spec_path.is_file():
                    # Not this check's business — the composer decides what to
                    # emit. Counted so a silent mismatch cannot hide here.
                    missing_specs += 1
                    continue
                document = load_yaml(spec_path, yaml)
                schemas = (document.get("components", {}) or {}).get("schemas", {}) or {}
                schema = next(iter(schemas.values()), {})
                required, pending, unresolved, _td = traces_in_spec(schema)
                dmn_entry = (
                    ((required_doc.get("events") or {}).get(fmt) or {}).get(role) or {}
                ).get(topic) or {}
                jsonpaths = dmn_entry.get("jsonpaths") or {}
                required_td = dmn_entry.get("required_transaktionsdaten") or common_core

                for var, pruefis in sorted(gates.items()):
                    if var in required:
                        counts["required"] += 1
                    elif var in pending:
                        counts["pending"] += 1
                    elif var in unresolved:
                        counts["unresolved"] += 1
                        unresolved_vars[var].add(f"[{role}] {topic}")
                    elif covered_by_transaktionsdaten(
                        jsonpaths.get(var) or sorted(known_paths.get(var, ())),
                        required_td,
                    ):
                        counts["transaktionsdaten"] += 1
                    else:
                        violations.append(
                            f"{fmt} [{role}] {topic}: '{var}' gates pruefi "
                            f"{sorted(pruefis)} but leaves no trace in the spec "
                            f"(neither required nor pending/unresolved)"
                        )
                    if args.verbose:
                        print(f"{fmt} [{role}] {topic}: {var}", file=sys.stderr)

    total = sum(counts.values())
    print(f"routing coverage: {topics_with_gates} topic(s) with gates, {total} gate(s)")
    for key in ("required", "transaktionsdaten", "pending", "unresolved"):
        print(f"  {key:20} {counts[key]}")
    if unresolved_vars:
        print("  unresolved variables:")
        for var, topics in sorted(unresolved_vars.items()):
            print(f"    {var:26} in {len(topics)} topic(s)")
    if missing_specs:
        print(f"  note: {missing_specs} topic(s) with gates had no spec file")

    if violations:
        print(f"\nrouting invariant: {len(violations)} violation(s)", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return EXIT_VIOLATION

    print("routing invariant: no violations")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
