"""Normalized design-specification schema.

Defines the top-level fields, permitted enumerations, and identifier rules. The
schema deliberately keeps provenance-carrying fields flexible: DeepSeek may submit
a plain value or a ``{"value": ..., "source": ..., "inference": ...}`` object.
"""

from __future__ import annotations

import re

VERILOG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

TOP_LEVEL_FIELDS = (
    "original_request",
    "assessment_context",
    "module_name",
    "language_standard",
    "design_kind",
    "ports",
    "parameters",
    "clocking",
    "resets",
    "functional_requirements",
    "timing_requirements",
    "verification_requirements",
    "assumptions",
    "unresolved_questions",
)

LIST_FIELDS = (
    "ports",
    "parameters",
    "clocking",
    "resets",
    "functional_requirements",
    "timing_requirements",
    "verification_requirements",
    "assumptions",
    "unresolved_questions",
)

VALID_DIRECTIONS = {"input", "output", "inout"}
VALID_SOURCES = {"explicit", "derived", "inferred", "controller_default"}
VALID_ASSESSMENT_CONTEXTS = {"self_verified", "external_testbench", "unknown"}
VALID_EDGES = {"posedge", "negedge"}
VALID_POLARITIES = {"active_high", "active_low"}
VALID_SYNCHRONY = {"synchronous", "asynchronous"}


def unwrap(value):
    """Return the plain value, whether wrapped in a provenance object or not."""
    if isinstance(value, dict) and "value" in value and "source" in value:
        return value["value"]
    return value


def source_of(value):
    if isinstance(value, dict) and "source" in value:
        return value.get("source")
    return None


def is_valid_identifier(name) -> bool:
    return isinstance(name, str) and bool(VERILOG_IDENTIFIER.match(name))


def empty_specification(original_request: str, assessment_context: str) -> dict:
    """A fully-populated skeleton with every top-level field present."""
    return {
        "original_request": original_request,
        "assessment_context": assessment_context,
        "module_name": None,
        "language_standard": None,
        "design_kind": None,
        "ports": [],
        "parameters": [],
        "clocking": [],
        "resets": [],
        "functional_requirements": [],
        "timing_requirements": [],
        "verification_requirements": [],
        "assumptions": [],
        "unresolved_questions": [],
    }
