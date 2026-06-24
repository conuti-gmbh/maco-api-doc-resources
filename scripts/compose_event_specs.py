#!/usr/bin/env python3
"""Compose event specs from event-bauteile + the two intermediate JSON maps.

Reads:
  * ``event-bauteil/<format>/<scope>/PI_<id>.yaml``  (Skript 1 output)
  * ``event-mapping.json``                            (Skript 2 output)
  * ``event-required-fields.json``                    (Skript 4 output)

Emits one OpenAPI 3.1 spec per (format, role, topic) at
``event/<format>/[<ROLE>]_<TOPIC>.yaml``.

Modelling (Stand 2026-05-27, MACO-13040):
  * Event-Wrapper requires stammdaten / transaktionsdaten / zusatzdaten.
  * ``transaktionsdaten`` = a single object listing only the fields the event
    actually uses (Schicht 1, DMN-derived; falls back to aggregate Common-Core).
    Scalar reads $ref the cdoc/Transaktionsdaten field atom; nested reads (e.g.
    absender.rollencodenummer) become a focused sub-object over the read sub-
    fields. This replaces the former ``allOf``(full Transaktionsdaten + required-
    override), which Apidog rendered as two unmergeable 0/1 branches. Fields the
    DMN reads but the BO4E model lacks (e.g. lokationsTyp) go to
    ``x-unresolved-transaktionsdaten`` instead of required-but-undefined.
  * ``transaktionsdaten.pruefidentifikator`` is regularly **optional, no enum**:
    Camunda determines the Prüfi dynamically (Sparte + Transaktionsgrund +
    Empfänger-Marktrolle). Beauskunftung is carried by a ``description`` listing
    the Prüfis possible in the topic plus an ``examples`` array. The single NNA
    outlier (``pruefidentifikator_source == "transaktionsdaten"``) instead marks
    it **required + enum**, because there the body value routes the T_ gateway.
  * ``oneOf`` over the topic's Prüfi-Bauteile = Union-of-Required-Coverage
    (the sender must satisfy the stammdaten any pool member needs), not a
    discriminated XOR branch. No discriminator, no x-condition, no empty ``{}``.
  * Schema name gets a trailing `` GAS`` suffix iff every Prüfi in the pool is
    in the 44xxx range.

Scope (UTILMD / UTILMD_GAS / ORDERS / …) is not carried in event-mapping.json;
it is resolved from the event-bauteil tree by Prüfi id.

Story: MACO-13040 (Skript 3 — final generator).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from ruamel.yaml import YAML


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_INPUT = 2

GAS_RANGE = range(44000, 45000)

TRANSAKTIONSDATEN_REF = (
    "../../bo4e/cdoc/Transaktionsdaten.yaml#/components/schemas/Transaktionsdaten"
)

# Variante B: transaktionsdaten shows only the fields the event actually uses
# (DMN reads), each as a $ref to its atom — not the whole CDOC object wrapped in
# allOf (which Apidog renders as unmergeable 0/1 branches). Scalar reads point at
# the cdoc/Transaktionsdaten field atom; nested reads (e.g. absender.rollencode-
# nummer) become a focused sub-object whose properties are the read sub-fields,
# resolved to the referenced BO's field atoms.
# {root} = the bo4e atom mirror dir name (bo4e for DE, bo4e-en for EN) — taken
# from --bo4e-dir so EN event specs reference bo4e-schema-en, not the DE atoms.
CDOC_TD_FIELD_REF = (
    "../../{root}/fields/cdoc/Transaktionsdaten/{field}.yaml#/components/schemas/{field}"
)
BO_SUBFIELD_REF = (
    "../../{root}/fields/{tier}/{bo}/{seg}.yaml#/components/schemas/{seg}"
)
# Matches the BO target inside a cdoc field atom's $ref, e.g.
# "../../../bo/Marktteilnehmer.yaml#/..." → ("bo", "Marktteilnehmer").
_BO_REF_RE = re.compile(r"([a-z]+)/([A-Za-z0-9_]+)\.yaml")

PRUEFI_DESC_DYNAMIC = (
    "Wird dynamisch im Event-Prozess ermittelt "
    "(Sparte + Transaktionsgrund + Empfänger-Marktrolle). "
    "Ein vom Sender mitgegebener Wert wird ignoriert. "
    "Im Topic mögliche Prüfis — {pruefis}."
)
PRUEFI_DESC_NNA = (
    "Pflichtfeld — bestimmt direkt die Antwort-Variante. "
    "Der Body-Wert routet den Versand-Prozess (T_-Gateway über ${pruefidentifikator})."
)


def make_yaml() -> YAML:
    """Configured ruamel.yaml instance with deterministic dumping behaviour.

    Matches the dumper settings used by filter_event_bauteile.py so the whole
    pipeline emits stylistically uniform YAML.
    """
    yaml = YAML(typ="rt")
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_scope_index(
    bauteil_dir: Path,
) -> tuple[dict[str, dict[int, str]], dict[str, Path], list[str]]:
    """Walk event-bauteil/<format>/<scope>/PI_<id>.yaml.

    Returns (scope_index, representative_path_per_format, warnings) where
    scope_index[format][pruefi_id] = scope. A representative bauteil path per
    format is kept for reading provenance (bo4e-schema-version, templater-sha).
    """
    scope_index: dict[str, dict[int, str]] = {}
    representative: dict[str, Path] = {}
    warnings: list[str] = []
    if not bauteil_dir.is_dir():
        return scope_index, representative, warnings

    for format_dir in sorted(bauteil_dir.iterdir()):
        if not format_dir.is_dir():
            continue
        fmt = format_dir.name
        per_format = scope_index.setdefault(fmt, {})
        for scope_dir in sorted(format_dir.iterdir()):
            if not scope_dir.is_dir():
                continue
            for spec_path in sorted(scope_dir.glob("PI_*.yaml")):
                try:
                    pid = int(spec_path.stem[len("PI_"):])
                except ValueError:
                    warnings.append(f"non-numeric bauteil name: {spec_path}")
                    continue
                if pid in per_format and per_format[pid] != scope_dir.name:
                    warnings.append(
                        f"pruefi {pid} in multiple scopes for {fmt}: "
                        f"{per_format[pid]} vs {scope_dir.name} — keeping first"
                    )
                    continue
                per_format[pid] = scope_dir.name
                representative.setdefault(fmt, spec_path)
    return scope_index, representative, warnings


def read_bauteil_provenance(path: Path, yaml: YAML) -> dict[str, str]:
    """Pull x-bo4e-schema-version / x-templater-sha from a bauteil's info block."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            spec = yaml.load(fh)
    except OSError:
        return {}
    info = spec.get("info", {}) if isinstance(spec, dict) else {}
    return {
        key: info[key]
        for key in ("x-bo4e-schema-version", "x-templater-sha")
        if key in info
    }


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def resolve_pool(
    pruefis: list[dict],
    scope_by_pid: dict[int, str],
) -> tuple[list[tuple[int, str]], list[str]]:
    """Split a topic's pruefi list into (pool, pending).

    pool = sorted (id, scope) pairs for pruefis that have an event-bauteil.
    pending = sorted string ids for pruefis whose body-validation spec is not
    in the snapshot yet (Prüfi im Templater noch nicht implementiert, oder als
    transaktionsdaten-only von Skript 1 verworfen). The caller records these in
    ``x-pending-pruefis`` so the gap is visible in the artifact and a later
    regeneration closes it automatically.
    """
    pool: list[tuple[int, str]] = []
    pending: list[str] = []
    seen_ids: set[int] = set()
    for pruefi in pruefis:
        pid = pruefi["id"]
        # A pruefi can appear on several service tasks / condition paths of the
        # same topic (distinct `paths` in event-mapping). For the oneOf / pending
        # we only care about the unique id, so de-duplicate here.
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        scope = scope_by_pid.get(pid)
        if scope is None:
            pending.append(str(pid))
            continue
        pool.append((pid, scope))
    pool.sort()
    pending.sort()
    return pool, pending


