"""Testbench traceability validation.

Compares a testbench's declared ``covered_requirements`` against the frozen list of
required ``VP-*`` identifiers, checks the module name matches the design spec, and
rejects obvious unconditional pass paths. This runs before the testbench is
accepted by ``write_testbench_file``.
"""

from __future__ import annotations

import re

FAIL_MARKER = "RTL_AGENT_TEST_FAIL"
PASS_MARKER = "RTL_AGENT_TEST_PASS"


def validate_testbench(
    module_name: str,
    code_str: str,
    covered_requirements: list[str],
    required_ids: list[str],
    spec_module_name: str | None,
) -> dict:
    errors: list[str] = []

    if not code_str or not code_str.strip():
        errors.append("Testbench source is empty.")

    if spec_module_name and module_name != spec_module_name:
        errors.append(
            f"Module name '{module_name}' does not match design spec '{spec_module_name}'."
        )

    covered = set(covered_requirements or [])
    required = set(required_ids or [])

    missing = sorted(required - covered)
    unknown = sorted(covered - required)
    if missing:
        errors.append(f"Missing required verification ids: {', '.join(missing)}.")
    if unknown:
        errors.append(f"Unknown verification ids declared: {', '.join(unknown)}.")

    # The DUT must be instantiated by name.
    if spec_module_name and spec_module_name not in code_str:
        errors.append(f"Testbench does not instantiate '{spec_module_name}'.")

    # Pass marker must exist and the fail marker must be reachable.
    if PASS_MARKER not in code_str:
        errors.append(f"Testbench never prints {PASS_MARKER}.")
    if FAIL_MARKER not in code_str:
        errors.append(
            f"Testbench never prints {FAIL_MARKER}; an unconditional pass path is not allowed."
        )

    # Reject an obvious unconditional pass: a pass marker with no comparison/if at all.
    if PASS_MARKER in code_str and not re.search(r"\bif\b", code_str):
        errors.append("Testbench prints the pass marker without any conditional checks.")

    return {
        "module_name": module_name,
        "required_requirement_ids": sorted(required),
        "covered_requirements": sorted(covered),
        "missing": missing,
        "unknown": unknown,
        "valid": len(errors) == 0,
        "errors": errors,
    }
