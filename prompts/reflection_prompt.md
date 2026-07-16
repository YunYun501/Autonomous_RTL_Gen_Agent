# Reflection Stage

The most recent development simulation failed. Diagnose the failure and produce a
corrected complete design.

## Instructions

- Read the current design contract, frozen verification plan, RTL, testbench, and
  the full compiler and simulator logs (use `read_current_design`).
- Classify the failure before editing: distinguish an RTL defect from a testbench
  defect from an infrastructure failure.
- Return complete corrected source rather than partial patches.
- Preserve the original user requirements and the frozen verification plan.
- Modify the testbench ONLY when it is mechanically defective (does not compile,
  wrong port name, clock/timing bug, hangs, or cannot execute a frozen case).
- Never delete, weaken, or reverse a valid expected result. Never remove a frozen
  `VP-*` requirement. Never print the pass marker without executing all checks.
- External testbench mode: if a real testbench was provided, it is authoritative
  and read-only. Only correct the DUT; never modify, replace, or regenerate the
  testbench (the `write_testbench_file` tool is unavailable in this mode).

## Action

Call `write_verilog_file` (and `write_testbench_file` only if the testbench is
mechanically broken), then call `run_simulation()` again. If you repair the
testbench, it must still cover every frozen required `VP-*` id.
