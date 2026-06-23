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
# Host of JSON-Schema dialect/meta-schema URIs ($ref targets that can't be
# bundled and that strict OpenAPI 3.1 builders reject — see rewrite()).
_META_SCHEMA_HOST = "json-schema.org"


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 4096  # don't wrap long descriptions / refs
    return y


def slug(rel: str) -> str:
    """Sanitise a path+name into a valid OpenAPI component key ([A-Za-z0-9._-])."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", rel)


def verbose_key(repo_root: Path, file: Path, name: str) -> str:
    """Path-qualified key: <repo/rel/path>__<name>. Long but collision-free by
    construction — used as the deterministic fallback when a readable key clashes."""
    rel = file.resolve().relative_to(repo_root.resolve())
    rel_noext = rel.with_suffix("")
    return slug(str(rel_noext).replace("/", "__") + "__" + name)


def readable_key(repo_root: Path, file: Path, name: str) -> str:
    """Short, human-readable component key for Apidog's display (it shows the key
    verbatim). Drops the format version, the scope (UTILMD…) and the file-vs-name
    doubling; uses '.' as the separator (valid in OpenAPI component keys).

        bo4e/bo/Marktlokation                -> bo.Marktlokation
        bo4e/enum/MarktlokationsTyp          -> enum.MarktlokationsTyp
        bo4e/fields/bo/Marktlokation/x (x)   -> field.Marktlokation.x
        pruefi/<fmt>/<scope>/PI_5#PI_5__a__b -> pruefi.PI_5.a.b
        event-bauteil/<fmt>/<scope>/…#PI_5…  -> bauteil.PI_5…
        event/<fmt>/[NB]_X  ([NB] X)         -> event.NB.X

    NOT guaranteed unique on its own — collisions fall back to verbose_key (see
    build_keymap). Empirically 0 collisions for 202604."""
    rel = file.resolve().relative_to(repo_root.resolve()).with_suffix("")
    parts = str(rel).split("/")
    ns = parts[0]
    if ns == "bo4e":
        tier = parts[1]  # bo / com / cdoc / enum / fields
        if tier == "fields":
            # bo4e/fields/<tier2>/<Owner>/<field-file> ; name == field
            return f"field.{parts[3]}.{slug(name)}"
        return f"{tier}.{slug(name)}"
    if ns == "pruefi":
        return "pruefi." + slug(name).replace("__", ".")
    if ns == "event-bauteil":
        return "bauteil." + slug(name).replace("__", ".")
    if ns == "event":
        m = re.match(r"^\[(\w+)\]\s*(.+)$", name)  # "[NB] START_X" -> NB . START_X
        body = f"{m.group(1)}.{slug(m.group(2))}" if m else slug(name)
        return "event." + body
    return slug(str(rel).replace("/", ".") + "." + name)


def build_keymap(repo_root: Path, pairs: list) -> dict:
    """(file, name) -> final bundle key. Readable by default; any readable-key
    collision drops *all* its members to verbose_key (collision-free). Asserts the
    final mapping is injective."""
    from collections import defaultdict

    by_readable: dict = defaultdict(list)
    for f, n in pairs:
        by_readable[readable_key(repo_root, f, n)].append((f, n))

    keymap: dict = {}
    fallbacks = 0
    for rk, members in by_readable.items():
        if len(members) == 1:
            keymap[members[0]] = rk
        else:
            for f, n in members:
                keymap[(f, n)] = verbose_key(repo_root, f, n)
                fallbacks += 1

    final = set(keymap.values())
    if len(final) != len(keymap):
        raise RuntimeError("BUG: key map not injective even after verbose fallback")
    return keymap, fallbacks


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


def collect_targets(node, owner_file: Path, targets: list):
    """Walk ``node`` and record every (file, name) a schema-$ref points to."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and _FRAG in ref:
            targets.append(resolve(owner_file, ref))
        for k, v in node.items():
            if k != "$ref":
                collect_targets(v, owner_file, targets)
    elif isinstance(node, list):
        for v in node:
            collect_targets(v, owner_file, targets)


def rewrite(node, owner_file: Path, keymap: dict):
    """Deep-copy ``node``; rewrite every schema-$ref to its local bundled key via
    ``keymap``. Non-schema $refs (external example URLs) pass through untouched.

    Exception: a $ref to a JSON-Schema dialect/meta-schema (e.g. the BO4E
    ``object.meta`` schema's ``allOf: [{$ref: https://json-schema.org/draft/...}]``)
    is dropped to a permissive empty schema. Such a ref is unresolvable in a
    self-contained bundle and a strict OpenAPI 3.1 schema builder (vacuum)
    rejects it as a missing component. Sibling keywords are preserved."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and _FRAG in ref:
            out = {k: v for k, v in node.items() if k != "$ref"}
            out["$ref"] = _FRAG + keymap[resolve(owner_file, ref)]
            return out
        if isinstance(ref, str) and _META_SCHEMA_HOST in ref:
            return {k: rewrite(v, owner_file, keymap)
                    for k, v in node.items() if k != "$ref"}
        return {k: rewrite(v, owner_file, keymap) for k, v in node.items()}
    if isinstance(node, list):
        return [rewrite(v, owner_file, keymap) for v in node]
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

    # Phase 1 — BFS to discover the full reachable (file, name) set (no rewriting
    # yet: keys depend on the global set, so we can't assign them per-node here).
    missing: list[str] = []
    seen: set[tuple[Path, str]] = set()
    discovered: list[tuple[Path, str]] = []  # insertion order = stable
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
        discovered.append((f, n))
        targets: list[tuple[Path, str]] = []
        collect_targets(body, f, targets)
        for t in targets:
            if t not in seen:
                queue.append(t)

    # Phase 2 — assign readable keys (verbose fallback on collision).
    keymap, fallbacks = build_keymap(repo_root, discovered)

    # Phase 3 — emit each schema with its refs rewritten via the key map.
    out_schemas: dict[str, object] = {}
    for f, n in discovered:
        out_schemas[keymap[(f, n)]] = rewrite(schemas_of(f).get(n), f, keymap)

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
          f"missing={len(missing)} verbose-fallback-keys={fallbacks} "
          f"bytes={size} -> {args.out}")
    if missing and args.verbose:
        for m in missing[:50]:
            print(f"  MISSING {m}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
