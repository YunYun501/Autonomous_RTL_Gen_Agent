"""Three-layer local validation of a design specification.

Layer 1 -- schema: required keys, types, enums, identifier syntax, no empty strings.
Layer 2 -- engineering semantics: unique ports, clock/reset refer to inputs,
           polarity/edge agreement, outputs have behaviour.
Layer 3 -- readiness & risk: no unresolved high-risk field, medium inference only
           where the context allows, provenance recorded.

The controller -- not the model -- owns the ``ready_for_generation`` outcome.
"""

from __future__ import annotations

from . import design_spec_schema as schema
from .risk_policy import decide_missing_field, lookup_field_risk


def _has_meaningful_value(raw) -> bool:
    value = schema.unwrap(raw)
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    if isinstance(value, list) and len(value) == 0:
        # Empty list means "confirmed not to apply" -- meaningful for list fields.
        return True
    return True


def validate_specification(spec: dict, assessment_context: str) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    accepted_inferences: list[dict] = []
    rejected_inferences: list[dict] = []
    clarification_questions: list[dict] = []

    # ---- Layer 1: schema -------------------------------------------------
    if not isinstance(spec, dict):
        return _result(
            "model_repair_required",
            False,
            False,
            False,
            errors=[{"path": "$", "code": "NOT_OBJECT", "message": "Specification must be an object."}],
        )

    for field in schema.TOP_LEVEL_FIELDS:
        if field not in spec:
            errors.append(
                {"path": field, "code": "MISSING_FIELD", "message": f"Required top-level field '{field}' is absent."}
            )

    for field in schema.LIST_FIELDS:
        if field in spec and not isinstance(spec[field], list):
            errors.append(
                {"path": field, "code": "WRONG_TYPE", "message": f"'{field}' must be a list."}
            )

    # Empty strings are rejected outright for scalar fields.
    for field in ("module_name", "language_standard", "design_kind"):
        if isinstance(spec.get(field), str) and spec[field].strip() == "":
            errors.append(
                {"path": field, "code": "EMPTY_STRING", "message": f"'{field}' must not be an empty string; use null."}
            )

    if not schema.unwrap(spec.get("original_request")):
        errors.append(
            {"path": "original_request", "code": "MISSING_REQUEST", "message": "original_request must be preserved verbatim."}
        )

    # Port-level schema checks.
    for i, port in enumerate(spec.get("ports", []) or []):
        if not isinstance(port, dict):
            errors.append({"path": f"ports[{i}]", "code": "WRONG_TYPE", "message": "Port must be an object."})
            continue
        name = schema.unwrap(port.get("name"))
        if name is not None and not schema.is_valid_identifier(name):
            errors.append({"path": f"ports[{i}].name", "code": "BAD_IDENTIFIER", "message": f"Invalid Verilog identifier: {name!r}."})
        direction = schema.unwrap(port.get("direction"))
        if direction is not None and direction not in schema.VALID_DIRECTIONS:
            errors.append({"path": f"ports[{i}].direction", "code": "INVALID_ENUM", "message": "Expected input, output, or inout."})

    # Enum checks for clocking / resets.
    for i, clk in enumerate(spec.get("clocking", []) or []):
        edge = schema.unwrap(clk.get("edge")) if isinstance(clk, dict) else None
        if edge is not None and edge not in schema.VALID_EDGES:
            errors.append({"path": f"clocking[{i}].edge", "code": "INVALID_ENUM", "message": "Expected posedge or negedge."})
    for i, rst in enumerate(spec.get("resets", []) or []):
        if not isinstance(rst, dict):
            continue
        pol = schema.unwrap(rst.get("polarity"))
        if pol is not None and pol not in schema.VALID_POLARITIES:
            errors.append({"path": f"resets[{i}].polarity", "code": "INVALID_ENUM", "message": "Expected active_high or active_low."})
        syn = schema.unwrap(rst.get("synchrony"))
        if syn is not None and syn not in schema.VALID_SYNCHRONY:
            errors.append({"path": f"resets[{i}].synchrony", "code": "INVALID_ENUM", "message": "Expected synchronous or asynchronous."})

    schema_valid = len(errors) == 0
    if not schema_valid:
        return _result("model_repair_required", False, False, False, errors=errors)

    # ---- Layer 2: engineering semantics ----------------------------------
    module_name = schema.unwrap(spec.get("module_name"))
    if module_name is not None and not schema.is_valid_identifier(module_name):
        errors.append({"path": "module_name", "code": "BAD_IDENTIFIER", "message": f"Invalid module name: {module_name!r}."})

    port_names = [schema.unwrap(p.get("name")) for p in (spec.get("ports") or []) if isinstance(p, dict)]
    seen = set()
    for name in port_names:
        if name is None:
            continue
        if name in seen:
            errors.append({"path": "ports", "code": "DUPLICATE_PORT", "message": f"Duplicate port name: {name}."})
        seen.add(name)
    input_names = {
        schema.unwrap(p.get("name"))
        for p in (spec.get("ports") or [])
        if isinstance(p, dict) and schema.unwrap(p.get("direction")) == "input"
    }

    for i, clk in enumerate(spec.get("clocking", []) or []):
        sig = schema.unwrap(clk.get("signal")) if isinstance(clk, dict) else None
        if sig is not None and port_names and sig not in input_names:
            errors.append({"path": f"clocking[{i}].signal", "code": "UNDECLARED_SIGNAL", "message": f"Clock '{sig}' is not a declared input port."})
    for i, rst in enumerate(spec.get("resets", []) or []):
        if not isinstance(rst, dict):
            continue
        sig = schema.unwrap(rst.get("signal"))
        if sig is not None and port_names and sig not in input_names:
            errors.append({"path": f"resets[{i}].signal", "code": "UNDECLARED_SIGNAL", "message": f"Reset '{sig}' is not a declared input port."})

    semantics_valid = len(errors) == 0
    if not semantics_valid:
        return _result("model_repair_required", True, False, False, errors=errors)

    # ---- Layer 3: readiness & risk ---------------------------------------
    # Evaluate required interface fields that are still missing.
    for field, present in _required_field_presence(spec).items():
        if present:
            continue
        action = decide_missing_field(field, assessment_context, can_be_derived=False)
        risk = lookup_field_risk(field, assessment_context)
        if action in ("infer_and_record", "infer_with_warning"):
            entry = {"field": field, "risk": risk, "action": action}
            accepted_inferences.append(entry)
            if action == "infer_with_warning":
                warnings.append({"field": field, "message": f"Inferred conventional default for medium-risk field '{field}'."})
        elif action == "ask_user":
            clarification_questions.append(
                {"field": field, "question": _question_for(field),
                 "options": _options_for(field), "critical": True}
            )
        # controller_owned / derive handled elsewhere.

    # Guard: the model must not label a locally high-risk field as safely inferable.
    for port in spec.get("ports", []) or []:
        for key in ("direction", "width"):
            if isinstance(port, dict) and schema.source_of(port.get(key)) == "inferred":
                risk = lookup_field_risk(f"ports.*.{key}", assessment_context)
                if risk == "high":
                    rejected_inferences.append({"field": f"ports.*.{key}", "reason": "Model inferred a locally high-risk field."})
                    clarification_questions.append({"field": f"ports.*.{key}", "question": _question_for(f"ports.*.{key}"),
                                                    "options": _options_for(f"ports.*.{key}"), "critical": True})

    ready = len(clarification_questions) == 0 and len(rejected_inferences) == 0
    if not ready:
        return _result(
            "user_clarification_required",
            True,
            True,
            False,
            warnings=warnings,
            clarification_questions=clarification_questions,
            rejected_inferences=rejected_inferences,
            accepted_inferences=accepted_inferences,
        )

    status = "valid_with_inferences" if accepted_inferences else "valid"
    return _result(
        status,
        True,
        True,
        True,
        warnings=warnings,
        accepted_inferences=accepted_inferences,
        normalized=spec,
    )


