"""Authoritative local field-risk policy.

DeepSeek proposes values; this registry -- not the model -- determines the
authoritative risk for a missing field. The model must never downgrade a field's
risk. Lookups support ``*`` wildcards for list-element paths such as
``ports.*.width``.
"""

from __future__ import annotations

CONTROLLER_OWNED = "controller_owned"

# Baseline policy from project_plan.md section 8.1.5. Values are the required risk
# for an *omitted* field, keyed by assessment context.
FIELD_RISK_POLICY: dict[str, dict[str, str]] = {
    "original_request": {"self_verified": "high", "external_testbench": "high"},
    "module_name": {"self_verified": "medium", "external_testbench": "high"},
    "language_standard": {"self_verified": "low", "external_testbench": "low"},
    "design_kind": {"self_verified": "low", "external_testbench": "low"},
    "assessment_context": {
        "self_verified": CONTROLLER_OWNED,
        "external_testbench": CONTROLLER_OWNED,
    },
    "ports.*.name": {"self_verified": "medium", "external_testbench": "high"},
    "ports.*.direction": {"self_verified": "medium", "external_testbench": "high"},
    "ports.*.width": {"self_verified": "medium", "external_testbench": "high"},
    "ports.*.signed": {"self_verified": "medium", "external_testbench": "high"},
    "clocking.*.signal": {"self_verified": "medium", "external_testbench": "high"},
    "clocking.*.edge": {"self_verified": "medium", "external_testbench": "high"},
    "resets.*.signal": {"self_verified": "high", "external_testbench": "high"},
    "resets.*.polarity": {"self_verified": "high", "external_testbench": "high"},
    "resets.*.synchrony": {"self_verified": "high", "external_testbench": "high"},
    "resets.*.reset_values": {"self_verified": "high", "external_testbench": "high"},
    "functional_requirements": {"self_verified": "high", "external_testbench": "high"},
    "timing_requirements.*.duration": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "timing_requirements.*.reference_clock": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
}

# Fields that fall back to this when not explicitly registered.
DEFAULT_RISK = {"self_verified": "medium", "external_testbench": "high"}


def _normalize(field_path: str) -> str:
    """Replace numeric list indices with ``*`` so ports[0].width -> ports.*.width."""
    parts = []
    for token in field_path.replace("[", ".").replace("]", "").split("."):
        token = token.strip()
        if not token:
            continue
        parts.append("*" if token.isdigit() else token)
    return ".".join(parts)


def lookup_field_risk(field_path: str, assessment_context: str) -> str:
    key = _normalize(field_path)
    policy = FIELD_RISK_POLICY.get(key, DEFAULT_RISK)
    return policy.get(assessment_context, policy.get("self_verified", "medium"))


def decide_missing_field(
    field_path: str,
    assessment_context: str,
    can_be_derived: bool,
) -> str:
    """Return an action for a missing field: derive, infer, or ask the user."""
    if can_be_derived:
        return "derive_and_record"

    risk = lookup_field_risk(field_path, assessment_context)

    if risk == CONTROLLER_OWNED:
        return "controller_owned"
    if risk == "low":
        return "infer_and_record"
    if risk == "medium":
        if assessment_context == "self_verified":
            return "infer_with_warning"
        return "ask_user"
    return "ask_user"
