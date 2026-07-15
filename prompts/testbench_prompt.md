# RTL + Testbench Generation Stage

The accepted design specification and the frozen verification plan are
authoritative. Generate the DUT and a self-checking development testbench that
exercises every frozen `VP-*` requirement.

## Generate the DUT

- Produce one complete synthesizable module matching the design contract.
- Return a complete replacement module, not a patch.
- Call `write_verilog_file(module_name, code_str)`.

## Generate the testbench

Convert every `VP-*` requirement into one or more executable checks. The testbench
must:

- Instantiate the DUT using the exact validated module name and named port
  connections.
- Generate every required clock and apply reset per the validated synchrony and
  polarity.
- Sample sequential outputs only after non-blocking updates settle (avoid races).
- Use case equality/inequality (`===` / `!==`) so `x` and `z` values fail.
- Not rely on DUT internal state encoding or counter implementation.
- Print `RTL_AGENT_TEST_FAIL <VP-id>` on any failed check.
- Print `RTL_AGENT_TEST_PASS` exactly once, only after all required checks pass.
- Finish automatically via `$finish`. No GUI or waveform dependency.

## Action

Call `write_testbench_file(module_name, code_str, covered_requirements)` where
`covered_requirements` lists every frozen required `VP-*` id the testbench covers.
The controller rejects the call if any required id is missing or unknown, if the
module name differs from the spec, or if an unconditional pass path is detected.

After both files are written, call `run_simulation()` to compile and simulate.
Never add, delete, weaken, or reinterpret a frozen verification requirement.
