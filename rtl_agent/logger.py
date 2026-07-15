"""Run workspace and logging.

Creates one isolated run directory per natural-language request, snapshots the
frozen prompts and their hashes, records every DeepSeek API call in JSONL, versions
generated source files, and maintains a human-readable ``run_log.txt``. The API key
must never reach these artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import textwrap
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT

RUNS_DIR = PROJECT_ROOT / "runs"

_WIDTH = 80  # section-rule width / text-wrap column


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(text: str, max_len: int = 32) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return (slug or "task")[:max_len]


class RunContext:
    """Owns the on-disk artifacts for a single task run."""

    def __init__(self, request: str, module_hint: str = "task"):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        name = f"{timestamp}_{_slug(module_hint)}"
        self.dir = RUNS_DIR / name
        self.dir.mkdir(parents=True, exist_ok=True)

        (self.dir / "prompts").mkdir(exist_ok=True)
        (self.dir / "rtl_versions").mkdir(exist_ok=True)
        (self.dir / "testbench_versions").mkdir(exist_ok=True)
        (self.dir / "simulation_logs").mkdir(exist_ok=True)

        self.request = request
        self.api_log_path = self.dir / "api_calls.jsonl"
        self.run_log_path = self.dir / "run_log.txt"
        self._rtl_attempts = 0
        self._tb_attempts = 0
        self._sim_attempts = 0

    # -- prompt snapshotting ------------------------------------------------
    def snapshot_prompts(self, prompts: dict[str, str]) -> dict[str, str]:
        hashes = {}
        dest_dir = self.dir / "prompts"
        for name, content in prompts.items():
            (dest_dir / name).write_text(content, encoding="utf-8")
            hashes[name] = sha256_text(content)
        (self.dir / "prompt_hashes.json").write_text(
            json.dumps(hashes, indent=2), encoding="utf-8"
        )
        return hashes

    def ensure(self) -> None:
        """Recreate the run directory tree if it was removed while a task runs.

        Makes writes resilient to the run directory disappearing mid-task (e.g. an
        external cleanup) so a single filesystem hiccup never aborts the task or
        loses generated RTL.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        for sub in ("prompts", "rtl_versions", "testbench_versions", "simulation_logs"):
            (self.dir / sub).mkdir(exist_ok=True)

    # -- source versioning --------------------------------------------------
    def save_rtl_version(self, code: str) -> Path:
        self.ensure()
        self._rtl_attempts += 1
        path = self.dir / "rtl_versions" / f"attempt_{self._rtl_attempts:02d}.v"
        path.write_text(code, encoding="utf-8")
        return path

    def save_testbench_version(self, code: str) -> Path:
        self.ensure()
        self._tb_attempts += 1
        path = self.dir / "testbench_versions" / f"attempt_{self._tb_attempts:02d}.v"
        path.write_text(code, encoding="utf-8")
        return path

    def save_simulation_log(self, result: dict) -> Path:
        self.ensure()
        self._sim_attempts += 1
        path = self.dir / "simulation_logs" / f"attempt_{self._sim_attempts:02d}.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return path

    # -- generic artifact + json -------------------------------------------
    def write_json(self, filename: str, data: dict) -> Path:
        self.ensure()
        path = self.dir / filename
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    # -- low-level writers --------------------------------------------------
    def _write(self, text: str) -> None:
        # Auxiliary: never let a logging failure crash the task.
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.run_log_path.open("a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def log(self, line: str) -> None:
        """A simple timestamped one-liner (kept for incidental notes)."""
        self._write(f"[{_now()}] {line}\n")

    def log_api_call(self, record: dict) -> None:
        record = {"timestamp": datetime.now().isoformat(), **record}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.api_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # -- structured transcript ---------------------------------------------
    def log_task_header(
        self,
        request: str,
        masked_key: str,
        iverilog: str,
        vvp: str,
        prompts: dict[str, str],
        hashes: dict[str, str],
    ) -> None:
        self._write(
            _rule("=")
            + "\n RTL AGENT - EXECUTION LOG\n"
            + _rule("=")
            + "\n"
            + f"Started       : {_now(full=True)}\n"
            + f"Run directory : {self.dir}\n"
            + "Model         : deepseek-v4-pro (thinking: enabled, reasoning_effort: max)\n"
            + f"iverilog      : {iverilog}\n"
            + f"vvp           : {vvp}\n"
            + f"API key       : {masked_key}\n"
        )
        self._sub("ORIGINAL REQUEST")
        self._write(_wrap(request) + "\n")
        sys_hash = hashes.get("system_prompt.md", "?")
        self._sub(f"SYSTEM PROMPT  (constant for the task; sha256 {sys_hash[:16]}...)")
        self._write(_wrap(prompts.get("system_prompt.md", "")) + "\n")
        self._sub("FROZEN PROMPT VERSIONS (sha256)")
        for name, digest in hashes.items():
            self._write(f"  {name:<26} {digest}\n")

    def log_stage(self, stage: str, instruction: str) -> None:
        self._write(f"\n\n{_rule('=')}\n [{_now()}]  STAGE: {stage}\n{_rule('=')}\n")
        self._sub("USER MESSAGE  (stage instruction prompt sent to the model)")
        self._write(_wrap(instruction) + "\n")

    def log_assistant_turn(self, turn: int, stage: str, message) -> None:
        reasoning = getattr(message, "reasoning_content", None)
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None)

        self._write(
            f"\n{_rule('-')}\n [{_now()}]  ASSISTANT  -  turn {turn}  (stage: {stage})\n{_rule('-')}\n"
        )
        self._write("\nTHINKING  (reasoning_content):\n\n")
        self._write(_wrap(reasoning or "(none)", indent="  ") + "\n")
        self._write("\nRESPONSE  (content):\n\n")
        self._write(_wrap(content or "(none)", indent="  ") + "\n")
        if tool_calls:
            self._write("\nTOOL CALLS REQUESTED:\n")
            for i, call in enumerate(tool_calls, 1):
                self._write(f"\n  [{i}] {call.function.name}\n")
                self._write("      parameters:\n")
                self._write(_indent(_readable(_parse(call.function.arguments)), "      ") + "\n")

    def log_tool_result(self, name: str, result) -> None:
        self._write(f"\n{_rule('-')}\n [{_now()}]  TOOL RESULT  -  {name}\n{_rule('-')}\n\n")
        if name == "run_simulation" and isinstance(result, dict):
            self._write(_format_simulation(result) + "\n")
        else:
            self._write(_readable(result) + "\n")

    def log_clarification(self, questions: list[dict], answer: str) -> None:
        self._write(f"\n{_rule('-')}\n [{_now()}]  USER CLARIFICATION\n{_rule('-')}\n")
        self._write("\nQuestions asked:\n")
        for q in questions:
            self._write(f"  - {q.get('field')}: {q.get('question')}\n")
            if q.get("options"):
                self._write(f"      options: {q['options']}\n")
        self._write("\nUser answered:\n\n")
        self._write(_wrap(answer or "(no answer / skipped)", indent="  ") + "\n")

    def log_reflection(self, cycle: int, max_cycles: int, diagnosis: str) -> None:
        self._write(
            f"\n\n{_rule('=')}\n [{_now()}]  REFLECTION CYCLE {cycle}/{max_cycles}\n{_rule('=')}\n"
        )
        self._write(f"Failure diagnosis: {diagnosis}\n")

    def log_attempt(self, attempt: int, max_attempts: int, passed: bool, failure_type) -> None:
        verdict = "PASS" if passed else f"FAIL ({failure_type})"
        self._write(f"\n>>> SIMULATION ATTEMPT {attempt}/{max_attempts}: {verdict}\n")

    def log_final(
        self,
        status: str,
        attempts: int,
        reflections: int,
        detail: str,
        dut_path=None,
        tb_path=None,
    ) -> None:
        self._write(f"\n\n{_rule('=')}\n FINAL RESULT\n{_rule('=')}\n")
        self._write(f"Status              : {status}\n")
        self._write(f"Simulation attempts : {attempts}\n")
        self._write(f"Reflection cycles   : {reflections}\n")
        self._write(f"Detail              : {detail}\n")
        if dut_path:
            self._write(f"Final RTL           : {dut_path}\n")
        if tb_path:
            self._write(f"Final testbench     : {tb_path}\n")
        self._write(f"Ended               : {_now(full=True)}\n")
        self._write(_rule("=") + "\n")

    def _sub(self, title: str) -> None:
        self._write(f"\n{_rule('-')}\n {title}\n{_rule('-')}\n")


