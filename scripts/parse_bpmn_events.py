#!/usr/bin/env python3
"""Build event-mapping.json from Camunda T_*.bpmn files.

Walks `<processes-root>/maco-{lf,nb,msb}-processes/<format>/T_PROZESSE/T_*.bpmn`
in the working tree (expected to be the dev branch — caller is responsible
for the checkout, analogous to the bo4e-generator sync workflow). Parses
each file into a ProcessEntry via scripts.bpmn.parser, then emits a
deterministic JSON mapping for use by compose_event_specs.py.

Output schema:

    {
      "_provenance": {
        "maco-lf-processes": "<short-sha>",
        "maco-nb-processes": "<short-sha>",
        "maco-msb-processes": "<short-sha>"
      },
      "events": {
        "<format>": {
          "<role>": {
            "<topic>": {
              "process_id": "...",
              "process_name_raw": "...",
              "source": "maco-<role>-processes/<format>/T_PROZESSE/T_<EVENT>.bpmn",
              "pruefis": [
                {"id": 55001, "paths": [["${sparte==..}", ...]]},
                ...
              ]
            }
          }
        }
      }
    }

Story: MACO-13040.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from scripts.bpmn.parser import ProcessEntry, parse_bpmn_xml


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_FILES = 2

ROLES = ("lf", "nb", "msb")


def discover_bpmn_files(
    processes_root: Path,
    *,
    filter_format: str | None,
    filter_role: str | None,
) -> Iterator[tuple[str, str, Path]]:
    """Yield (role, format_version, absolute_path) for every T_*.bpmn.

    Role is one of lf/nb/msb. Format version is the four/six-digit directory
    name (e.g. 202604). Iteration order is deterministic via sorting.
    """
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
            t_proc_dir = format_dir / "T_PROZESSE"
            if not t_proc_dir.is_dir():
                continue
            for bpmn_path in sorted(t_proc_dir.glob("T_*.bpmn")):
                yield role, format_dir.name, bpmn_path


def relative_source(processes_root: Path, abs_path: Path) -> str:
    """Path relative to processes-root, using forward slashes for portability."""
    return abs_path.relative_to(processes_root).as_posix()


def collect_provenance(
    processes_root: Path, filter_role: str | None
) -> dict[str, str]:
    """Return {repo-name: short-sha} for every present process repo."""
    provenance: dict[str, str] = {}
    for role in ROLES:
        if filter_role and role != filter_role:
            continue
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
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(
                f"warn: cannot read HEAD of {repo.name}: {exc}",
                file=sys.stderr,
            )
            continue
        provenance[repo.name] = result.stdout.strip()
    return provenance


def process_entry_to_json(entry: ProcessEntry) -> dict:
    """Convert a ProcessEntry into the on-disk JSON representation."""
    return {
        "process_id": entry.process_id,
        "process_name_raw": entry.name_raw,
        "source": entry.source_path,
        "pruefis": [
            {
                "id": p.id,
                "paths": [list(path) for path in p.paths],
            }
            for p in entry.pruefis
        ],
    }


def build_mapping(
    processes_root: Path,
    *,
    filter_format: str | None,
    filter_role: str | None,
    verbose: bool,
) -> tuple[dict, int, int, list[str]]:
    """Walk all T_*.bpmn, build the events mapping. Returns (events, seen,
    parsed, warnings)."""
    events: dict[str, dict[str, dict[str, dict]]] = {}
    seen = 0
    parsed = 0
    warnings: list[str] = []

    for role, format_version, bpmn_path in discover_bpmn_files(
        processes_root,
        filter_format=filter_format,
        filter_role=filter_role,
    ):
        seen += 1
        source = relative_source(processes_root, bpmn_path)
        try:
            xml_bytes = bpmn_path.read_bytes()
        except OSError as exc:
            warnings.append(f"read failed: {source}: {exc}")
            continue

        entry = parse_bpmn_xml(xml_bytes, source)
        if entry is None:
            warnings.append(f"no <bpmn:process>: {source}")
            continue
        if not entry.pruefis:
            if verbose:
                print(
                    f"skip: no pruefidentifikator in {source}",
                    file=sys.stderr,
                )
            continue

        # MACO-13123: the process id is the canonical topic source. Cross-check
        # it against the directory (role/format) and the filename; these drifts
        # are runtime-relevant, unlike the now-ignored cosmetic
        # <bpmn:process name> drift.
        fn_stem = bpmn_path.stem
        fn_topic = fn_stem[len("T_"):] if fn_stem.startswith("T_") else fn_stem
        if entry.id_role is None:
            warnings.append(
                f"non-conventional process id {entry.process_id!r} in {source} "
                f"— topic taken from filename"
            )
        else:
            if entry.id_role.upper() != role.upper():
                warnings.append(
                    f"id role {entry.id_role!r} != repo {role.upper()!r} in {source}"
                )
            if entry.id_format != format_version:
                warnings.append(
                    f"id format {entry.id_format!r} != dir {format_version!r} in {source}"
                )
            if entry.topic != fn_topic:
                warnings.append(
                    f"id/filename drift in {source}: id event {entry.topic!r} "
                    f"!= filename {fn_topic!r}"
                )

        parsed += 1
        bucket = events.setdefault(format_version, {}).setdefault(
            role.upper(), {}
        )

        if entry.topic in bucket:
            warnings.append(
                f"duplicate topic {entry.topic!r} in {role}/{format_version}: "
                f"{bucket[entry.topic]['source']} vs {source}"
            )
            continue
        bucket[entry.topic] = process_entry_to_json(entry)
        if verbose:
            print(f"parsed: {source} → {entry.topic}", file=sys.stderr)

    return events, seen, parsed, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract event → pruefidentifikator mapping from "
            "Camunda T_*.bpmn files in the three process repos."
        ),
    )
    parser.add_argument(
        "--processes-root",
        type=Path,
        required=True,
        help=(
            "Directory containing maco-lf-processes/, maco-nb-processes/, "
            "maco-msb-processes/ checked out on dev (e.g. via git worktree)"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("event-mapping.json"),
        help="Output file path (default: event-mapping.json)",
    )
    parser.add_argument(
        "--filter-format", help="Only process this format version (e.g. 202604)"
    )
    parser.add_argument(
        "--filter-role", choices=ROLES, help="Only process this market role"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log every parsed file"
    )

    args = parser.parse_args(argv)

    if not args.processes_root.is_dir():
        print(
            f"error: processes-root {args.processes_root} is not a directory",
            file=sys.stderr,
        )
        return EXIT_ERROR

    events, seen, parsed, warnings = build_mapping(
        args.processes_root,
        filter_format=args.filter_format,
        filter_role=args.filter_role,
        verbose=args.verbose,
    )

    for warning in warnings:
        print(f"warn: {warning}", file=sys.stderr)

    if seen == 0:
        print(
            f"error: no T_*.bpmn files found under {args.processes_root}",
            file=sys.stderr,
        )
        return EXIT_NO_FILES

    provenance = collect_provenance(args.processes_root, args.filter_role)

    document = {
        "_provenance": provenance,
        "events": events,
    }
    serialised = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
    args.output.write_text(serialised + "\n", encoding="utf-8")

    print(
        f"bpmn files seen: {seen}  parsed: {parsed}  warnings: {len(warnings)}",
        file=sys.stderr,
    )
    print(f"wrote: {args.output}", file=sys.stderr)

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