def schema_name(role: str, topic: str, pruefi_ids: list[int]) -> str:
    name = f"[{role}] {topic}"
    if pruefi_ids and all(pid in GAS_RANGE for pid in pruefi_ids):
        name += " GAS"
    return name


def _cdoc_field_atom(bo4e_dir: Path | None, field: str) -> Path | None:
    if bo4e_dir is None:
        return None
    path = bo4e_dir / "fields" / "cdoc" / "Transaktionsdaten" / f"{field}.yaml"
    return path if path.is_file() else None


def schema_names(path: Path, yaml: YAML, cache: dict[Path, set]) -> set:
    """Case-sensitive set of schema names a file defines (cached; empty if absent
    or unparseable). The filesystem may be case-insensitive (macOS), so this is
    the authoritative check for whether an atom actually defines a given name."""
    rp = path.resolve()
    if rp not in cache:
        names: set = set()
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    spec = yaml.load(fh)
                names = set((spec.get("components", {}) or {}).get("schemas", {}) or {})
            except (OSError, AttributeError):
                names = set()
        cache[rp] = names
    return cache[rp]


def cdoc_field_ref(
    bo4e_dir: Path | None, field: str, yaml: YAML, schema_cache: dict[Path, set]
) -> str | None:
    """Ref to the cdoc/Transaktionsdaten field atom IFF it defines a schema named
    exactly ``field`` (case-sensitive). Guards against DMN casing drift such as
    ``angebotsReferenz`` (atom defines ``angebotsreferenz``) which on a
    case-insensitive FS would otherwise emit a dangling pointer."""
    if bo4e_dir is None:
        return None
    atom = bo4e_dir / "fields" / "cdoc" / "Transaktionsdaten" / f"{field}.yaml"
    if field in schema_names(atom, yaml, schema_cache):
        return CDOC_TD_FIELD_REF.format(root=bo4e_dir.name, field=field)
    return None


