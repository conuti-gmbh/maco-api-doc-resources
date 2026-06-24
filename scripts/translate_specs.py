#!/usr/bin/env python3
"""Translate every OpenAPI spec under ``--source`` via the bo4e-translator's
OpenAPI endpoint and write the result under ``--target``, preserving the
relative path (e.g. ``pruefi/`` → ``pruefi-en/``).

The translator route is YAML-in / JSON-out (Story 04 / MACO-13036); this helper
dumps the JSON response back to YAML so the downstream generator scripts
(``filter_event_bauteile`` / ``compose_event_specs`` / ``bundle_spec``) — which
read YAML — consume it unchanged, and the published EN specs match the DE
format. The endpoint rewrites ``$ref`` ``bo4e/`` → ``bo4e-en/`` and translates
container-schema keys + property names, so the EN specs reference the canonical
``bo4e-schema-en`` atoms (Story 11 / MACO-13088).

Exit codes:
  0  all specs translated
  1  source is not a directory
  2  no specs found under source
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from ruamel.yaml import YAML


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True,
                    help="directory of source specs (recursed for *.yaml)")
    ap.add_argument("--target", type=Path, required=True,
                    help="output directory (relative paths preserved)")
    ap.add_argument("--endpoint", required=True,
                    help="full translate URL, e.g. "
                         "http://localhost:8080/api/translate/openapi/de/en/")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.source.is_dir():
        print(f"ERROR: source {args.source} is not a directory", file=sys.stderr)
        return 1

    specs = sorted(args.source.rglob("*.yaml"))
    if not specs:
        print(f"ERROR: no specs found under {args.source}", file=sys.stderr)
        return 2

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # avoid line-wrapping long $ref strings

    count = 0
    for spec in specs:
        body = spec.read_bytes()
        req = urllib.request.Request(
            args.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/yaml"},
        )
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            translated = json.load(resp)

        rel = spec.relative_to(args.source)
        out = args.target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            y.dump(translated, fh)
        count += 1
        if args.verbose:
            print(f"  {rel}")

    print(f"translated {count} specs: {args.source} -> {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
