"""BPMN parsing primitives for the event-mapping generator (MACO-13040)."""

from scripts.bpmn.parser import ProcessEntry, PruefiEntry, parse_bpmn_xml

__all__ = ["ProcessEntry", "PruefiEntry", "parse_bpmn_xml"]
