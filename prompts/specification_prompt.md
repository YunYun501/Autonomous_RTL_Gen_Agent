# Specification Stage

Translate the exact user request into a structured design contract. Your goal in
this stage is ONLY to produce and submit that contract through `save_design_spec`.
Do not generate RTL or a testbench yet.

## Instructions

- Preserve the exact user request as the authoritative source of truth. Copy it
  verbatim into `original_request`.
- Extract the module interface and behaviour: module name, ports (name, direction,
  width, signedness), parameters, clocking, resets, and functional requirements.
- Identify clocking, reset, timing, state, width, and boundary requirements.
- Record assumptions separately from explicit requirements, in `assumptions`.
- Record open questions you could not resolve in `unresolved_questions`.
- Return a machine-readable design contract with every top-level field present.
  Use a concrete value when known, an empty list `[]` when a category confirmed
  not to apply, and `null` when a scalar value is genuinely unknown. Never use an
  empty string as a substitute for missing information.
- For any value you did not take verbatim from the user, record its provenance:
  `explicit`, `derived`, `inferred`, or `controller_default`, with an inference
  basis when applicable.
- Ask for clarification only when a missing detail materially changes the module
  interface or core behaviour. The controller decides whether clarification is
  actually required.

## Required top-level fields

`original_request`, `assessment_context`, `module_name`, `language_standard`,
`design_kind`, `ports`, `parameters`, `clocking`, `resets`,
`functional_requirements`, `timing_requirements`, `verification_requirements`,
`assumptions`, `unresolved_questions`.

Do NOT set `assessment_context` yourself beyond echoing the value the controller
supplies. Do NOT set or claim `ready_for_generation`.

## Action

Call `save_design_spec` with the complete structured specification. If the tool
returns `model_repair_required`, fix the reported fields and resubmit. If it
returns `user_clarification_required`, the controller will collect answers and
you will be asked to update the draft.
