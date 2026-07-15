# Verification Planning Stage

Derive an abstract verification plan from the accepted design contract. This stage
defines WHAT evidence is required to demonstrate correctness. Do NOT generate any
Verilog or SystemVerilog testbench code in this stage.

## Instructions

- Derive verification requirements only from the accepted design contract.
- Give every required check a stable identifier such as `VP-001`, `VP-002`.
- For each requirement define: stimulus intent, observable behaviour, expected
  result, sampling/timing rule, boundary conditions, and pass criteria.
- Trace each requirement back to a field in the design spec via
  `requirement_source`.
- Cover reset, normal operation, boundaries, transitions, repeated operation, and
  relevant corner cases. Ensure exactly one valid output condition where mutual
  exclusion is required.
- Avoid implementation-specific checks (state encodings, internal counters) unless
  the design specification explicitly requires them. Verify observable behaviour.
- Mark each item `required` or `informational`.

## Action

Call `save_verification_plan` with the complete plan. The controller validates
uniqueness of `VP-*` ids, traceability, and completeness, and computes
`verification_plan_ready`. If it returns errors, correct them and resubmit. Never
generate HDL testbench code during this stage.
