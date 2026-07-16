# RTL Generation Stage (External Testbench)

A real, user-provided testbench is authoritative. You must generate ONLY the DUT.
You must not generate, write, or modify any testbench, and there is no verification
plan in this mode.

## Rules

- The provided testbench defines the exact interface: the module name it
  instantiates and the exact port names and directions. Your module MUST match
  them exactly, or it will not compile.
- Produce one complete synthesizable module consistent with both the accepted
  design specification and the provided testbench.
- Return a complete replacement module, not a patch.
- Do not add ports the testbench does not connect, and do not rename ports.

## Action

1. Call `write_verilog_file(module_name, code_str)` with the complete DUT, using
   the exact module name the testbench instantiates.
2. Call `run_simulation()` to compile and run the DUT against the provided
   testbench.

The tools `write_testbench_file` and `save_verification_plan` are unavailable in
this mode and will be rejected. Use only the real testbench for verification.
