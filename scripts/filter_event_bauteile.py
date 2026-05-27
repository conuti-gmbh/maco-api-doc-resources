#!/usr/bin/env python3
"""Strip transaktionsdaten from pruefi specs to produce event-bauteile.

Reads `pruefi/<format>/<scope>/PI_<id>.yaml`, removes the `transaktionsdaten`
property from the top-level container and drops every Container-Subset-Schema
rooted under `PI_<id>__transaktionsdaten`. Writes the result to
`event-bauteil/<format>/<scope>/PI_<id>.yaml`.

Story: MACO-13040 (Filter step, first of three generator scripts).
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Iterator
from pathlib import Path

from ruamel.yaml import YAML


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_SPECS = 2

TRANSAKTIONSDATEN = "transaktionsdaten"


def make_yaml() -> YAML:
    """Configured ruamel.yaml instance with deterministic dumping behaviour."""
    yaml = YAML(typ="rt")
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    yaml.width = 4096  # avoid line wrapping mid-string
    return yaml


def iter_spec_paths(
    source: Path,
    *,
    filter_format: str | None,
    filter_scope: str | None,
    filter_pruefi: str | None,
) -> Iterator[Path]:
    """Yield PI_*.yaml paths under source, filtered by optional predicates."""
    if not source.is_dir():
        return
    for format_dir in sorted(source.iterdir()):
        if not format_dir.is_dir():
            continue
        if filter_format and format_dir.name != filter_format:
            continue
        for scope_dir in sorted(format_dir.iterdir()):
            if not scope_dir.is_dir():
                continue
            if filter_scope and scope_dir.name != filter_scope:
                continue
            for spec_path in sorted(scope_dir.glob("PI_*.yaml")):
                if filter_pruefi and spec_path.stem != f"PI_{filter_pruefi}":
                    continue
                yield spec_path


def filter_spec(spec: dict, top_key: str) -> tuple[dict, bool]:
    """Return (filtered-spec, was-empty-after-filter).

    A spec is "empty-after-filter" when removing transaktionsdaten leaves the
    top container with no remaining properties. The caller decides what to do
    with such specs (skip with warning).
    """
    schemas = spec["components"]["schemas"]
    top_schema = schemas[top_key]

    # 1. Drop the transaktionsdaten property + required entry from the top.
    properties = top_schema.get("properties", {})
    if TRANSAKTIONSDATEN in properties:
        del properties[TRANSAKTIONSDATEN]
    required = top_schema.get("required")
    if required is not None and TRANSAKTIONSDATEN in required:
        required.remove(TRANSAKTIONSDATEN)
        if not required:
            del top_schema["required"]

    # 2. Drop all Container-Subset-Schemas rooted under transaktionsdaten.
    drop_prefix = f"{top_key}__{TRANSAKTIONSDATEN}"
    keys_to_drop = [
        key
        for key in schemas
        if key == drop_prefix or key.startswith(f"{drop_prefix}__")
    ]
    for key in keys_to_drop:
        del schemas[key]

    is_empty = not properties
    return spec, is_empty


def process(
    source: Path,
    target: Path,
    *,
    filter_format: str | None,
    filter_scope: str | None,
    filter_pruefi: str | None,
    verbose: bool,
) -> tuple[int, int, int]:
    """Filter every matching spec from source → target. Returns counts."""
    yaml = make_yaml()
    written = 0
    skipped_empty = 0
    seen = 0

    for spec_path in iter_spec_paths(
        source,
        filter_format=filter_format,
        filter_scope=filter_scope,
        filter_pruefi=filter_pruefi,
    ):
        seen += 1
        relative = spec_path.relative_to(source)
        top_key = spec_path.stem  # PI_<id>

        with spec_path.open("r", encoding="utf-8") as fh:
            spec = yaml.load(fh)

        if not isinstance(spec, dict) or "components" not in spec:
            print(
                f"warn: {relative} has no components block — skipping",
                file=sys.stderr,
            )
            continue

        schemas = spec.get("components", {}).get("schemas", {})
        if top_key not in schemas:
            print(
                f"warn: {relative} has no top schema {top_key} — skipping",
                file=sys.stderr,
            )
            continue

        filtered, is_empty = filter_spec(spec, top_key)

        if is_empty:
            skipped_empty += 1
            if verbose:
                print(
                    f"skip: {relative} would be empty after stripping "
                    f"transaktionsdaten — not emitted",
                    file=sys.stderr,
                )
            continue

        out_path = target / relative
        out_path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO()
        yaml.dump(filtered, buffer)
        out_path.write_text(buffer.getvalue(), encoding="utf-8")
        written += 1
        if verbose:
            print(f"wrote: {out_path.relative_to(target.parent)}", file=sys.stderr)

    return seen, written, skipped_empty


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Filter transaktionsdaten out of pruefi specs to produce "
            "event-bauteil specs."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("pruefi"),
        help="Source directory with pruefi/<format>/<scope>/PI_*.yaml "
        "(default: pruefi)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("event-bauteil"),
        help="Target directory for event-bauteil output (default: event-bauteil)",
    )
    parser.add_argument("--filter-format", help="Only process this format version")
    parser.add_argument("--filter-scope", help="Only process this scope")
    parser.add_argument("--filter-pruefi", help="Only process this prüfi id")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log every emitted file"
    )

    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"error: source {args.source} is not a directory", file=sys.stderr)
        return EXIT_ERROR

    seen, written, skipped_empty = process(
        args.source,
        args.target,
        filter_format=args.filter_format,
        filter_scope=args.filter_scope,
        filter_pruefi=args.filter_pruefi,
        verbose=args.verbose,
    )

    print(
        f"specs seen: {seen}  written: {written}  "
        f"skipped (empty-after-filter): {skipped_empty}",
        file=sys.stderr,
    )

    if seen == 0:
        return EXIT_NO_SPECS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
