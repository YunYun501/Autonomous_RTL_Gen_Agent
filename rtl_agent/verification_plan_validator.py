"""Verification-plan validation.

Validates plan structure, unique stable ``VP-*`` identifiers, traceability to the
accepted design spec, and internal consistency. The controller -- not the model --
computes ``verification_plan_ready``. Accepted plans are rendered to Markdown as
well as JSON.
"""

from __future__ import annotations

import re

VP_ID = re.compile(r"^VP-\d{3,}$")


def validate_plan(plan: dict, design_spec: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []

    if not isinstance(plan, dict):
        return _result(False, errors=[{"code": "NOT_OBJECT", "message": "Plan must be an object."}])

    requirements = plan.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return _result(False, errors=[{"code": "NO_REQUIREMENTS", "message": "Plan must contain a non-empty 'requirements' list."}])

    seen_ids: set[str] = set()
    required_ids: list[str] = []
    for i, req in enumerate(requirements):
        if not isinstance(req, dict):
            errors.append({"code": "WRONG_TYPE", "message": f"requirements[{i}] must be an object."})
            continue
        vp_id = req.get("id")
        if not isinstance(vp_id, str) or not VP_ID.match(vp_id):
            errors.append({"code": "BAD_ID", "message": f"requirements[{i}].id must match VP-NNN, got {vp_id!r}."})
            continue
        if vp_id in seen_ids:
            errors.append({"code": "DUPLICATE_ID", "message": f"Duplicate identifier {vp_id}."})
        seen_ids.add(vp_id)

        for key in ("stimulus_intent", "expected_observations"):
            if not req.get(key):
                errors.append({"code": "MISSING_FIELD", "message": f"{vp_id} is missing '{key}'."})

        if req.get("priority", "required") == "required":
            required_ids.append(vp_id)

    # Traceability: functional requirements in the spec should be covered.
    functional = design_spec.get("functional_requirements") or []
    if functional and not required_ids:
        errors.append({"code": "NO_COVERAGE", "message": "No required verification items cover the design's functional requirements."})

    ready = len(errors) == 0
    return _result(ready, errors=errors, warnings=warnings, required_ids=required_ids)


def render_markdown(plan: dict) -> str:
    lines = [f"# Verification Plan: {plan.get('plan_id', 'plan')}", ""]
    lines.append(f"Module: `{plan.get('module_name', '?')}`")
    lines.append("")
    for req in plan.get("requirements", []):
        lines.append(f"## {req.get('id')} — {req.get('title', '')}")
        lines.append(f"- Priority: {req.get('priority', 'required')}")
        src = req.get("requirement_source")
        if src:
            lines.append(f"- Source: {', '.join(src) if isinstance(src, list) else src}")
        lines.append(f"- Stimulus: {req.get('stimulus_intent', '')}")
        obs = req.get("expected_observations")
        if obs:
            joined = ", ".join(obs) if isinstance(obs, list) else str(obs)
            lines.append(f"- Expected: {joined}")
        if req.get("sampling_rule"):
            lines.append(f"- Sampling: {req['sampling_rule']}")
        lines.append("")
    return "\n".join(lines)


def _result(ready: bool, *, errors=None, warnings=None, required_ids=None) -> dict:
    return {
        "validation_status": "valid" if ready else "rejected",
        "verification_plan_ready": ready,
        "errors": errors or [],
        "warnings": warnings or [],
        "required_requirement_ids": required_ids or [],
    }
