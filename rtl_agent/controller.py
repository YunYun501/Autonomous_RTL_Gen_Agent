"""Task controller.

Drives one natural-language request through the full pipeline: freeze prompts ->
design-spec validation -> verification-plan validation -> RTL + testbench
generation -> compile/simulate -> up to five reflection cycles. The controller,
not the model, enforces every gate and computes readiness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .deepseek_client import DeepSeekClient, assistant_message_to_dict
from .logger import RunContext
from .reflection import MAX_REFLECTION_CYCLES, MAX_SIMULATION_ATTEMPTS, summarize_failure
from .tools import ToolRegistry

MAX_TURNS_PER_STAGE = 12

AskUser = Callable[[list[dict]], str]
Progress = Callable[[str], None]

_EXTERNAL_SPEC_ADDENDUM = (
    "\n\n## External testbench mode\n"
    "A real testbench is provided above and is authoritative. Extract the module "
    "name and the exact port names, directions, and widths from how it instantiates "
    "the DUT, and make the specification match them exactly (mark these as explicit, "
    "sourced from the testbench). Do not create a verification plan; it is not used "
    "in this mode."
)


class TaskAborted(Exception):
    """Raised when the user terminates the running task (Esc)."""


@dataclass
class TaskResult:
    status: str
    run_dir: str
    dut_path: str | None = None
    testbench_path: str | None = None
    simulation_attempts: int = 0
    reflection_cycles: int = 0
    detail: str = ""


class Controller:
    def __init__(
        self,
        config: Config,
        prompts: dict[str, str],
        client: DeepSeekClient,
        ask_user: AskUser | None = None,
        progress: Progress | None = None,
        assessment_context: str = "self_verified",
        on_stream: Callable[[str, str], None] | None = None,
        on_step: Callable[[str, str], None] | None = None,
        on_stage: Callable[[str, str], None] | None = None,
    ):
        self.config = config
        self.prompts = prompts
        self.client = client
        self.ask_user = ask_user or (lambda qs: "")
        self.progress = progress or (lambda msg: None)
        self.assessment_context = assessment_context
        self.on_stream = on_stream
        self.on_step = on_step or (lambda kind, summary: None)
        # Called once per stage with (label, full stage transcript) for summarizing.
        self.on_stage = on_stage or (lambda label, text: None)
        self._turn = 0
        self._sim_attempts = 0
        self._reflections = 0
        self._external_tb_content: str | None = None
        self._should_cancel: Callable[[], bool] = lambda: False

    # -- public entrypoint --------------------------------------------------
    def run_task(
        self,
        request: str,
        should_cancel: Callable[[], bool] | None = None,
        external_testbench: str | None = None,
    ) -> TaskResult:
        self._should_cancel = should_cancel or (lambda: False)
        external = bool(external_testbench)
        context = "external_testbench" if external else self.assessment_context

        run = RunContext(request, module_hint=request)
        hashes = run.snapshot_prompts(self.prompts)
        run.log_task_header(
            request,
            self.config.masked_key(),
            self.config.iverilog_path,
            self.config.vvp_path,
            self.prompts,
            hashes,
        )

        external_tb_path = None
        self._external_tb_content = None
        if external:
            try:
                content = Path(external_testbench).read_text(encoding="utf-8")
            except OSError as exc:
                run.log_final("INFRASTRUCTURE_FAILED", 0, 0, f"Cannot read testbench: {exc}")
                return TaskResult(status="INFRASTRUCTURE_FAILED", run_dir=str(run.dir),
                                  detail=f"Cannot read provided testbench: {external_testbench} ({exc})")
            external_tb_path = run.path("external_testbench.v")
            external_tb_path.write_text(content, encoding="utf-8")
            self._external_tb_content = content
            run.log(f"MODE: EXTERNAL TESTBENCH ({external_testbench})")
        else:
            run.log("MODE: SELF-VERIFIED (agent generates its own testbench)")

        registry = ToolRegistry(self.config, run, context, external_tb_path=external_tb_path)
        try:
            return self._pipeline(request, run, registry)
        except TaskAborted:
            self.progress("Task terminated.")
            run.log_final("ABORTED", self._sim_attempts, self._reflections,
                          "Terminated by user before completion.",
                          registry.dut_path, registry.tb_path)
            return TaskResult(
                status="ABORTED",
                run_dir=str(run.dir),
                dut_path=str(registry.dut_path) if registry.dut_path else None,
                testbench_path=str(registry.tb_path) if registry.tb_path else None,
                detail="Terminated by user before completion.",
            )

    def _pipeline(self, request: str, run: RunContext, registry: ToolRegistry) -> TaskResult:
        self._turn = 0
        self._sim_attempts = 0
        self._reflections = 0
        external = registry.external_mode
        client = DeepSeekClient(
            self.config.deepseek_api_key,
            on_call=run.log_api_call,
        )

        messages: list[dict] = [
            {"role": "system", "content": self.prompts["system_prompt.md"]},
            {"role": "user", "content": request},
        ]
        if external:
            messages.append({"role": "user", "content": self._tb_block()})

        # Stage: specification.
        self.progress("Interpreting the specification...")
        spec_prompt = self.prompts["specification_prompt.md"]
        if external:
            spec_prompt += _EXTERNAL_SPEC_ADDENDUM
        messages.append({"role": "user", "content": spec_prompt})
        run.log_stage("SPECIFICATION", spec_prompt)
        if not self._run_stage(client, messages, registry, "specification",
                               stop=lambda r: r.generation_gate_open,
                               label="Specification"):
            return self._fail(run, registry, "INFRASTRUCTURE_FAILED", "Design spec was not accepted.")

        # Stage: verification planning (skipped entirely in external-testbench mode).
        if not external:
            self.progress("Creating a verification plan...")
            messages.append({"role": "user", "content": self.prompts["verification_prompt.md"]})
            run.log_stage("VERIFICATION PLANNING", self.prompts["verification_prompt.md"])
            if not self._run_stage(client, messages, registry, "verification",
                                   stop=lambda r: r.verification_gate_open,
                                   label="Verification planning"):
                return self._fail(run, registry, "INFRASTRUCTURE_FAILED", "Verification plan was not accepted.")

        # Stage: RTL (+ testbench, self-verified only) generation + first simulation.
        if external:
            self.progress("Generating RTL to match the provided testbench...")
            gen_prompt = self.prompts["external_generation_prompt.md"]
            gen_stage, gen_label = "RTL GENERATION (EXTERNAL TESTBENCH)", "RTL generation (external testbench)"
        else:
            self.progress("Generating RTL and self-checking testbench...")
            gen_prompt = self.prompts["testbench_prompt.md"]
            gen_stage, gen_label = "RTL + TESTBENCH GENERATION", "RTL & testbench generation"
        messages.append({"role": "user", "content": gen_prompt})
        run.log_stage(gen_stage, gen_prompt)
        self._run_stage(client, messages, registry, "generation",
                        stop=lambda r: r.last_sim_result is not None,
                        label=gen_label)

        if registry.last_sim_result is None:
            return self._fail(run, registry, "INFRASTRUCTURE_FAILED", "No simulation was run.")
        self._sim_attempts = 1

        result = registry.last_sim_result
        self.progress(f"[Simulation attempt 1/{MAX_SIMULATION_ATTEMPTS}] "
                      + ("PASS" if result["passed"] else "FAIL"))
        run.log_attempt(1, MAX_SIMULATION_ATTEMPTS, result["passed"], result.get("failure_type"))

        # Reflection loop.
        while not result["passed"] and self._reflections < MAX_REFLECTION_CYCLES:
            if self._should_cancel():
                raise TaskAborted()
            self._reflections += 1
            diagnosis = summarize_failure(result)
            self.progress(f"[Reflection cycle {self._reflections}/{MAX_REFLECTION_CYCLES}] {diagnosis}")
            run.log_reflection(self._reflections, MAX_REFLECTION_CYCLES, diagnosis)

            messages.append({
                "role": "user",
                "content": self.prompts["reflection_prompt.md"]
                + "\n\n## Latest simulation result\n"
                + _sim_digest(result),
            })
            run.log_stage(f"REFLECTION {self._reflections}", self.prompts["reflection_prompt.md"])
            before = registry.last_sim_result
            self._run_stage(client, messages, registry, "reflection",
                            stop=lambda r, b=before: r.last_sim_result is not b,
                            label=f"Reflection {self._reflections}")
            if registry.last_sim_result is before:
                run.log("Reflection produced no new simulation; terminating.")
                break
            result = registry.last_sim_result
            self._sim_attempts += 1
            self.progress(f"[Simulation attempt {self._sim_attempts}/{MAX_SIMULATION_ATTEMPTS}] "
                          + ("PASS" if result["passed"] else "FAIL"))
            run.log_attempt(self._sim_attempts, MAX_SIMULATION_ATTEMPTS, result["passed"],
                            result.get("failure_type"))

        status = "SUCCESS_INTERNAL" if result["passed"] else "DEVELOPMENT_FAILED"
        detail = "internal verification passed" if result["passed"] else summarize_failure(result)
        run.log_final(status, self._sim_attempts, self._reflections, detail,
                      registry.dut_path, registry.tb_path)
        return TaskResult(
            status=status,
            run_dir=str(run.dir),
            dut_path=str(registry.dut_path) if registry.dut_path else None,
            testbench_path=str(registry.tb_path) if registry.tb_path else None,
            simulation_attempts=self._sim_attempts,
            reflection_cycles=self._reflections,
            detail=detail,
        )

    # -- agentic loop for a single stage -----------------------------------
    def _run_stage(self, client, messages, registry: ToolRegistry, stage: str, stop,
                   label: str | None = None) -> bool:
        label = label or stage
        tools = registry.tools_for_stage(stage)
        transcript: list[str] = []  # everything that happened this stage, for the summary

        try:
            for _ in range(MAX_TURNS_PER_STAGE):
                if self._should_cancel():
                    raise TaskAborted()
                try:
                    message = client.chat(messages, tools=tools, on_delta=self.on_stream)
                except Exception as exc:  # noqa: BLE001 - transient API errors don't consume cycles
                    registry.run.log(f"API error in {stage}: {exc}")
                    return _stopped(registry, stop)

                messages.append(assistant_message_to_dict(message))
                tool_calls = getattr(message, "tool_calls", None)

                # Full transcript of this assistant turn (thinking + response + calls).
                self._turn += 1
                registry.run.log_assistant_turn(self._turn, stage, message)
                raw_turn = _assistant_turn_text(message)
                if raw_turn:
                    transcript.append(raw_turn)

                if not tool_calls:
                    return _stopped(registry, stop)

                for call in tool_calls:
                    result = registry.dispatch(call.function.name, call.function.arguments)
                    registry.run.log_tool_result(call.function.name, result)
                    tool_summary = _summarize_tool(call.function.name, result)
                    self.on_step("tool", tool_summary)
                    transcript.append(f"Tool result -> {tool_summary}")
                    # Intercept user-clarification requests during the spec stage.
                    if result.get("clarification_questions"):
                        answer = self.ask_user(result["clarification_questions"])
                        registry.run.log_clarification(result["clarification_questions"], answer)
                        transcript.append(f"User clarification -> {answer}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result),
                        })
                        if answer:
                            messages.append({"role": "user", "content": f"Clarification: {answer}"})
                        continue
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result)[:20000],
                    })

                if _stopped(registry, stop):
                    return True
            return _stopped(registry, stop)
        finally:
            # One stage-level summary request, regardless of how the stage ended.
            if transcript:
                combined = f"STAGE: {label}\n\n" + "\n\n---\n\n".join(transcript)
                self.on_stage(label, combined)

    def _tb_block(self) -> str:
        return (
            "A real testbench has been provided and is authoritative. Generate a DUT "
            "that matches its exact interface (module name and ports). You must NOT "
            "generate or modify a testbench; use only this one for simulation.\n\n"
            "## Provided testbench (read-only)\n```verilog\n"
            + (self._external_tb_content or "")
            + "\n```"
        )

    def _fail(self, run, registry, status, detail) -> TaskResult:
        run.log_final(status, self._sim_attempts, self._reflections, detail,
                      registry.dut_path, registry.tb_path)
        return TaskResult(
            status=status,
            run_dir=str(run.dir),
            dut_path=str(registry.dut_path) if registry.dut_path else None,
            testbench_path=str(registry.tb_path) if registry.tb_path else None,
            detail=detail,
        )


def _stopped(registry, stop) -> bool:
    try:
        return bool(stop(registry))
    except Exception:  # noqa: BLE001
        return False


def _sim_digest(result: dict) -> str:
    parts = [f"failure_type: {result.get('failure_type')}"]
    if result.get("compile_stderr"):
        parts.append("compile_stderr:\n" + result["compile_stderr"][:2000])
    if result.get("simulation_stdout"):
        parts.append("simulation_stdout:\n" + result["simulation_stdout"][:2000])
    if result.get("simulation_stderr"):
        parts.append("simulation_stderr:\n" + result["simulation_stderr"][:1000])
    return "\n".join(parts)


def _assistant_turn_text(message) -> str:
    """Assemble a self-contained description of one assistant turn for the summary
    agent: its reasoning, any message text, and the actions (tool calls) it took."""
    reasoning = (getattr(message, "reasoning_content", None) or "").strip()
    content = (getattr(message, "content", None) or "").strip()
    parts = []
    if reasoning:
        parts.append("Reasoning:\n" + reasoning)
    if content:
        parts.append("Message:\n" + content)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        actions = []
        for call in tool_calls:
            args = call.function.arguments or ""
            if len(args) > 600:
                args = args[:600] + "...(truncated)"
            actions.append(f"{call.function.name}({args})")
        parts.append("Actions:\n" + "\n".join(actions))
    return "\n\n".join(parts)


def _summarize_thinking(message) -> str:
    """Cost-free gist of a completed thinking step (no extra API call)."""
    reasoning = (getattr(message, "reasoning_content", None) or "").strip()
    content = (getattr(message, "content", None) or "").strip()
    text = reasoning or content
    if not text:
        return ""
    gist = " ".join(text.split())
    if len(gist) > 200:
        gist = gist[:197] + "..."
    words = len(reasoning.split())
    return f"({words} words) {gist}" if reasoning else gist


def _summarize_tool(name: str, result) -> str:
    """One-line human-readable summary of a completed tool call."""
    if not isinstance(result, dict):
        return f"{name} -> done"
    if result.get("error"):
        return f"{name} -> error: {str(result['error'])[:120]}"

    if name == "save_design_spec":
        status = result.get("validation_status", "?")
        ready = result.get("ready_for_generation")
        qs = result.get("clarification_questions") or []
        extra = f", {len(qs)} question(s)" if qs else ""
        return f"save_design_spec -> {status} (ready={ready}){extra}"
    if name == "save_verification_plan":
        ready = result.get("verification_plan_ready")
        ids = result.get("required_requirement_ids") or []
        return f"save_verification_plan -> ready={ready}, {len(ids)} required check(s)"
    if name == "write_verilog_file":
        if result.get("ok"):
            return f"write_verilog_file -> {_basename(result.get('path'))} ({result.get('bytes','?')} bytes)"
        return "write_verilog_file -> rejected"
    if name == "write_testbench_file":
        if result.get("ok"):
            cov = (result.get("traceability") or {}).get("covered_requirements") or []
            return f"write_testbench_file -> ok, covers {len(cov)} check(s)"
        errs = result.get("errors") or []
        return f"write_testbench_file -> rejected: {errs[0] if errs else 'invalid'}"
    if name == "read_current_design":
        return "read_current_design -> returned current artifacts"
    if name == "run_simulation":
        if result.get("passed"):
            return "run_simulation -> PASS"
        return f"run_simulation -> FAIL ({result.get('failure_type')})"
    return f"{name} -> done"


def _basename(path) -> str:
    if not path:
        return "?"
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]
