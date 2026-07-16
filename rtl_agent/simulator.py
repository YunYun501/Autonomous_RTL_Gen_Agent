"""Icarus Verilog compile-and-simulate wrapper.

Runs the compiler and runtime as separate subprocess stages with explicit argument
lists (never a shell string), timeouts, and full output capture.

For the agent's own development testbench, a pass requires the
``RTL_AGENT_TEST_PASS`` marker and absence of ``RTL_AGENT_TEST_FAIL``. When a real
(external) testbench is supplied it will not print those markers, so ``external=True``
switches to an output heuristic instead.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .config import Config
from .testbench_validator import FAIL_MARKER, PASS_MARKER

COMPILE_TIMEOUT = 60.0
SIM_TIMEOUT = 60.0

# Heuristic tokens for judging a real testbench's output (case-insensitive).
_EXT_FAIL = re.compile(
    r"\b(error|fail(ed|ure)?|mismatch|assert\w*|fatal|incorrect|wrong|violation|bad)\b",
    re.IGNORECASE,
)
_EXT_PASS = re.compile(
    r"\b(success|succeeded|passed|pass\b|all\s+tests?\s+pass\w*|test\s+passed|ok\b)\b",
    re.IGNORECASE,
)
# Benign phrases that contain a "fail" word but indicate success; stripped first.
_EXT_BENIGN = re.compile(
    r"\b(no|0|zero|without|and)\s+(errors?|failures?|mismatches?)\b", re.IGNORECASE
)


def run_simulation(
    config: Config,
    work_dir: Path,
    dut_path: Path,
    tb_path: Path,
    external: bool = False,
) -> dict:
    result = {
        "compile_succeeded": False,
        "compile_return_code": None,
        "compile_stdout": "",
        "compile_stderr": "",
        "compile_command": "",
        "simulation_started": False,
        "simulation_return_code": None,
        "simulation_stdout": "",
        "simulation_stderr": "",
        "timed_out": False,
        "passed": False,
        "failure_type": None,
    }

    vvp_out = work_dir / "simulation.vvp"
    if vvp_out.exists():
        try:
            vvp_out.unlink()
        except OSError:
            pass

    if not dut_path.exists() or not tb_path.exists():
        result["failure_type"] = "file_system_error"
        result["compile_stderr"] = "DUT or testbench file is missing."
        return result

    compile_cmd = [
        config.iverilog_path,
        "-g2012",
        "-o",
        str(vvp_out),
        str(tb_path),
        str(dut_path),
    ]
    result["compile_command"] = " ".join(compile_cmd)

    start = time.time()
    try:
        proc = subprocess.run(
            compile_cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["failure_type"] = "compile_timeout"
        return result
    except OSError as exc:
        result["failure_type"] = "compile_error"
        result["compile_stderr"] = str(exc)
        return result

    result["compile_return_code"] = proc.returncode
    result["compile_stdout"] = proc.stdout
    result["compile_stderr"] = proc.stderr
    result["compile_duration_s"] = round(time.time() - start, 3)

    if proc.returncode != 0:
        result["failure_type"] = "compile_error"
        return result
    result["compile_succeeded"] = True

    sim_cmd = [config.vvp_path, str(vvp_out)]
    result["simulation_command"] = " ".join(sim_cmd)
    start = time.time()
    try:
        sim = subprocess.run(
            sim_cmd, capture_output=True, text=True, timeout=SIM_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["failure_type"] = "simulation_timeout"
        return result
    except OSError as exc:
        result["failure_type"] = "simulation_error"
        result["simulation_stderr"] = str(exc)
        return result

    result["simulation_started"] = True
    result["simulation_return_code"] = sim.returncode
    result["simulation_stdout"] = sim.stdout
    result["simulation_stderr"] = sim.stderr
    result["simulation_duration_s"] = round(time.time() - start, 3)

    stdout = sim.stdout or ""
    if external:
        passed, failure_type = _external_verdict(stdout, sim.stderr or "", sim.returncode)
        result["passed"] = passed
        result["failure_type"] = failure_type
    elif FAIL_MARKER in stdout:
        result["failure_type"] = "functional_failure"
    elif PASS_MARKER not in stdout:
        result["failure_type"] = "missing_pass_marker"
    else:
        result["passed"] = True

    return result


def _external_verdict(stdout: str, stderr: str, return_code: int):
    """Judge a real testbench's result from its output (no agent markers).

    Priority: a non-zero exit code or any failure keyword -> fail; otherwise, an
    explicit success keyword or a clean run -> pass. Benign phrases like
    "0 errors" are neutralized first so they are not read as failures.
    """
    if return_code not in (0, None):
        return False, "functional_failure"

    combined = f"{stdout}\n{stderr}"
    neutralized = _EXT_BENIGN.sub(" ok ", combined)
    has_fail = bool(_EXT_FAIL.search(neutralized))
    has_pass = bool(_EXT_PASS.search(combined))

    if has_fail:
        return False, "functional_failure"
    # No failure signal: an explicit success token or a clean run both pass.
    return True, None
