#!/usr/bin/env python3
"""Parse a single T_<EVENT>.bpmn into a ProcessEntry.

Extracts:
  * process id and raw process name from <bpmn:process>
  * the topic from the process id (`<ROLE>-<format>-T_<eventName>`); the id is
    the canonical source per Fachlichkeit (MACO-13123) — it is what Camunda
    correlates on and what the sender's `zusatzdaten.eventname` resolves to via
    the CallActivity `T_${eventName}`. The `<bpmn:process name>` attribute is an
    intentionally human-readable label and may drift; it is kept only as
    metadata. Falls back to the filename stem if the id is non-conventional.
  * every pruefidentifikator emitted by a descendant <bpmn:serviceTask>
  * the AND-conjunctive condition path(s) from start event to each
    pruefi-emitting task, via backward graph walk on <bpmn:sequenceFlow>

Multiple incoming paths to the same task become multiple condition paths
(OR of ANDs). Skript 3 (compose_event_specs.py) decides how to encode them.

BPMN source is internal/trusted (conuti Process-Repos), so the parser uses
stdlib xml.etree without defusedxml.

Story: MACO-13040.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass


BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

PRUEFI_PARAM_NAME = "pruefidentifikator"

# Canonical process-id shape: <ROLE>-<format>-T_<eventName>, e.g.
# "LF-202604-T_START_LIEFERBEGINN". The role token is left open (any alphabetic
# string) so new market roles need no code change; the format is six digits.
PROCESS_ID_RE = re.compile(r"^(?P<role>[A-Za-z]+)-(?P<format>\d{6})-T_(?P<event>.+)$")

_PROCESS_TAG = f"{{{BPMN_NS}}}process"
_SERVICE_TASK_TAG = f"{{{BPMN_NS}}}serviceTask"
_SEQUENCE_FLOW_TAG = f"{{{BPMN_NS}}}sequenceFlow"
_CONDITION_TAG = f"{{{BPMN_NS}}}conditionExpression"
_CAMUNDA_INPUT_TAG = f"{{{CAMUNDA_NS}}}inputParameter"


@dataclass(frozen=True)
class PruefiEntry:
    """One pruefidentifikator with the condition paths that gate it."""

    id: int
    paths: tuple[tuple[str, ...], ...]
    # Each inner tuple = AND-conjunctive list of condition expressions in
    # forward order (closest to start event first, closest to the task last).
    # Multiple inner tuples = OR of ANDs (multiple incoming paths).
    # Single empty tuple = unconditional (no conditions on any path).


@dataclass(frozen=True)
class ProcessEntry:
    """Parsed <bpmn:process> view."""

    process_id: str
    name_raw: str
    topic: str
    pruefis: tuple[PruefiEntry, ...]
    source_path: str
    # Role + format parsed out of the canonical process id; None when the id
    # does not follow the <ROLE>-<format>-T_<event> convention (topic then
    # falls back to the filename stem). Used by the CLI for drift cross-checks.
    id_role: str | None = None
    id_format: str | None = None


def parse_bpmn_xml(xml_bytes: bytes, source_path: str) -> ProcessEntry | None:
    """Parse a T_*.bpmn payload. Returns None if no <bpmn:process> present."""
    root = ET.fromstring(xml_bytes)
    proc = root.find(_PROCESS_TAG)
    if proc is None:
        return None

    process_id = proc.get("id", "")
    name_raw = proc.get("name", "") or process_id

    match = PROCESS_ID_RE.match(process_id)
    if match:
        id_role: str | None = match.group("role")
        id_format: str | None = match.group("format")
        topic = match.group("event")
    else:
        id_role = None
        id_format = None
        topic = _topic_from_source(source_path)

    incoming = _build_incoming_index(proc)
    pruefi_tasks = _collect_pruefi_tasks(proc)

    pruefi_entries = tuple(
        PruefiEntry(id=pruefi_id, paths=_walk_back(task_id, incoming))
        for task_id, pruefi_id in sorted(
            pruefi_tasks.items(), key=lambda kv: (kv[1], kv[0])
        )
    )

    return ProcessEntry(
        process_id=process_id,
        name_raw=name_raw,
        topic=topic,
        pruefis=pruefi_entries,
        source_path=source_path,
        id_role=id_role,
        id_format=id_format,
    )


def _topic_from_source(source_path: str) -> str:
    """Fallback topic = filename stem without the leading T_ and .bpmn suffix.

    Used only when the process id is non-conventional; the filename is the
    secondary canonical source per Fachlichkeit (MACO-13123).
    """
    stem = source_path.rsplit("/", 1)[-1]
    if stem.endswith(".bpmn"):
        stem = stem[: -len(".bpmn")]
    if stem.startswith("T_"):
        stem = stem[len("T_"):]
    return stem


def _build_incoming_index(
    proc: ET.Element,
) -> dict[str, list[tuple[str, str | None]]]:
    """Map node_id → list of (source_id, condition_expression_or_None)."""
    incoming: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for flow in proc.findall(_SEQUENCE_FLOW_TAG):
        source = flow.get("sourceRef")
        target = flow.get("targetRef")
        if source is None or target is None:
            continue
        cond_el = flow.find(_CONDITION_TAG)
        condition = (
            cond_el.text.strip()
            if cond_el is not None and cond_el.text is not None
            else None
        )
        incoming[target].append((source, condition))
    return incoming


def _collect_pruefi_tasks(proc: ET.Element) -> dict[str, int]:
    """Map ServiceTask id → pruefidentifikator int value."""
    out: dict[str, int] = {}
    for task in proc.findall(_SERVICE_TASK_TAG):
        task_id = task.get("id")
        if task_id is None:
            continue
        for input_param in task.iter(_CAMUNDA_INPUT_TAG):
            if input_param.get("name") != PRUEFI_PARAM_NAME:
                continue
            if input_param.text is None:
                continue
            try:
                out[task_id] = int(input_param.text.strip())
            except ValueError:
                continue
            break
    return out


def _walk_back(
    start_id: str,
    incoming: dict[str, list[tuple[str, str | None]]],
) -> tuple[tuple[str, ...], ...]:
    """Return all distinct condition paths from start event to start_id.

    DFS backward through incoming edges. Accumulates condition expressions
    in reverse order; on terminal nodes (no incoming) emits the reversed
    accumulator as a forward-order path. Per-path `visited` set guards
    against malformed BPMN cycles.

    Returns a sorted tuple of distinct paths.
    Returns ((),) — a single empty path — for tasks reachable without
    any conditions. Returns () (empty outer tuple) if the task is
    unreachable, which should not happen on well-formed BPMN.
    """
    paths: set[tuple[str, ...]] = set()
    stack: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
        (start_id, (), frozenset())
    ]
    while stack:
        node, acc, visited = stack.pop()
        if node in visited:
            continue
        edges = incoming.get(node)
        if not edges:
            paths.add(tuple(reversed(acc)))
            continue
        new_visited = visited | {node}
        for source, condition in edges:
            new_acc = acc + (condition,) if condition is not None else acc
            stack.append((source, new_acc, new_visited))
    return tuple(sorted(paths))
