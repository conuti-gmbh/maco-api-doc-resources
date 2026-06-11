#!/usr/bin/env python3
"""Build event-required-fields.json from S_EVENT_VARIABLEN.dmn files.

Walks `<processes-root>/maco-{lf,nb,msb}-processes/<format>/S_TABELLEN/
S_EVENT_VARIABLEN.dmn` in the working tree. Per DMN, parses every rule
(event-name → output columns of Tasker-FN-strings) and classifies the JSONPath
reads into transaktionsdaten / stammdaten / zusatzdaten / other.

Output schema:

    {
      "_provenance": {
        "maco-<role>-processes": {"<format>": "<short-sha>", ...},
        ...
      },
      "_aggregate": {
        "event_role_combo_count": <int>,
        "common_core_threshold": 0.80,
        "common_core_transaktionsdaten": ["absender", "empfaenger", ...],
        "transaktionsdaten_frequency": [
          {"field": "absender", "count": 143, "pct": 97.3},
          ...
        ]
      },
      "events": {
        "<format>": {
          "<ROLE>": {
            "<eventName>": {
              "required_transaktionsdaten": ["absender", ...],
              "transaktionsdaten_reads": {"absender": ["rollencodenummer", ...]},
              "required_zusatzdaten": ["erpEvent", ...],
              "stammdaten_reads": ["MARKTLOKATION", "BILANZIERUNG"],
              "pruefidentifikator_source": "transaktionsdaten" | "erpEvent.eventName" | null,
              "description": "..." | null,
              "jsonpaths": {"<output_col>": ["$.path1", "$.path2"]}
            }

``transaktionsdaten_reads`` records, per top-level transaktionsdaten field, the
first sub-segments the DMN reads (e.g. ``absender.rollencodenummer`` →
``{"absender": ["rollencodenummer"]}``). Only nested reads are listed; a field
read at top level (scalar value) is omitted, signalling "use the whole field
atom". Skript 3 uses this to emit a focused sub-object instead of the entire BO.
          }
        }
      }
    }

Story: MACO-13040 (Skript 4 — Required-Quelle für Skript 3 compose_event_specs.py).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from scripts.dmn.parser import DecisionTable, Rule, parse_dmn_xml


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_FILES = 2

ROLES = ("lf", "nb", "msb")

# Default threshold: any field present in >= 80% of event-role-combos is
# considered Common-Core for documentation purposes.
DEFAULT_COMMON_CORE_THRESHOLD = 0.80

JSONPATH_RE = re.compile(r"(?:jsonPath|altJsonPath)=(\$\.[^,\)\s]+)")

BLOCK_TRANSAKTIONSDATEN = "transaktionsdaten"
BLOCK_STAMMDATEN = "stammdaten"
BLOCK_ZUSATZDATEN = "zusatzdaten"
BLOCK_OTHER = "other"

PRUEFI_OUTPUT_COL = "pruefidentifikator"
PRUEFI_PATH_DIRECT = "$.transaktionsdaten.pruefidentifikator"


def discover_dmn_files(
    processes_root: Path,
    *,
    filter_format: str | None,
    filter_role: str | None,
) -> Iterator[tuple[str, str, Path]]:
    """Yield (role, format_version, absolute_path) for every S_EVENT_VARIABLEN.dmn."""
    for role in ROLES:
        if filter_role and role != filter_role:
            continue
        repo = processes_root / f"maco-{role}-processes"
        if not repo.is_dir():
            continue
        for format_dir in sorted(repo.iterdir()):
            if not format_dir.is_dir() or not format_dir.name.isdigit():
                continue
            if filter_format and format_dir.name != filter_format:
                continue
            dmn_path = format_dir / "S_TABELLEN" / "S_EVENT_VARIABLEN.dmn"
            if dmn_path.is_file():
                yield role, format_dir.name, dmn_path


def relative_source(processes_root: Path, abs_path: Path) -> str:
    return abs_path.relative_to(processes_root).as_posix()


def collect_provenance(
    processes_root: Path,
    seen_formats: dict[str, set[str]],
) -> dict[str, dict[str, str]]:
    """Return {repo-name: {format: short-sha}} for every present process-repo
    that contributed at least one DMN."""
    provenance: dict[str, dict[str, str]] = {}
    for role in sorted(seen_formats):
        repo = processes_root / f"maco-{role}-processes"
        if not repo.is_dir():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--short=8", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            sha = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(
                f"warn: cannot read HEAD of {repo.name}: {exc}",
                file=sys.stderr,
            )
            continue
        provenance[repo.name] = {
            fmt: sha for fmt in sorted(seen_formats[role])
        }
    return provenance


def classify_path(jsonpath: str) -> tuple[str, str | None]:
    """Return (block, top_field) for a JSONPath like '$.transaktionsdaten.x.y'.

    top_field is the first segment after the block; e.g. 'absender' or
    'MARKTLOKATION'. Array indices like [0] are stripped.
    """
    for block in (BLOCK_TRANSAKTIONSDATEN, BLOCK_STAMMDATEN, BLOCK_ZUSATZDATEN):
        prefix = f"$.{block}."
        if jsonpath.startswith(prefix):
            tail = jsonpath[len(prefix):]
            tail = re.sub(r"\[\d+\]", "", tail)
            top = tail.split(".", 1)[0]
            return block, top
    return BLOCK_OTHER, None


def td_subpath(jsonpath: str, top: str) -> str:
    """Return the remainder after ``$.transaktionsdaten.<top>`` ('' if scalar).

    Array indices are stripped, so ``$.transaktionsdaten.absender[0].x`` and
    ``$.transaktionsdaten.absender.x`` both yield ``x``.
    """
    prefix = f"$.{BLOCK_TRANSAKTIONSDATEN}.{top}"
    rest = jsonpath[len(prefix):]
    rest = re.sub(r"\[\d+\]", "", rest)
    return rest.lstrip(".")


def extract_jsonpaths(value: str) -> list[str]:
    """Pull all JSONPath strings from a Tasker-FN cell value.

    A cell can contain primary `jsonPath=...` plus optional
    `altJsonPath=...` fallbacks. Both are extracted.
    """
    return JSONPATH_RE.findall(value)


def analyze_rule(rule: Rule) -> dict:
    """Turn a parsed DMN rule into the on-disk event entry."""
    td_fields: set[str] = set()
    td_nested: dict[str, set[str]] = defaultdict(set)
    td_scalar: set[str] = set()
    sd_fields: set[str] = set()
    zd_fields: set[str] = set()
    jsonpaths_per_column: dict[str, list[str]] = {}
    pruefi_source: str | None = None

    for column, cell in rule.outputs:
        if not cell:
            continue
        paths = extract_jsonpaths(cell)
        if not paths:
            # Literal output values like "STROM" — not a body read.
            continue
        jsonpaths_per_column[column] = paths
        for path in paths:
            block, top = classify_path(path)
            if block == BLOCK_TRANSAKTIONSDATEN and top:
                td_fields.add(top)
                sub = td_subpath(path, top)
                if sub:
                    td_nested[top].add(sub.split(".", 1)[0])
                else:
                    td_scalar.add(top)
            elif block == BLOCK_STAMMDATEN and top:
                sd_fields.add(top)
            elif block == BLOCK_ZUSATZDATEN and top:
                zd_fields.add(top)

        # Detect pruefidentifikator-source kind for the dedicated column.
        if column == PRUEFI_OUTPUT_COL:
            for path in paths:
                if path == PRUEFI_PATH_DIRECT:
                    pruefi_source = "transaktionsdaten"
                    break
                if path.startswith("$.zusatzdaten.erpEvent."):
                    pruefi_source = "erpEvent.eventName"

    # Only nested reads are recorded; a field also read at top level (scalar)
    # is treated as whole-field and dropped from the nested map.
    transaktionsdaten_reads = {
        field: sorted(segs)
        for field, segs in sorted(td_nested.items())
        if field not in td_scalar
    }

    return {
        "required_transaktionsdaten": sorted(td_fields),
        "transaktionsdaten_reads": transaktionsdaten_reads,
        "required_zusatzdaten": sorted(zd_fields),
        "stammdaten_reads": sorted(sd_fields),
        "pruefidentifikator_source": pruefi_source,
        "description": rule.description,
        "jsonpaths": {col: sorted(paths) for col, paths in sorted(jsonpaths_per_column.items())},
    }


def build_events_tree(
    files: list[tuple[str, str, Path]],
    *,
    verbose: bool,
) -> tuple[dict, list[str], dict[str, set[str]]]:
    """Walk every DMN, build events[format][ROLE][eventName] = entry.

    Returns (events_tree, warnings, seen_formats_by_role).
    """
    events: dict[str, dict[str, dict[str, dict]]] = {}
    warnings: list[str] = []
    seen_formats: dict[str, set[str]] = defaultdict(set)

    for role, format_version, dmn_path in files:
        try:
            xml_bytes = dmn_path.read_bytes()
        except OSError as exc:
            warnings.append(f"read failed: {dmn_path}: {exc}")
            continue
        source = dmn_path.as_posix()
        table = parse_dmn_xml(xml_bytes, source)
        if table is None:
            warnings.append(f"no <decisionTable>: {source}")
            continue
        seen_formats[role].add(format_version)
        bucket = events.setdefault(format_version, {}).setdefault(
            role.upper(), {}
        )
        for rule in table.rules:
            if rule.event_name in bucket:
                warnings.append(
                    f"duplicate eventName {rule.event_name!r} in "
                    f"{role}/{format_version} — first kept"
                )
                continue
            bucket[rule.event_name] = analyze_rule(rule)
            if verbose:
                print(
                    f"parsed: {role}/{format_version}/{rule.event_name}",
                    file=sys.stderr,
                )
    return events, warnings, dict(seen_formats)


def compute_aggregate(
    events: dict, common_core_threshold: float
) -> dict:
    """Compute frequency aggregate of transaktionsdaten-fields across all
    event-role-combos. Common-Core = fields whose pct >= threshold."""
    combo_count = 0
    field_counter: dict[str, int] = defaultdict(int)
    for _format, by_role in events.items():
        for _role, by_event in by_role.items():
            for _event_name, entry in by_event.items():
                combo_count += 1
                for field in entry["required_transaktionsdaten"]:
                    field_counter[field] += 1

    if combo_count == 0:
        return {
            "event_role_combo_count": 0,
            "common_core_threshold": common_core_threshold,
            "common_core_transaktionsdaten": [],
            "transaktionsdaten_frequency": [],
        }

    frequency = [
        {
            "field": field,
            "count": count,
            "pct": round(count * 100.0 / combo_count, 1),
        }
        for field, count in field_counter.items()
    ]
    frequency.sort(key=lambda x: (-x["count"], x["field"]))

    common_core = sorted(
        field
        for field, count in field_counter.items()
        if count / combo_count >= common_core_threshold
    )

    return {
        "event_role_combo_count": combo_count,
        "common_core_threshold": common_core_threshold,
        "common_core_transaktionsdaten": common_core,
        "transaktionsdaten_frequency": frequency,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract required body fields per event from "
            "S_EVENT_VARIABLEN.dmn in the three process repos."
        ),
    )
    parser.add_argument(
        "--processes-root",
        type=Path,
        required=True,
        help=(
            "Directory containing maco-lf-processes/, maco-nb-processes/, "
            "maco-msb-processes/ checked out on dev"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("event-required-fields.json"),
        help="Output file path (default: event-required-fields.json)",
    )
    parser.add_argument(
        "--filter-format", help="Only process this format version (e.g. 202604)"
    )
    parser.add_argument(
        "--filter-role", choices=ROLES, help="Only process this market role"
    )
    parser.add_argument(
        "--common-core-threshold",
        type=float,
        default=DEFAULT_COMMON_CORE_THRESHOLD,
        help=(
            "Frequency threshold above which a transaktionsdaten-field "
            "counts as Common-Core (default: 0.80)"
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log every parsed event"
    )

    args = parser.parse_args(argv)

    if not args.processes_root.is_dir():
        print(
            f"error: processes-root {args.processes_root} is not a directory",
            file=sys.stderr,
        )
        return EXIT_ERROR

    files = list(
        discover_dmn_files(
            args.processes_root,
            filter_format=args.filter_format,
            filter_role=args.filter_role,
        )
    )

    if not files:
        print(
            f"error: no S_EVENT_VARIABLEN.dmn found under {args.processes_root}",
            file=sys.stderr,
        )
        return EXIT_NO_FILES

    events, warnings, seen_formats = build_events_tree(
        files, verbose=args.verbose
    )

    for warning in warnings:
        print(f"warn: {warning}", file=sys.stderr)

    provenance = collect_provenance(args.processes_root, seen_formats)
    aggregate = compute_aggregate(events, args.common_core_threshold)

    document = {
        "_provenance": provenance,
        "_aggregate": aggregate,
        "events": events,
    }
    serialised = json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=False
    )
    args.output.write_text(serialised + "\n", encoding="utf-8")

    parsed_count = sum(
        len(by_event) for by_role in events.values() for by_event in by_role.values()
    )
    print(
        f"dmn files seen: {len(files)}  events parsed: {parsed_count}  "
        f"warnings: {len(warnings)}",
        file=sys.stderr,
    )
    print(f"wrote: {args.output}", file=sys.stderr)

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