def resolve_bo_target(
    bo4e_dir: Path | None,
    field: str,
    yaml: YAML,
    cache: dict[str, tuple[str, str] | None],
) -> tuple[str, str] | None:
    """(tier, bo) the cdoc Transaktionsdaten <field> atom $refs to, or None.

    A nested-read field (absender, empfaenger, …) is a $ref to a BO (e.g.
    bo/Marktteilnehmer). We need that BO to locate its sub-field atoms.
    """
    if field in cache:
        return cache[field]
    target: tuple[str, str] | None = None
    atom = _cdoc_field_atom(bo4e_dir, field)
    if atom is not None:
        try:
            with atom.open("r", encoding="utf-8") as fh:
                spec = yaml.load(fh)
            schemas = spec.get("components", {}).get("schemas", {})
            node = schemas.get(field) or next(iter(schemas.values()), None)
            ref = node.get("$ref") if isinstance(node, dict) else None
            if ref:
                m = _BO_REF_RE.search(ref)
                if m:
                    target = (m.group(1), m.group(2))
        except (OSError, AttributeError):
            target = None
    cache[field] = target
    return target


def build_nested_field(
    bo4e_dir: Path | None,
    field: str,
    segs: list[str],
    yaml: YAML,
    cache: dict[str, tuple[str, str] | None],
    schema_cache: dict[Path, set],
    warnings: set[str],
) -> dict | None:
    """Focused sub-object: only the DMN-read sub-fields, as $ref atoms.

    Returns None if the field's BO target cannot be resolved (caller falls back
    to the whole-field ref).
    """
    target = resolve_bo_target(bo4e_dir, field, yaml, cache)
    if target is None:
        return None
    tier, bo = target
    properties: dict = {}
    for seg in segs:
        atom = bo4e_dir / "fields" / tier / bo / f"{seg}.yaml"
        if seg in schema_names(atom, yaml, schema_cache):
            properties[seg] = {"$ref": BO_SUBFIELD_REF.format(root=bo4e_dir.name, tier=tier, bo=bo, seg=seg)}
        else:
            warnings.add(
                f"no atom for {field}.{seg} ({tier}/{bo}) — sub-field omitted"
            )
    if not properties:
        return None
    return {"type": "object", "properties": properties}