def _required_field_presence(spec: dict) -> dict[str, bool]:
    """Map the interface fields that gate readiness to whether they are present."""
    presence = {
        "module_name": _has_meaningful_value(spec.get("module_name")),
        "functional_requirements": bool(spec.get("functional_requirements")),
    }
    for i, rst in enumerate(spec.get("resets", []) or []):
        if isinstance(rst, dict):
            for key in ("polarity", "synchrony", "reset_values"):
                presence[f"resets[{i}].{key}"] = _has_meaningful_value(rst.get(key))
    return presence


def _question_for(field: str) -> str:
    questions = {
        "module_name": "What module name should the design use?",
        "functional_requirements": "What is the required functional behaviour?",
    }
    if field.endswith(".polarity"):
        return "Should reset be active-high or active-low?"
    if field.endswith(".synchrony"):
        return "Should reset be synchronous or asynchronous?"
    if field.endswith(".reset_values"):
        return "What values should outputs take on reset?"
    if field.endswith(".direction"):
        return "What is the direction (input/output/inout) of this port?"
    if field.endswith(".width"):
        return "What is the bit width of this port?"
    return questions.get(field, f"Please clarify '{field}'.")


def _options_for(field: str) -> list[str]:
    """Selectable choices for categorical clarifications; [] means free text."""
    if field.endswith(".polarity"):
        return ["active_low", "active_high"]
    if field.endswith(".synchrony"):
        return ["asynchronous", "synchronous"]
    if field.endswith(".direction"):
        return ["input", "output", "inout"]
    return []


def _result(
    status: str,
    schema_valid: bool,
    semantics_valid: bool,
    ready: bool,
    *,
    errors=None,
    warnings=None,
    accepted_inferences=None,
    rejected_inferences=None,
    clarification_questions=None,
    normalized=None,
) -> dict:
    return {
        "validation_status": status,
        "schema_valid": schema_valid,
        "semantics_valid": semantics_valid,
        "ready_for_generation": ready,
        "errors": errors or [],
        "warnings": warnings or [],
        "accepted_inferences": accepted_inferences or [],
        "rejected_inferences": rejected_inferences or [],
        "derived_values": [],
        "clarification_questions": clarification_questions or [],
        "normalized_specification": normalized or {},
    }
