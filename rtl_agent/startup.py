"""Mandatory startup preflight.

Runs during first-time setup, on every start, after any configuration change and
when the user enters ``/doctor``. The main prompt must not open until all
mandatory checks pass.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, PROJECT_ROOT
from .prompt_loader import REQUIRED_PROMPTS, PROMPT_DIRECTORY

RUNS_DIR = PROJECT_ROOT / "runs"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / ".rtl-agent"

SMOKE_SOURCE = """module rtl_agent_smoke_test;
    initial begin
        $display("RTL_AGENT_SIMULATOR_READY");
        $finish;
    end
endmodule
"""


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class PreflightReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name, passed, detail))


def _run(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def check_executable(path: str, label: str) -> CheckResult:
    exe = Path(path)
    if not exe.exists():
        return CheckResult(label, False, f"Path does not exist: {path}")
    if not exe.is_file():
        return CheckResult(label, False, f"Not a file: {path}")
    proc = _run([path, "-V"])
    if proc is None:
        return CheckResult(label, False, f"Executable did not start: {path}")
    version = (proc.stdout or proc.stderr or "").strip().splitlines()
    first_line = version[0] if version else ""
    return CheckResult(label, True, f"{path}  {first_line}".strip())


def check_smoke_test(config: Config) -> CheckResult:
    """Compile and run a trivial module end-to-end through the configured tools."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "rtl_agent_smoke_test.v"
        out = tmp_path / "rtl_agent_smoke_test.vvp"
        src.write_text(SMOKE_SOURCE, encoding="utf-8")

        compile_proc = _run(
            [config.iverilog_path, "-o", str(out), str(src)]
        )
        if compile_proc is None or compile_proc.returncode != 0:
            detail = compile_proc.stderr.strip() if compile_proc else "compiler failed to start"
            return CheckResult("Compile-and-run smoke test", False, detail)

        run_proc = _run([config.vvp_path, str(out)])
        if run_proc is None or run_proc.returncode != 0:
            detail = run_proc.stderr.strip() if run_proc else "runtime failed to start"
            return CheckResult("Compile-and-run smoke test", False, detail)

        if "RTL_AGENT_SIMULATOR_READY" not in (run_proc.stdout or ""):
            return CheckResult(
                "Compile-and-run smoke test",
                False,
                "Expected marker not found in simulator output",
            )

    return CheckResult("Compile-and-run smoke test", True, "simulator ready")


def check_prompts() -> CheckResult:
    missing = []
    for name in REQUIRED_PROMPTS:
        path = PROMPT_DIRECTORY / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            missing.append(name)
    if missing:
        return CheckResult("Prompt files", False, f"Missing/empty: {', '.join(missing)}")
    return CheckResult("Prompt files", True, f"{len(REQUIRED_PROMPTS)} prompts present")


def check_writable_dirs() -> CheckResult:
    for directory in (RUNS_DIR, LOGS_DIR, CONFIG_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return CheckResult("Writable run directory", False, str(exc))
    return CheckResult("Writable run directory", True, "runs/, logs/, .rtl-agent/")


def check_api(config: Config, skip: bool = False) -> CheckResult:
    if skip:
        return CheckResult("DeepSeek API", True, "skipped")
    try:
        from .deepseek_client import DeepSeekClient

        client = DeepSeekClient(config.deepseek_api_key)
        ok = client.validate_key()
        if ok:
            return CheckResult("DeepSeek API", True, "deepseek-v4-pro accepted")
        return CheckResult("DeepSeek API", False, "unexpected response from model")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("DeepSeek API", False, str(exc))


def run_preflight(config: Config, skip_api: bool = False) -> PreflightReport:
    report = PreflightReport()
    report.add("Configuration loaded", True, "config.json present")
    report.results.append(check_executable(config.iverilog_path, "iverilog.exe"))
    report.results.append(check_executable(config.vvp_path, "vvp.exe"))
    report.results.append(check_smoke_test(config))
    report.results.append(check_api(config, skip=skip_api))
    report.results.append(check_prompts())
    report.results.append(check_writable_dirs())
    return report