def build_transaktionsdaten(
    required_fields: list[str],
    td_reads: dict[str, list[str]],
    pruefi_source: str | None,
    all_pruefi_ids: list[int],
    bo4e_dir: Path | None,
    yaml: YAML,
    bo_cache: dict[str, tuple[str, str] | None],
    schema_cache: dict[Path, set],
    warnings: set[str],
) -> dict:
    """transaktionsdaten as a single object listing only the fields the event uses.

    Each used field (Schicht 1, DMN-derived) becomes a visible property: scalar
    reads $ref the cdoc/Transaktionsdaten field atom, nested reads become a
    focused sub-object over the read sub-fields. Fields that do not resolve to an
    atom defining a schema of exactly that name — the field is absent from the
    BO4E model (e.g. lokationsTyp) or the DMN mis-cased it (e.g. angebotsReferenz
    vs angebotsreferenz) — are surfaced in ``x-unresolved-transaktionsdaten``
    instead of emitting a dangling/required-but-undefined reference.

    The pruefidentifikator Beauskunftung lists the full topic pool (resolved +
    pending), independent of whether each Prüfi already has an event-bauteil.
    """
    properties: dict = {}
    resolved: list[str] = []
    unresolved: list[str] = []

    for field in sorted(required_fields):
        segs = td_reads.get(field, [])
        node: dict | None = None
        if segs:
            node = build_nested_field(
                bo4e_dir, field, segs, yaml, bo_cache, schema_cache, warnings
            )
            if node is None:
                # BO target unresolved → whole-field ref, but only if the atom
                # actually defines a schema named exactly <field> (case-sensitive).
                ref = cdoc_field_ref(bo4e_dir, field, yaml, schema_cache)
                if ref is not None:
                    node = {"$ref": ref}
        else:
            ref = cdoc_field_ref(bo4e_dir, field, yaml, schema_cache)
            if ref is not None:
                node = {"$ref": ref}
        if node is not None:
            properties[field] = node
            resolved.append(field)
        else:
            unresolved.append(field)

    required = set(resolved)
    pruefi_ids = [str(pid) for pid in all_pruefi_ids]
    if pruefi_source == "transaktionsdaten":
        # NNA outlier: the body value routes the gateway → required + enum.
        required.add("pruefidentifikator")
        pruefi_prop = {"enum": pruefi_ids, "description": PRUEFI_DESC_NNA}
    else:
        # Regular case: optional, no constraint; Beauskunftung via description + examples.
        pruefi_prop = {
            "description": PRUEFI_DESC_DYNAMIC.format(pruefis=", ".join(pruefi_ids)),
            "examples": pruefi_ids,
        }
    properties["pruefidentifikator"] = pruefi_prop

    td: dict = {
        "type": "object",
        "required": sorted(required),
        "properties": properties,
    }
    if unresolved:
        td["x-unresolved-transaktionsdaten"] = sorted(unresolved)
        warnings.add(
            "transaktionsdaten fields read by DMN with no matching BO4E atom "
            f"(absent from model or DMN casing drift): {sorted(unresolved)}"
        )
    return td


def build_schema(
    format_version: str,
    role: str,
    topic: str,
    pool: list[tuple[int, str]],
    pending: list[str],
    all_pruefi_ids: list[int],
    required_fields: list[str],
    td_reads: dict[str, list[str]],
    pruefi_source: str | None,
    bo4e_dir: Path | None,
    bauteil_dirname: str,
    yaml: YAML,
    bo_cache: dict[str, tuple[str, str] | None],
    schema_cache: dict[Path, set],
    warnings: set[str],
) -> dict:
    # bauteil_dirname = event-bauteil for DE, event-bauteil-en for EN — must match
    # the dir the pool was built from, else EN events $ref the DE bauteile (broken).
    one_of = [
        {
            "$ref": (
                f"../../{bauteil_dirname}/{format_version}/{scope}/"
                f"PI_{pid}.yaml#/components/schemas/PI_{pid}__stammdaten"
            )
        }
        for pid, scope in pool
    ]
    properties: dict = {}
    if one_of:
        # stammdaten as a *visible* property: oneOf over the topic's Prüfi-Bauteile
        # = Stammdaten-Union-Coverage (the sender must satisfy the stammdaten any
        # pool member needs). Surfaced here rather than as a top-level allOf[oneOf]
        # sibling so it renders in Apidog's model view; the $ref targets the named
        # PI_<id>__stammdaten sub-schema (one wrapper level less, no double nesting).
        properties["stammdaten"] = {"oneOf": one_of}
    # else: Stub — alle Prüfis pending, stammdaten bleibt required-aber-undefiniert.
    properties["transaktionsdaten"] = build_transaktionsdaten(
        required_fields, td_reads, pruefi_source, all_pruefi_ids,
        bo4e_dir, yaml, bo_cache, schema_cache, warnings,
    )
    properties["zusatzdaten"] = {
        "type": "object",
        "required": ["prozessId", "eventname"],
        "properties": {
            "prozessId": {"type": "string"},
            "eventname": {"type": "string", "const": topic, "default": topic},
        },
    }
    schema: dict = {
        "type": "object",
        "required": ["stammdaten", "transaktionsdaten", "zusatzdaten"],
        "properties": properties,
    }
    if pending:
        # Prüfis im BPMN-Pool ohne event-bauteil (Templater-Spec fehlt noch).
        # Sichtbar gemacht, damit die Teil-Abdeckung im Artefakt steht; ein
        # Re-Run nach dem Templater-Nachzug entfernt den Marker.
        schema["x-pending-pruefis"] = pending
    return schema