# ---- formatting helpers -------------------------------------------------------

def _now(full: bool = False) -> str:
    fmt = "%Y-%m-%d %H:%M:%S" if full else "%H:%M:%S"
    return datetime.now().strftime(fmt)


def _rule(char: str = "=") -> str:
    return char * _WIDTH


def _wrap(text: str, indent: str = "", width: int = _WIDTH) -> str:
    """Wrap prose to a readable column while preserving existing line breaks
    (blank lines stay blank so paragraphs keep their gaps)."""
    if text is None:
        return indent + "(none)"
    out = []
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            out.append("")
            continue
        out.append(
            textwrap.fill(
                line,
                width=width,
                initial_indent=indent,
                subsequent_indent=indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(out)


def _indent(text: str, prefix: str) -> str:
    return "\n".join((prefix + ln if ln else ln) for ln in str(text).split("\n"))


def _parse(arguments):
    if isinstance(arguments, (dict, list)):
        return arguments
    try:
        return json.loads(arguments or "")
    except (ValueError, TypeError):
        return {"(unparsed arguments)": arguments}


def _readable(obj, depth: int = 0) -> str:
    """Render a JSON-ish object as readable, indented, YAML-like text.

    Multi-line / long string values (e.g. generated code) are shown as verbatim
    indented blocks rather than escaped one-liners, so code stays code."""
    pad = "  " * depth
    lines: list[str] = []

    if isinstance(obj, dict):
        if not obj:
            return f"{pad}(empty)"
        for key, val in obj.items():
            lines.append(_render_kv(str(key), val, depth))
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return f"{pad}(empty list)"
        for i, item in enumerate(obj):
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}[{i}]:")
                lines.append(_readable(item, depth + 1))
            else:
                lines.append(f"{pad}- {item}")
        return "\n".join(lines)
    return f"{pad}{obj}"


def _render_kv(key: str, val, depth: int) -> str:
    pad = "  " * depth
    if isinstance(val, (dict, list)):
        inner = _readable(val, depth + 1)
        return f"{pad}{key}:\n{inner}"
    if isinstance(val, str) and ("\n" in val or len(val) > 100):
        block = _indent(val.replace("\r\n", "\n"), pad + "  | ")
        return f"{pad}{key}:\n{block}"
    return f"{pad}{key}: {val}"


def _format_simulation(r: dict) -> str:
    lines = [
        f"compile succeeded  : {r.get('compile_succeeded')}  (return code {r.get('compile_return_code')})",
        f"simulation started : {r.get('simulation_started')}  (return code {r.get('simulation_return_code')})",
        f"timed out          : {r.get('timed_out')}",
        f"passed             : {r.get('passed')}",
        f"failure type       : {r.get('failure_type')}",
    ]
    if r.get("compile_command"):
        lines.append(f"compile command    : {r.get('compile_command')}")
    if r.get("simulation_command"):
        lines.append(f"simulate command   : {r.get('simulation_command')}")
    for label, key in (
        ("COMPILE STDOUT", "compile_stdout"),
        ("COMPILE STDERR", "compile_stderr"),
        ("SIMULATION STDOUT", "simulation_stdout"),
        ("SIMULATION STDERR", "simulation_stderr"),
    ):
        val = (r.get(key) or "").strip()
        if val:
            lines.append(f"\n--- {label} ---")
            lines.append(_indent(val, "  "))
    return "\n".join(lines)
