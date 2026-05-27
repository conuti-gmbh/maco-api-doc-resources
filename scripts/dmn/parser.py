#!/usr/bin/env python3
"""Parse a Camunda DMN 1.3 decision table into structured rules.

Targeted at `S_EVENT_VARIABLEN.dmn` (single decision per file, hitPolicy=FIRST,
one input column = eventName, ~37 output columns with Tasker-FN-strings).

Each rule maps an event-name input value to a dict {output_column_name: cell_text}.
Empty cells (no <text> or empty text) become None.

DMN source is internal/trusted (conuti Process-Repos); the parser uses stdlib
xml.etree without defusedxml.

Story: MACO-13040 (Skript 4).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass


DMN_NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"

_DECISION_TAG = f"{{{DMN_NS}}}decision"
_DECISION_TABLE_TAG = f"{{{DMN_NS}}}decisionTable"
_INPUT_TAG = f"{{{DMN_NS}}}input"
_OUTPUT_TAG = f"{{{DMN_NS}}}output"
_RULE_TAG = f"{{{DMN_NS}}}rule"
_INPUT_ENTRY_TAG = f"{{{DMN_NS}}}inputEntry"
_OUTPUT_ENTRY_TAG = f"{{{DMN_NS}}}outputEntry"
_TEXT_TAG = f"{{{DMN_NS}}}text"
_DESCRIPTION_TAG = f"{{{DMN_NS}}}description"


@dataclass(frozen=True)
class Rule:
    """A single DMN rule: input event name plus output cells per column."""

    event_name: str
    outputs: tuple[tuple[str, str | None], ...]
    description: str | None


@dataclass(frozen=True)
class DecisionTable:
    """Parsed Camunda DMN decision-table view."""

    decision_id: str
    decision_name: str
    input_label: str
    output_columns: tuple[str, ...]
    hit_policy: str
    rules: tuple[Rule, ...]
    source_path: str


def parse_dmn_xml(xml_bytes: bytes, source_path: str) -> DecisionTable | None:
    """Parse a DMN payload. Returns None if no <decision>/<decisionTable> found.

    For DMNs with multiple <decision> elements, only the first one is parsed
    (S_EVENT_VARIABLEN.dmn has exactly one).
    """
    root = ET.fromstring(xml_bytes)
    decision = root.find(_DECISION_TAG)
    if decision is None:
        # Decision may live under definitions/{decision} or at root.
        decision = root.find(f".//{_DECISION_TAG}")
    if decision is None:
        return None
    table = decision.find(_DECISION_TABLE_TAG)
    if table is None:
        return None

    decision_id = decision.get("id", "")
    decision_name = decision.get("name", decision_id)
    hit_policy = table.get("hitPolicy", "UNIQUE")

    inputs = table.findall(_INPUT_TAG)
    input_label = (
        inputs[0].get("label", inputs[0].get("id", ""))
        if inputs
        else ""
    )

    output_columns = tuple(
        out.get("name") or out.get("label") or out.get("id", "")
        for out in table.findall(_OUTPUT_TAG)
    )

    rules: list[Rule] = []
    for rule_el in table.findall(_RULE_TAG):
        rule = _parse_rule(rule_el, output_columns)
        if rule is not None:
            rules.append(rule)

    return DecisionTable(
        decision_id=decision_id,
        decision_name=decision_name,
        input_label=input_label,
        output_columns=output_columns,
        hit_policy=hit_policy,
        rules=tuple(rules),
        source_path=source_path,
    )


def _parse_rule(
    rule_el: ET.Element, output_columns: tuple[str, ...]
) -> Rule | None:
    """Extract one rule's event-name input + per-column outputs."""
    input_entries = rule_el.findall(_INPUT_ENTRY_TAG)
    if not input_entries:
        return None
    event_name = _strip_text(input_entries[0])
    if event_name is None:
        return None

    output_entries = rule_el.findall(_OUTPUT_ENTRY_TAG)
    cells: list[tuple[str, str | None]] = []
    for column, entry in zip(output_columns, output_entries):
        cells.append((column, _strip_text(entry)))

    desc_el = rule_el.find(_DESCRIPTION_TAG)
    desc = desc_el.text.strip() if desc_el is not None and desc_el.text else None

    return Rule(
        event_name=event_name,
        outputs=tuple(cells),
        description=desc,
    )


def _strip_text(entry: ET.Element) -> str | None:
    """Read inner <text> of an inputEntry/outputEntry, strip whitespace+quotes.

    Returns None for empty / missing cells.
    """
    text_el = entry.find(_TEXT_TAG)
    if text_el is None or text_el.text is None:
        return None
    raw = text_el.text.strip()
    if not raw:
        return None
    # DMN cells often quote literals: "ECS_LIEFERBEGINN" or "FN:..."
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return raw