def build_document(
    format_version: str,
    role: str,
    topic: str,
    schema: dict,
    name: str,
    *,
    bpmn_sha: str | None,
    bauteil_provenance: dict[str, str],
) -> dict:
    info: dict[str, object] = {"title": name, "version": format_version}
    if bpmn_sha:
        info["x-bpmn-source-sha"] = bpmn_sha
    info.update(bauteil_provenance)
    return {
        "openapi": "3.1.0",
        "info": info,
        "components": {"schemas": {name: schema}},
    }


def iter_events(
    mapping: dict,
    *,
    filter_format: str | None,
    filter_role: str | None,
    filter_topic: str | None,
) -> Iterator[tuple[str, str, str, dict]]:
    """Yield (format, ROLE, topic, mapping_entry) in deterministic order."""
    events = mapping.get("events", {})
    for fmt in sorted(events):
        if filter_format and fmt != filter_format:
            continue
        for role in sorted(events[fmt]):
            if filter_role and role != filter_role:
                continue
            for topic in sorted(events[fmt][role]):
                if filter_topic and topic != filter_topic:
                    continue
                yield fmt, role, topic, events[fmt][role][topic]


def lookup_required(
    required_doc: dict, fmt: str, role: str, topic: str
) -> dict | None:
    """Find the Skript-4 entry for (format, role, eventName==topic)."""
    return (
        required_doc.get("events", {})
        .get(fmt, {})
        .get(role, {})
        .get(topic)
    )


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def compose(
    *,
    bauteil_dir: Path,
    mapping: dict,
    required_doc: dict,
    target: Path,
    bo4e_dir: Path | None,
    filter_format: str | None,
    filter_role: str | None,
    filter_topic: str | None,
    verbose: bool,
) -> tuple[int, int, list[str]]:
    """Generate every matching event spec. Returns (seen, written, warnings)."""
    yaml = make_yaml()
    scope_index, representative, warnings = build_scope_index(bauteil_dir)
    provenance_cache: dict[str, dict[str, str]] = {}
    bo_cache: dict[str, tuple[str, str] | None] = {}
    schema_cache: dict[Path, set] = {}
    td_warnings: set[str] = set()
    common_core = required_doc.get("_aggregate", {}).get(
        "common_core_transaktionsdaten", []
    )
    bpmn_provenance = mapping.get("_provenance", {})

    seen = 0
    written = 0
    stub_count = 0
    partial_count = 0
    fallback_count = 0

    for fmt, role, topic, entry in iter_events(
        mapping,
        filter_format=filter_format,
        filter_role=filter_role,
        filter_topic=filter_topic,
    ):
        seen += 1
        # No format-level skip: a WIP FUM version (e.g. 202610) whose pruefis
        # the Templater has not produced yet still gets a spec per event — a
        # stub listing all its pruefis in x-pending-pruefis. scope_by_pid is
        # then empty, so every pruefi is pending.
        scope_by_pid = scope_index.get(fmt, {})
        pruefis = entry.get("pruefis", [])
        all_ids = sorted({p["id"] for p in pruefis})
        pool, pending = resolve_pool(pruefis, scope_by_pid)

        required_entry = lookup_required(required_doc, fmt, role, topic)
        if required_entry is not None:
            required_fields = required_entry.get("required_transaktionsdaten", [])
            td_reads = required_entry.get("transaktionsdaten_reads", {})
            pruefi_source = required_entry.get("pruefidentifikator_source")
        else:
            required_fields = common_core
            td_reads = {}
            pruefi_source = None
            fallback_count += 1
            if verbose:
                print(
                    f"note: no DMN required-fields for {role}/{fmt}/{topic} — "
                    f"using aggregate Common-Core",
                    file=sys.stderr,
                )

        name = schema_name(role, topic, all_ids)
        schema = build_schema(
            fmt, role, topic, pool, pending, all_ids, required_fields,
            td_reads, pruefi_source, bo4e_dir, bauteil_dir.name, yaml, bo_cache,
            schema_cache, td_warnings,
        )

        if fmt not in provenance_cache and fmt in representative:
            provenance_cache[fmt] = read_bauteil_provenance(representative[fmt], yaml)
        document = build_document(
            fmt,
            role,
            topic,
            schema,
            name,
            bpmn_sha=bpmn_provenance.get(f"maco-{role.lower()}-processes"),
            bauteil_provenance=provenance_cache.get(fmt, {}),
        )

        out_path = target / fmt / f"[{role}]_{topic}.yaml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO()
        yaml.dump(document, buffer)
        out_path.write_text(buffer.getvalue(), encoding="utf-8")
        written += 1
        if not pool:
            stub_count += 1
        elif pending:
            partial_count += 1
        if verbose:
            kind = "stub" if not pool else ("partial" if pending else "full")
            print(
                f"wrote ({kind}): {out_path.relative_to(target.parent)}",
                file=sys.stderr,
            )

    if partial_count or stub_count:
        warnings.append(
            f"{partial_count} event(s) with pending pruefis + {stub_count} stub(s) "
            f"(all pruefis pending) — recorded in x-pending-pruefis"
        )
    if fallback_count:
        warnings.append(
            f"{fallback_count} event(s) had no DMN required-fields entry — "
            f"used aggregate Common-Core"
        )
    warnings.extend(sorted(td_warnings))
    return seen, written, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compose event/<format>/[<ROLE>]_<TOPIC>.yaml specs from "
            "event-bauteile + event-mapping.json + event-required-fields.json."
        ),
    )
    parser.add_argument(
        "--bauteil-dir",
        type=Path,
        default=Path("event-bauteil"),
        help="Directory with event-bauteil/<format>/<scope>/PI_*.yaml "
        "(default: event-bauteil)",
    )
    parser.add_argument(
        "--event-mapping",
        type=Path,
        default=Path("event-mapping.json"),
        help="Skript-2 output (default: event-mapping.json)",
    )
    parser.add_argument(
        "--required-fields",
        type=Path,
        default=Path("event-required-fields.json"),
        help="Skript-4 output (default: event-required-fields.json)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("event"),
        help="Target directory for event specs (default: event)",
    )
    parser.add_argument(
        "--bo4e-dir",
        type=Path,
        default=Path("bo4e"),
        help="BO4E atom mirror, used to resolve transaktionsdaten field atoms "
        "(default: bo4e)",
    )
    parser.add_argument("--filter-format", help="Only process this format version")
    parser.add_argument("--filter-role", help="Only process this market role (LF/NB/MSB)")
    parser.add_argument("--filter-topic", help="Only process this topic/eventName")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log every emitted file"
    )

    args = parser.parse_args(argv)

    if not args.bauteil_dir.is_dir():
        print(f"error: bauteil-dir {args.bauteil_dir} is not a directory", file=sys.stderr)
        return EXIT_ERROR
    for path in (args.event_mapping, args.required_fields):
        if not path.is_file():
            print(f"error: input {path} not found", file=sys.stderr)
            return EXIT_NO_INPUT

    mapping = load_json(args.event_mapping)
    required_doc = load_json(args.required_fields)

    filter_role = args.filter_role.upper() if args.filter_role else None

    bo4e_dir = args.bo4e_dir if args.bo4e_dir.is_dir() else None
    if bo4e_dir is None:
        print(
            f"warn: bo4e-dir {args.bo4e_dir} not found — transaktionsdaten "
            f"fields cannot be resolved to atoms",
            file=sys.stderr,
        )

    seen, written, warnings = compose(
        bauteil_dir=args.bauteil_dir,
        mapping=mapping,
        required_doc=required_doc,
        target=args.target,
        bo4e_dir=bo4e_dir,
        filter_format=args.filter_format,
        filter_role=filter_role,
        filter_topic=args.filter_topic,
        verbose=args.verbose,
    )

    for warning in warnings:
        print(f"warn: {warning}", file=sys.stderr)

    print(
        f"events seen: {seen}  written: {written}  warnings: {len(warnings)}",
        file=sys.stderr,
    )

    if seen == 0:
        return EXIT_NO_INPUT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
