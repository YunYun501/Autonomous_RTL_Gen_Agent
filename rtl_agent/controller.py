"""Task controller.

Drives one natural-language request through the full pipeline: freeze prompts ->
design-spec validation -> verification-plan validation -> RTL + testbench
generation -> compile/simulate -> up to five reflection cycles. The controller,
not the model, enforces every gate and computes readiness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .config import Config
from .deepseek_client import DeepSeekClient, assistant_message_to_dict
from .logger import RunContext
from .reflection import MAX_REFLECTION_CYCLES, MAX_SIMULATION_ATTEMPTS, summarize_failure
from .tools import ToolRegistry

MAX_TURNS_PER_STAGE = 12

AskUser = Callable[[list[dict]], str]
Progress = Callable[[str], None]


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
    ):
        self.config = config
        self.prompts = prompts
        self.client = client
        self.ask_user = ask_user or (lambda qs: "")
        self.progress = progress or (lambda msg: None)
        self.assessment_context = assessment_context
        self._should_cancel: Callable[[], bool] = lambda: False

    # -- public entrypoint --------------------------------------------------
    def run_task(self, request: str, should_cancel: Callable[[], bool] | None = None) -> TaskResult:
        self._should_cancel = should_cancel or (lambda: False)
        run = RunContext(request, module_hint=request)
        run.snapshot_prompts(self.prompts)
        registry = ToolRegistry(self.config, run, self.assessment_context)
        try:
            return self._pipeline(request, run, registry)
        except TaskAborted:
            run.log("ABORTED: task terminated by user (Esc).")
            self.progress("Task terminated.")
            return TaskResult(
                status="ABORTED",
                run_dir=str(run.dir),
                dut_path=str(registry.dut_path) if registry.dut_path else None,
                testbench_path=str(registry.tb_path) if registry.tb_path else None,
                detail="Terminated by user before completion.",
            )

    def _pipeline(self, request: str, run: RunContext, registry: ToolRegistry) -> TaskResult:
        client = DeepSeekClient(
            self.config.deepseek_api_key,
            on_call=run.log_api_call,
        )

        messages: list[dict] = [
            {"role": "system", "content": self.prompts["system_prompt.md"]},
            {"role": "user", "content": request},
        ]

        # Stage: specification.
        self.progress("Interpreting the specification...")
        messages.append({"role": "user", "content": self.prompts["specification_prompt.md"]})
        if not self._run_stage(client, messages, registry, "specification",
                               stop=lambda r: r.generation_gate_open):
            return self._fail(run, registry, "INFRASTRUCTURE_FAILED", "Design spec was not accepted.")

        # Stage: verification planning.
        self.progress("Creating a verification plan...")
        messages.append({"role": "user", "content": self.prompts["verification_prompt.md"]})
        if not self._run_stage(client, messages, registry, "verification",
                               stop=lambda r: r.verification_gate_open):
            return self._fail(run, registry, "INFRASTRUCTURE_FAILED", "Verification plan was not accepted.")

        # Stage: RTL + testbench generation + first simulation.
        self.progress("Generating RTL and self-checking testbench...")
        messages.append({"role": "user", "content": self.prompts["testbench_prompt.md"]})
        self._run_stage(client, messages, registry, "generation",
                        stop=lambda r: r.last_sim_result is not None)

        sim_attempts = 1 if registry.last_sim_result is not None else 0
        if registry.last_sim_result is None:
            return self._fail(run, registry, "INFRASTRUCTURE_FAILED", "No simulation was run.")

        result = registry.last_sim_result
        self.progress(f"[Simulation attempt 1/{MAX_SIMULATION_ATTEMPTS}] "
                      + ("PASS" if result["passed"] else "FAIL"))

        # Reflection loop.
        reflection_cycles = 0
        while not result["passed"] and reflection_cycles < MAX_REFLECTION_CYCLES:
            if self._should_cancel():
                raise TaskAborted()
            reflection_cycles += 1
            diagnosis = summarize_failure(result)
            self.progress(f"[Reflection cycle {reflection_cycles}/{MAX_REFLECTION_CYCLES}] {diagnosis}")
            run.log(f"Reflection {reflection_cycles}: {diagnosis}")

            messages.append({
                "role": "user",
                "content": self.prompts["reflection_prompt.md"]
                + "\n\n## Latest simulation result\n"
                + _sim_digest(result),
            })
            before = registry.last_sim_result
            self._run_stage(client, messages, registry, "reflection",
                            stop=lambda r, b=before: r.last_sim_result is not b)
            if registry.last_sim_result is before:
                run.log("Reflection produced no new simulation; terminating.")
                break
            result = registry.last_sim_result
            sim_attempts += 1
            self.progress(f"[Simulation attempt {sim_attempts}/{MAX_SIMULATION_ATTEMPTS}] "
                          + ("PASS" if result["passed"] else "FAIL"))

        status = "SUCCESS_INTERNAL" if result["passed"] else "DEVELOPMENT_FAILED"
        run.log(f"Final status: {status} "
                f"(attempts={sim_attempts}, reflections={reflection_cycles})")
        return TaskResult(
            status=status,
            run_dir=str(run.dir),
            dut_path=str(registry.dut_path) if registry.dut_path else None,
            testbench_path=str(registry.tb_path) if registry.tb_path else None,
            simulation_attempts=sim_attempts,
            reflection_cycles=reflection_cycles,
            detail=summarize_failure(result) if not result["passed"] else "internal verification passed",
        )

    # -- agentic loop for a single stage -----------------------------------
    def _run_stage(self, client, messages, registry: ToolRegistry, stage: str, stop) -> bool:
        tools = registry.tools_for_stage(stage)
        for _ in range(MAX_TURNS_PER_STAGE):
            if self._should_cancel():
                raise TaskAborted()
            try:
                message = client.chat(messages, tools=tools)
            except Exception as exc:  # noqa: BLE001 - transient API errors don't consume cycles
                registry.run.log(f"API error in {stage}: {exc}")
                return _stopped(registry, stop)

            messages.append(assistant_message_to_dict(message))
            tool_calls = getattr(message, "tool_calls", None)

            if not tool_calls:
                # Model produced final text with no tool call; nothing more to do.
                return _stopped(registry, stop)

            for call in tool_calls:
                result = registry.dispatch(call.function.name, call.function.arguments)
                # Intercept user-clarification requests during the spec stage.
                if result.get("clarification_questions"):
                    answer = self.ask_user(result["clarification_questions"])
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

    def _fail(self, run, registry, status, detail) -> TaskResult:
        run.log(f"{status}: {detail}")
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
