"""Reflection loop counter semantics.

One initial generation + simulation, followed by at most five reflection cycles.
Therefore the maximum number of internal simulation attempts is six. Transient API
retries do not consume a reflection cycle because no RTL correction has occurred.
"""

MAX_REFLECTION_CYCLES = 5
MAX_SIMULATION_ATTEMPTS = 6


def summarize_failure(sim_result: dict) -> str:
    ftype = sim_result.get("failure_type") or "unknown"
    if ftype == "compile_error":
        stderr = (sim_result.get("compile_stderr") or "").strip().splitlines()
        return f"compile_error: {stderr[0] if stderr else ''}"
    if ftype == "functional_failure":
        for line in (sim_result.get("simulation_stdout") or "").splitlines():
            if "RTL_AGENT_TEST_FAIL" in line:
                return f"functional_failure: {line.strip()}"
        return "functional_failure"
    return ftype
