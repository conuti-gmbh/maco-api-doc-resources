#!/usr/bin/env python3
"""Bundle the atomic per-format OpenAPI files into ONE self-contained spec.

POC for the Apidog-Import change (MACO-13035 / MACO-13041): Apidog can no longer
import the atomic ``$ref`` files from a repo+branch, so we mash everything for one
format version into a single OpenAPI 3.1 document — all schemas inlined under
``components.schemas`` and every ``$ref`` rewritten to a local
``#/components/schemas/<key>`` pointer.

Graph (all refs are ``<relpath>.yaml#/components/schemas/<Name>`` or a same-file
``#/components/schemas/<Name>``):

    event/<fmt>        -> bo4e/cdoc + event-bauteil/<fmt>/<scope>/PI_*
    pruefi/<fmt>       -> bo4e/fields/*
    event-bauteil/<fmt>-> bo4e/fields/*
    bo4e/*             -> bo4e/{bo,com,cdoc,enum,fields}/*

Roots = the consumer-facing top-level docs (``event/<fmt>`` + ``pruefi/<fmt>``);
``event-bauteil`` and ``bo4e`` atoms are pulled in transitively via BFS. Output is
deterministic (schemas sorted by key).

Each bundled schema key is derived from the source file's repo-relative path plus
the schema name, so collisions across files (e.g. many ``sparte`` atoms) are
impossible and event names with brackets/spaces become valid component keys.

Usage:
    bundle_spec.py --repo-root . --format 202604 [--roots event,pruefi] \\
                   --out bundle/maco-bo4e-202604.openapi.yaml
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path

from ruamel.yaml import YAML

_FRAG = "#/components/schemas/"


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 4096  # don't wrap long descriptions / refs
    return y


def slug(rel: str) -> str:
    """Sanitise a path+name into a valid OpenAPI component key ([A-Za-z0-9._-])."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", rel)


def key_for(repo_root: Path, file: Path, name: str) -> str:
    rel = file.resolve().relative_to(repo_root.resolve())
    rel_noext = rel.with_suffix("")
    return slug(str(rel_noext).replace("/", "__") + "__" + name)


def split_ref(ref: str):
    """('rel/path.yaml' | None, 'SchemaName') for a components/schemas ref."""
    if _FRAG not in ref:
        raise ValueError(f"unexpected $ref (not a components/schemas ref): {ref!r}")
    path_part, name = ref.split(_FRAG, 1)
    return (path_part or None, name)


def resolve(owner_file: Path, ref: str) -> tuple[Path, str]:
    path_part, name = split_ref(ref)
    if not path_part:
        target = owner_file  # same-file pointer
    else:
        target = (owner_file.parent / path_part).resolve()
    return target, name


def transform(node, owner_file: Path, repo_root: Path, targets: list):
    """Deep-copy ``node``; rewrite every $ref to a local bundled key and record
    the (file, name) it points to in ``targets``."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and _FRAG in ref:
            tfile, tname = resolve(owner_file, ref)
            targets.append((tfile, tname))
            out = {k: v for k, v in node.items() if k != "$ref"}
            out["$ref"] = _FRAG + key_for(repo_root, tfile, tname)
            # keep any sibling keys (rare) after rewriting
            return out
        # Non-schema $refs (e.g. external example URLs) pass through untouched.
        return {k: transform(v, owner_file, repo_root, targets) for k, v in node.items()}
    if isinstance(node, list):
        return [transform(v, owner_file, repo_root, targets) for v in node]
    return node


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".", type=Path)
    ap.add_argument("--format", required=True, help="format version, e.g. 202604")
    ap.add_argument("--roots", default="event,pruefi",
                    help="comma-separated top-level dirs used as bundle roots")
    ap.add_argument("--catalog-roots", default="",
                    help="comma-separated NON-format-scoped dirs whose every schema "
                         "is additionally seeded as a root, e.g. "
                         "'bo4e/bo,bo4e/com,bo4e/cdoc,bo4e/enum'. Use to surface the "
                         "whole BO4E objects (Marktlokation, Messlokation, …) as a "
                         "browsable catalog — by default they are unreachable because "
                         "specs only $ref field-level atoms.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    y = _yaml()
    repo_root = args.repo_root.resolve()
    fmt = args.format

    # File cache: abs_path -> {schema_name: body}
    cache: dict[Path, dict] = {}

    def schemas_of(path: Path) -> dict:
        path = path.resolve()
        if path not in cache:
            if not path.exists():
                cache[path] = {}
            else:
                doc = y.load(path.read_text(encoding="utf-8")) or {}
                cache[path] = (doc.get("components") or {}).get("schemas") or {}
        return cache[path]

    # Roots: every schema in every file under <root>/<fmt>/**
    roots: list[tuple[Path, str]] = []
    for root in [r.strip() for r in args.roots.split(",") if r.strip()]:
        base = repo_root / root / fmt
        if not base.is_dir():
            print(f"WARN: root dir not found: {base}", file=sys.stderr)
            continue
        for f in sorted(base.rglob("*.yaml")):
            for name in schemas_of(f):
                roots.append((f.resolve(), name))

    # Catalog roots: non-format-scoped dirs (e.g. bo4e/bo) — seed every schema so
    # the whole BO4E objects show up as top-level browsable entries even when no
    # spec references them directly (specs only ref field-level atoms).
    catalog_roots = [r.strip() for r in args.catalog_roots.split(",") if r.strip()]
    for root in catalog_roots:
        base = repo_root / root
        if not base.is_dir():
            print(f"WARN: catalog-root dir not found: {base}", file=sys.stderr)
            continue
        for f in sorted(base.rglob("*.yaml")):
            for name in schemas_of(f):
                roots.append((f.resolve(), name))

    if not roots:
        print("ERROR: no root schemas found", file=sys.stderr)
        return 2

    # BFS over (file, name)
    out_schemas: dict[str, object] = {}
    missing: list[str] = []
    seen: set[tuple[Path, str]] = set()
    queue: deque[tuple[Path, str]] = deque(roots)

    while queue:
        f, n = queue.popleft()
        if (f, n) in seen:
            continue
        seen.add((f, n))
        body = schemas_of(f).get(n)
        if body is None:
            missing.append(f"{f.relative_to(repo_root) if f.is_relative_to(repo_root) else f}#{n}")
            continue
        targets: list[tuple[Path, str]] = []
        new_body = transform(body, f, repo_root, targets)
        out_schemas[key_for(repo_root, f, n)] = new_body
        for t in targets:
            if t not in seen:
                queue.append(t)

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": f"MACO BO4E API — {fmt}",
            "version": fmt,
            "description": (
                "Auto-generated bundle of all BO4E event + Prüfi specs for format "
                f"version {fmt}. Every $ref is inlined to a local "
                "#/components/schemas/<key> pointer (one self-contained document "
                "for Apidog import). Do not edit by hand — regenerate via "
                "scripts/bundle_spec.py."
            ),
        },
        "paths": {},
        "components": {"schemas": {k: out_schemas[k] for k in sorted(out_schemas)}},
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        y.dump(spec, fh)

    size = args.out.stat().st_size
    print(f"roots={len(roots)} schemas={len(out_schemas)} "
          f"missing={len(missing)} bytes={size} -> {args.out}")
    if missing and args.verbose:
        for m in missing[:50]:
            print(f"  MISSING {m}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
