#!/usr/bin/env python3
"""Verify that every external ``$ref`` in the generated specs resolves to a file
that actually exists.

The generator emits relative, file-level ``$ref``s of the form
``../../bo4e/fields/cdoc/Transaktionsdaten/absender.yaml#/components/schemas/absender``
(events) or ``../../../bo4e/fields/bo/Statusbericht/datumPruefung.yaml#/...``
(prüfis), plus ``../../event-bauteil/<format>/<scope>/PI_<id>.yaml#/...`` for the
event ``oneOf`` branches. A missing target means a broken bundle and a broken
Apidog import — this check is the CI gate that catches it before push.

Local ``#/components/schemas/...`` refs are intra-document and skipped; only
refs carrying a file path are resolved (relative to the owning file) and the
target file must exist. The fragment after ``#`` is not validated here — the
vacuum OpenAPI lint covers structural validity.

Exit codes:
  0  every external $ref resolves
  1  at least one external $ref points at a missing file
  2  no spec files found under the given roots (generation likely failed)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML

DEFAULT_ROOTS = ["pruefi", "event-bauteil", "event"]


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.preserve_quotes = True
    return y


def iter_refs(node):
    """Yield every ``$ref`` string value anywhere in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


def check_file(path: Path, y: YAML) -> list[tuple[str, Path]]:
    """Return [(ref, resolved_target)] for external refs whose target is missing."""
    doc = y.load(path.read_text(encoding="utf-8")) or {}
    missing: list[tuple[str, Path]] = []
    for ref in iter_refs(doc):
        file_part = ref.split("#", 1)[0]
        if not file_part:
            # pure "#/..." local pointer
            continue
        target = (path.parent / file_part).resolve()
        if not target.is_file():
            missing.append((ref, target))
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo-root", default=".", type=Path,
        help="repository root containing the spec roots",
    )
    ap.add_argument(
        "--roots", default=",".join(DEFAULT_ROOTS),
        help="comma-separated spec roots to scan (default: pruefi,event-bauteil,event)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    y = _yaml()
    repo_root = args.repo_root.resolve()
    roots = [r.strip() for r in args.roots.split(",") if r.strip()]

    specs: list[Path] = []
    for root in roots:
        base = repo_root / root
        if base.is_dir():
            specs.extend(sorted(base.rglob("*.yaml")))

    if not specs:
        print(
            f"ERROR: no spec files found under {roots} in {repo_root} — "
            "generation step likely failed",
            file=sys.stderr,
        )
        return 2

    total_refs = 0
    broken: list[tuple[Path, str, Path]] = []
    for spec in specs:
        missing = check_file(spec, y)
        for ref, target in missing:
            broken.append((spec, ref, target))
        if args.verbose:
            rel = spec.relative_to(repo_root)
            print(f"  checked {rel}")

    # second pass count (cheap; keeps the message honest about scope)
    for spec in specs:
        doc = y.load(spec.read_text(encoding="utf-8")) or {}
        total_refs += sum(
            1 for ref in iter_refs(doc) if ref.split("#", 1)[0]
        )

    print(
        f"$ref consistency: scanned {len(specs)} specs, "
        f"{total_refs} external refs, {len(broken)} broken"
    )

    if broken:
        print("\nBroken external $refs:", file=sys.stderr)
        for spec, ref, target in broken:
            print(
                f"  {spec.relative_to(repo_root)}\n"
                f"    ref:    {ref}\n"
                f"    target: {target} (missing)",
                file=sys.stderr,
            )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
