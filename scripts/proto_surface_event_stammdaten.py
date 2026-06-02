#!/usr/bin/env python3
"""PROTOTYPE — make ``stammdaten`` a visible property on event specs.

Background (v202604 Apidog-import PoC): event specs express ``stammdaten`` only
via a top-level ``allOf: [{oneOf: [$ref PI_x]}]`` that sits as a *sibling* to
``properties: {transaktionsdaten, zusatzdaten}``. JSON-Schema-correct, but
Apidog's model/example view does not merge a bare ``allOf[oneOf]`` into the
parent property list — so ``stammdaten`` renders as "required but undefined" and
the event looks like it has no Stammdaten.

This rewrites each event file in place to the equivalent, render-friendly shape:

    properties:
      stammdaten:
        oneOf: [ $ref PI_x.yaml#/.../PI_x__stammdaten ]   # <- now a real property
      transaktionsdaten: …
      zusatzdaten: …
    required: [stammdaten, transaktionsdaten, zusatzdaten]

The ``$ref`` target moves from the PI *envelope* (``PI_x``, which only wraps
``stammdaten``) to the named ``PI_x__stammdaten`` sub-schema the bauteil files
already expose — so no double ``stammdaten.stammdaten`` nesting, one wrapper
level less, semantics identical.

This is the PROTOTYPE form of the canonical fix in
``compose_event_specs.py::build_schema`` (kept in sync there). Applied directly to
the committed event specs so the v202604 PoC branch can be re-bundled and handed
to Apidog without re-running the full generator against the process repos.

Idempotent: files already in the new shape (no top-level ``allOf``) are skipped.

Usage:
    proto_surface_event_stammdaten.py --event-dir event/202604 [--check]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ruamel.yaml import YAML

_ENVELOPE_REF = re.compile(
    r"(#/components/schemas/PI_(\d+))$"
)


def make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def envelope_to_stammdaten_ref(ref: str) -> str:
    """PI_x.yaml#/components/schemas/PI_x  ->  …/PI_x__stammdaten"""
    m = _ENVELOPE_REF.search(ref)
    if not m:
        raise ValueError(f"unexpected oneOf branch ref (not a PI envelope): {ref!r}")
    return ref[: m.start()] + m.group(1) + "__stammdaten"


def surface(body) -> bool:
    """Rewrite one event schema body in place. Returns True if changed."""
    all_of = body.get("allOf")
    if not all_of:
        return False  # stub (all pending) or already migrated — leave untouched
    if len(all_of) != 1 or "oneOf" not in all_of[0]:
        raise ValueError(f"unexpected top-level allOf shape: {list(all_of)!r}")

    one_of = all_of[0]["oneOf"]
    for branch in one_of:
        ref = branch.get("$ref")
        if not isinstance(ref, str):
            raise ValueError(f"unexpected oneOf branch (no $ref): {branch!r}")
        branch["$ref"] = envelope_to_stammdaten_ref(ref)

    props = body.setdefault("properties", {})
    # Insert stammdaten first for nicer rendering order.
    rebuilt = {"stammdaten": {"oneOf": one_of}}
    for k, v in props.items():
        rebuilt[k] = v
    body["properties"] = rebuilt
    del body["allOf"]
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--event-dir", required=True, type=Path)
    ap.add_argument("--check", action="store_true",
                    help="report what would change, write nothing, exit 1 if dirty")
    args = ap.parse_args(argv)

    y = make_yaml()
    files = sorted(args.event_dir.rglob("*.yaml"))
    if not files:
        print(f"ERROR: no event files under {args.event_dir}", file=sys.stderr)
        return 2

    changed, stubs, already = 0, 0, 0
    for f in files:
        doc = y.load(f.read_text(encoding="utf-8"))
        schemas = (doc.get("components") or {}).get("schemas") or {}
        if len(schemas) != 1:
            raise ValueError(f"{f}: expected exactly one event schema, got {len(schemas)}")
        body = next(iter(schemas.values()))
        had_allof = bool(body.get("allOf"))
        try:
            did = surface(body)
        except ValueError as e:
            print(f"ERROR in {f.name}: {e}", file=sys.stderr)
            return 2
        if did:
            changed += 1
            if not args.check:
                with f.open("w", encoding="utf-8") as fh:
                    y.dump(doc, fh)
        elif had_allof:
            already += 1
        else:
            stubs += 1

    verb = "would change" if args.check else "changed"
    print(f"{verb}={changed} stubs(skipped)={stubs} already-migrated={already} "
          f"total={len(files)}")
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
