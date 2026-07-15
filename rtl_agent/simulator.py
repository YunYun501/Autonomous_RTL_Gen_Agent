"""Icarus Verilog compile-and-simulate wrapper.

Runs the compiler and runtime as separate subprocess stages with explicit argument
lists (never a shell string), timeouts, and full output capture. Pass requires the
``RTL_AGENT_TEST_PASS`` marker and absence of ``RTL_AGENT_TEST_FAIL`` -- a zero exit
code alone is insufficient.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .config import Config
from .testbench_validator import FAIL_MARKER, PASS_MARKER

COMPILE_TIMEOUT = 60.0
SIM_TIMEOUT = 60.0


def run_simulation(
    config: Config,
    work_dir: Path,
    dut_path: Path,
    tb_path: Path,
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
    if FAIL_MARKER in stdout:
        result["failure_type"] = "functional_failure"
    elif PASS_MARKER not in stdout:
        result["failure_type"] = "missing_pass_marker"
    else:
        result["passed"] = True

    return result
