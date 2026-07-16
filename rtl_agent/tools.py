"""Controlled agent tools.

Exposes only narrowly-scoped tools. Availability is state-dependent: RTL-writing
and simulation stay blocked until the design-spec generation gate opens, and the
testbench/simulation stay blocked until the verification-plan gate opens. The
controller derives all file paths; the model never supplies a path or a shell
command.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import design_spec_validator, verification_plan_validator, testbench_validator
from .config import Config
from .logger import RunContext, sha256_text
from .simulator import run_simulation as _run_simulation

SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


# ---- Tool JSON schema definitions (per stage) ---------------------------------

def _spec_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "save_design_spec",
            "description": (
                "Submit a complete structured RTL design specification for local "
                "schema, semantic, and field-risk validation. Valid specifications "
                "are saved as design_spec.json. Invalid drafts are saved separately "
                "and returned with repair instructions or user clarification "
                "questions. Do not generate RTL until this tool returns "
                "ready_for_generation=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specification": {
                        "type": "object",
                        "description": "The complete design specification with all top-level fields present.",
                    }
                },
                "required": ["specification"],
            },
        },
    }


def _verification_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "save_verification_plan",
            "description": (
                "Submit a complete verification plan with unique VP-* identifiers "
                "for local validation. Testbench generation stays blocked until this "
                "returns verification_plan_ready=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"type": "object", "description": "The verification plan."}
                },
                "required": ["plan"],
            },
        },
    }


def _write_verilog_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write_verilog_file",
            "description": "Write the complete DUT module. The controller determines the path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_name": {"type": "string"},
                    "code_str": {"type": "string", "description": "Complete Verilog module source."},
                },
                "required": ["module_name", "code_str"],
            },
        },
    }


def _write_testbench_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write_testbench_file",
            "description": (
                "Write the complete self-checking testbench. covered_requirements "
                "must list every frozen required VP-* id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "module_name": {"type": "string"},
                    "code_str": {"type": "string"},
                    "covered_requirements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["module_name", "code_str", "covered_requirements"],
            },
        },
    }


def _read_design_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_current_design",
            "description": "Return the current spec, plan, DUT, testbench, and latest simulation result.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _run_sim_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_simulation",
            "description": "Compile and run the current DUT and testbench with the configured simulator.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class ToolRegistry:
    """Holds per-task tool state and gate flags, and dispatches tool calls."""

    def __init__(
        self,
        config: Config,
        run: RunContext,
        assessment_context: str,
        external_tb_path: Path | None = None,
    ):
        self.config = config
        self.run = run
        self.assessment_context = assessment_context

        self.design_spec: dict | None = None
        self.verification_plan: dict | None = None
        self.required_ids: list[str] = []
        self.dut_path: Path | None = None
        self.tb_path: Path | None = None
        self.last_sim_result: dict | None = None

        # External-testbench mode: the real testbench is authoritative; the agent
        # must not generate or modify one, and no verification plan is created.
        self.external_mode = external_tb_path is not None
        if self.external_mode:
            self.tb_path = external_tb_path

        # Gates.
        self.generation_gate_open = False
        # In external mode there is no verification-plan stage, so the gate that
        # guards simulation is open from the start (the real testbench is ready).
        self.verification_gate_open = self.external_mode

    # -- tool availability per stage ---------------------------------------
    def tools_for_stage(self, stage: str) -> list[dict]:
        if stage == "specification":
            return [_spec_tool()]
        if stage == "verification":
            return [_verification_tool(), _read_design_tool()]
        if stage in ("generation", "reflection"):
            tools = [_write_verilog_tool(), _run_sim_tool(), _read_design_tool()]
            if not self.external_mode:
                # Only the agent's self-verification flow may write a testbench.
                tools.insert(1, _write_testbench_tool())
            return tools
        return []

    # -- dispatch -----------------------------------------------------------
    def dispatch(self, name: str, arguments: str) -> dict:
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return {"error": f"Malformed tool arguments: {exc}"}

        handler = {
            "save_design_spec": self._save_design_spec,
            "save_verification_plan": self._save_verification_plan,
            "write_verilog_file": self._write_verilog_file,
            "write_testbench_file": self._write_testbench_file,
            "read_current_design": self._read_current_design,
            "run_simulation": self._run_simulation,
        }.get(name)

        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        return handler(args)

    # -- handlers -----------------------------------------------------------
    def _save_design_spec(self, args: dict) -> dict:
        spec = args.get("specification")
        if not isinstance(spec, dict):
            return {"validation_status": "model_repair_required", "ready_for_generation": False,
                    "errors": [{"code": "NOT_OBJECT", "message": "specification must be an object."}]}
        # The controller owns assessment_context.
        spec["assessment_context"] = self.assessment_context

        result = design_spec_validator.validate_specification(spec, self.assessment_context)
        self.run.write_json("design_spec.draft.json", spec)
        self.run.write_json("design_spec_validation.json", result)

        if result["ready_for_generation"]:
            self.design_spec = result["normalized_specification"] or spec
            self.run.write_json("design_spec.json", self.design_spec)
            self.generation_gate_open = True
            result["saved_path"] = str(self.run.path("design_spec.json"))
        else:
            result["saved_path"] = None
            result["draft_path"] = str(self.run.path("design_spec.draft.json"))
        return result

    def _save_verification_plan(self, args: dict) -> dict:
        if self.external_mode:
            return {"error": "A real testbench was provided; no verification plan is created in external mode.",
                    "verification_plan_ready": False}
        if not self.generation_gate_open or self.design_spec is None:
            return {"error": "Design specification is not accepted yet.", "verification_plan_ready": False}
        plan = args.get("plan")
        if not isinstance(plan, dict):
            return {"verification_plan_ready": False, "errors": [{"code": "NOT_OBJECT", "message": "plan must be an object."}]}

        result = verification_plan_validator.validate_plan(plan, self.design_spec)
        self.run.write_json("verification_plan.draft.json", plan)
        self.run.write_json("verification_plan_validation.json", result)

        if result["verification_plan_ready"]:
            self.verification_plan = plan
            self.required_ids = result["required_requirement_ids"]
            self.run.write_json("verification_plan.json", plan)
            md = verification_plan_validator.render_markdown(plan)
            self.run.path("verification_plan.md").write_text(md, encoding="utf-8")
            self.verification_gate_open = True
            result["saved_json_path"] = str(self.run.path("verification_plan.json"))
            result["saved_markdown_path"] = str(self.run.path("verification_plan.md"))
        return result

    def _write_verilog_file(self, args: dict) -> dict:
        if not self.generation_gate_open:
            return {"error": "RTL generation is blocked until the design spec is accepted."}
        module_name = args.get("module_name", "")
        code = args.get("code_str", "")
        if not SAFE_NAME.match(module_name or ""):
            return {"error": f"Invalid module name: {module_name!r}."}
        if not code.strip():
            return {"error": "code_str is empty."}

        self.run.ensure()
        path = self.run.path(f"{module_name}.v")
        path.write_text(code, encoding="utf-8")
        self.dut_path = path
        version = self.run.save_rtl_version(code)
        digest = sha256_text(code)
        return {
            "path": str(path),
            "bytes": len(code.encode("utf-8")),
            "sha256": digest,
            "version": str(version),
            "ok": True,
        }

    def _write_testbench_file(self, args: dict) -> dict:
        if self.external_mode:
            return {"error": "A real testbench was provided. You must not generate or modify a "
                             "testbench; generate only the DUT and call run_simulation."}
        if not self.verification_gate_open:
            return {"error": "Testbench generation is blocked until the verification plan is frozen."}
        module_name = args.get("module_name", "")
        code = args.get("code_str", "")
        covered = args.get("covered_requirements", [])
        spec_module = _spec_module_name(self.design_spec)

        trace = testbench_validator.validate_testbench(
            module_name=module_name,
            code_str=code,
            covered_requirements=covered,
            required_ids=self.required_ids,
            spec_module_name=spec_module,
        )
        self.run.write_json("testbench_traceability.json", {**trace, "sha256": sha256_text(code or "")})
        if not trace["valid"]:
            return {"ok": False, "errors": trace["errors"], "traceability": trace}

        self.run.ensure()
        path = self.run.path(f"tb_{module_name}.v")
        path.write_text(code, encoding="utf-8")
        self.tb_path = path
        self.run.save_testbench_version(code)
        return {"path": str(path), "ok": True, "traceability": trace}

    def _read_current_design(self, args: dict) -> dict:
        return {
            "design_spec": self.design_spec,
            "verification_plan": self.verification_plan,
            "required_requirement_ids": self.required_ids,
            "dut": self.dut_path.read_text(encoding="utf-8") if self.dut_path and self.dut_path.exists() else None,
            "testbench": self.tb_path.read_text(encoding="utf-8") if self.tb_path and self.tb_path.exists() else None,
            "last_simulation": self.last_sim_result,
        }

    def _run_simulation(self, args: dict) -> dict:
        if not self.verification_gate_open:
            return {"error": "Simulation is blocked until the verification plan is frozen."}
        if not self.dut_path:
            return {"error": "The DUT must be written before simulation."}
        if not self.tb_path:
            return {"error": "No testbench available for simulation."}

        result = _run_simulation(
            self.config, self.run.dir, self.dut_path, self.tb_path, external=self.external_mode
        )
        self.last_sim_result = result
        self.run.save_simulation_log(result)
        return result


def _spec_module_name(spec: dict | None) -> str | None:
    if not spec:
        return None
    from .design_spec_schema import unwrap

    return unwrap(spec.get("module_name"))
