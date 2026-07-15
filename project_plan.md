# Agentic AI for Autonomous RTL Generation and Verification

## 1. Project Goal

Build a terminal-native AI agent that accepts an arbitrary natural-language RTL design request, loads editable Markdown prompt files and uses DeepSeek-V4-Pro in thinking mode to generate Verilog and a self-checking development testbench, compiles and simulates the design with Icarus Verilog, diagnoses failures, and performs up to five reflection-and-correction cycles.

The same completed agent will later be tested using many different natural-language RTL tasks. Those tasks are **not** predefined or built into the agent.

---

## 2. Intended User Experience

The user should only need to:

1. Start the terminal agent.
2. Complete the first-run setup if no valid configuration exists.
3. Enter a natural-language request.
4. Observe generation, compilation, simulation, and correction in the terminal.
5. Receive the final RTL and execution logs.

Example:

```text
$ python agent.py

RTL Agent startup checks
[PASS] iverilog.exe
[PASS] vvp.exe
[PASS] Simulator smoke test
[PASS] DeepSeek API
[PASS] Run directory

Model: deepseek-v4-pro
Thinking mode: enabled
Reasoning effort: max

rtl-agent> Build an 8-bit synchronous up-counter with an active-low
asynchronous reset and an enable input. Wrap from 255 to 0.

[Agent] Interpreting the specification...
[Agent] Creating a verification plan...
[Tool] write_verilog_file(...)
[Tool] write_testbench_file(...)
[Tool] run_simulation(...)

[Simulation attempt 1/6] FAIL
[Reflection cycle 1/5] Counter changes while enable is low.

[Tool] write_verilog_file(...)
[Tool] run_simulation(...)

[Simulation attempt 2/6] PASS

Final status: SUCCESS
```

The user should not need to create task manifests, task directories, module interfaces, testbenches, or simulator commands.

---

## 3. Fixed DeepSeek API Configuration

The prototype should ask the user only for a DeepSeek API key. The following values are fixed application defaults:

```python
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
DEEPSEEK_THINKING = {"type": "enabled"}
DEEPSEEK_REASONING_EFFORT = "max"
```

Use the OpenAI-compatible Chat Completions API.

Example:

```python
from openai import OpenAI

client = OpenAI(
    api_key=config["deepseek_api_key"],
    base_url="https://api.deepseek.com",
)

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    tools=tool_definitions,
    reasoning_effort="max",
    extra_body={
        "thinking": {
            "type": "enabled"
        }
    },
)
```

### Thinking-mode tool-call requirement

When a DeepSeek response contains tool calls, preserve the complete assistant response, including:

- `reasoning_content`
- `content`
- `tool_calls`

Append that complete assistant message to the conversation history before appending the tool results. The `reasoning_content` associated with a tool-call turn must be passed back in subsequent API requests for that interaction.

Do not send unsupported or unnecessary sampling controls such as `temperature` when thinking mode is enabled.

---

## 3.1 External Prompt File Architecture

All agent instructions must be stored as ordinary Markdown files in a top-level `prompts/` directory. They must not be hardcoded as long strings inside `deepseek_client.py`, `controller.py`, or other Python modules.

Recommended structure:

```text
prompts/
├── system_prompt.md
├── specification_prompt.md
├── verification_prompt.md
├── testbench_prompt.md
└── reflection_prompt.md
```

### 3.1.1 Prompt responsibilities

| Prompt file | Purpose | API usage |
|---|---|---|
| `system_prompt.md` | Permanent identity, responsibilities, safety boundaries, tool rules, and RTL-design constraints | Sent with API role `system` |
| `specification_prompt.md` | Instructions for translating the exact user request into a structured design contract | Added when interpreting a new task |
| `verification_prompt.md` | Instructions for deriving, identifying, and freezing abstract verification requirements and expected outcomes | Added only during verification planning |
| `testbench_prompt.md` | Instructions for translating the frozen verification plan into an executable self-checking development testbench | Added only after the verification plan has been accepted and frozen |
| `reflection_prompt.md` | Instructions for diagnosing compiler or simulator failures and producing a corrected complete design | Added after a failed development simulation |

Only `system_prompt.md` is sent using the API role `system`. The other files are stage-specific instruction templates inserted by the controller at the relevant point in the conversation.

### 3.1.2 Required `system_prompt.md` content

The master system prompt should be easy to open and edit in VS Code, Notepad, or another text editor.

A suitable initial version is:

```markdown
# RTL Agent System Prompt

You are an autonomous RTL design and verification agent.

## Responsibilities

1. Interpret the user's natural-language hardware request.
2. Produce synthesizable Verilog or SystemVerilog compatible with Icarus Verilog.
3. Create a precise design specification before implementation.
4. Create a verification plan derived from the user's requirements.
5. Generate a self-checking development testbench.
6. Use the available tools to write files and run simulation.
7. Analyse compilation and simulation failures.
8. Correct the RTL when verification fails.
9. Preserve all requirements from the user's original prompt.

## Rules

- Return complete modules rather than partial code fragments.
- Do not invent unnecessary ports or behaviours.
- State necessary assumptions clearly.
- Do not remove or weaken a verification requirement to make the design pass.
- Do not print a success marker unless all planned checks have executed.
- Do not claim that a file was written or a simulation was run without using the corresponding tool.
- Do not access files or commands outside the supplied tools.
- Use only the current task's working directory.
- Do not modify protected external holdout tests.
- Treat compiler and simulator logs as evidence when diagnosing failures.

## Available tools

Tool availability depends on the current workflow stage.

- `save_design_spec`
- `save_verification_plan`
- `write_verilog_file`
- `write_testbench_file`
- `read_current_design`
- `run_simulation`
```

### 3.1.3 Stage-specific prompt content

`specification_prompt.md` should instruct DeepSeek to:

- Preserve the exact user request as the authoritative source.
- Extract the module interface and behaviour.
- Identify clocking, reset, timing, state, width, and boundary requirements.
- Record assumptions separately from explicit requirements.
- Return a machine-readable design contract.
- Ask for clarification only when a missing detail materially changes the interface or core behaviour.

`verification_prompt.md` should instruct DeepSeek to:

- Derive abstract verification requirements only from the accepted design contract.
- Give every required check a stable identifier such as `VP-001`.
- Define stimulus intent, observable behaviour, expected result, boundary conditions, and pass criteria.
- Cover reset, normal operation, boundaries, transitions, repeated operation, and relevant corner cases.
- Avoid implementation-specific checks unless the design specification explicitly requires them.
- Call `save_verification_plan`.
- Never generate HDL testbench code during this stage.

`testbench_prompt.md` should instruct DeepSeek to:

- Treat the accepted design specification and frozen verification plan as authoritative.
- Convert every `VP-*` requirement into one or more executable testbench checks.
- Instantiate the DUT using exact validated names and named port connections.
- Generate required clocks and resets.
- Avoid simulation races and sample sequential outputs only after updates have settled.
- Use case equality or inequality where unknown values must fail.
- Print `RTL_AGENT_TEST_FAIL` with the relevant `VP-*` identifier on failure.
- Print `RTL_AGENT_TEST_PASS` exactly once and only after all required checks pass.
- Call `write_testbench_file` with the complete source and `covered_requirements`.
- Never add, delete, weaken, or reinterpret frozen verification requirements.

`reflection_prompt.md` should instruct DeepSeek to:

- Read the current design contract, frozen verification plan, RTL, testbench, and full tool logs.
- Classify the failure before editing.
- Distinguish RTL defects from testbench defects and infrastructure failures.
- Return complete corrected source rather than partial patches.
- Preserve the original user requirements.
- Modify the testbench only when it is mechanically defective.
- Never delete, weaken, or reverse a valid expected result.

### 3.1.4 Prompt loader

Create `rtl_agent/prompt_loader.py`:

```python
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIRECTORY = PROJECT_ROOT / "prompts"


class PromptLoadError(RuntimeError):
    \"\"\"Raised when a required prompt cannot be loaded safely.\"\"\"


def load_prompt(filename: str) -> str:
    prompt_path = (PROMPT_DIRECTORY / filename).resolve()

    try:
        prompt_path.relative_to(PROMPT_DIRECTORY.resolve())
    except ValueError as exc:
        raise PromptLoadError(f"Invalid prompt path: {filename}") from exc

    if not prompt_path.is_file():
        raise PromptLoadError(
            f"Required prompt file was not found: {prompt_path}"
        )

    try:
        content = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptLoadError(
            f"Could not read prompt file: {prompt_path}"
        ) from exc

    if not content:
        raise PromptLoadError(f"Prompt file is empty: {prompt_path}")

    return content
```

The loader must accept only filenames from the permitted prompt directory and must reject missing or empty prompt files.

### 3.1.5 Prompt loading policy

At the start of each new natural-language task:

1. Load all five prompt files.
2. Validate that none is missing or empty.
3. Calculate and record the SHA-256 hash of each prompt.
4. Copy the exact files into the new run directory.
5. Keep those loaded prompt strings fixed throughout that task.
6. Use the copied prompt versions for all reflections in that task.

Editing a master prompt while a task is active must not change the active task. The modified prompt takes effect when the user starts the next task with `/new` or submits a new request after the current run ends.

This prevents one design attempt from being produced under several different prompt versions.

### 3.1.6 Message construction

The initial API conversation should preserve the user’s exact wording:

```python
system_prompt = load_prompt("system_prompt.md")
specification_prompt = load_prompt("specification_prompt.md")

messages = [
    {
        "role": "system",
        "content": system_prompt,
    },
    {
        "role": "user",
        "content": user_prompt,
    },
    {
        "role": "user",
        "content": specification_prompt,
    },
]
```

The controller may combine the user request and stage instruction into one message, but the exact original prompt must also be stored separately in the run artifacts and API log.

Tool definitions are supplied through the API `tools` parameter rather than pasted into the user’s prompt.

### 3.1.7 Prompt accessibility commands

Add terminal commands:

| Command | Action |
|---|---|
| `/prompts` | Show the master prompt directory and the four prompt filenames |
| `/show-prompt <name>` | Display one active prompt, such as `system` or `reflection` |
| `/reload-prompts` | Reload master prompts for the next task; never alter the currently active task |

The prototype does not need an in-terminal prompt editor. The files should remain directly editable through the user’s normal text editor.

### 3.1.8 Completion condition

The prompt subsystem is complete when:

- All five prompt files exist outside the Python source.
- Startup detects missing or empty prompts.
- A new task loads and freezes the current prompt versions.
- The exact prompt versions are copied into the run directory.
- The DeepSeek request uses the loaded prompt contents.
- Editing a master prompt changes the next task without requiring code modification.

---


## 4. Stage 0 — First-Run Configuration Wizard

### 4.1 What to configure

On the first launch, ask for exactly three values:

1. DeepSeek API key.
2. Icarus Verilog compiler path.
3. VVP simulation runtime path.

Suggested defaults:

```text
Icarus Verilog:
C:\msys64\ucrt64\bin\iverilog.exe

VVP runtime:
C:\msys64\ucrt64\bin\vvp.exe
```

Example terminal flow:

```text
RTL Agent First-Time Setup

DeepSeek API key:
> ********************************

Icarus Verilog path
[C:\msys64\ucrt64\bin\iverilog.exe]:
>

VVP runtime path
[C:\msys64\ucrt64\bin\vvp.exe]:
>
```

Pressing Enter should accept the suggested executable path.

### 4.2 Configuration storage

For this prototype, save all three values, including the API key, in a local configuration file:

```text
.rtl-agent/config.json
```

Example:

```json
{
  "deepseek_api_key": "user-key-here",
  "iverilog_path": "C:\\msys64\\ucrt64\\bin\\iverilog.exe",
  "vvp_path": "C:\\msys64\\ucrt64\\bin\\vvp.exe"
}
```

The configuration directory must be excluded from Git:

```gitignore
.rtl-agent/
```

Although storing the API key in the configuration file is acceptable for this prototype:

- Mask it in terminal output.
- Redact it from logs.
- Never include it in prompts.
- Never include it in saved API request headers.
- Never commit the configuration file.

### 4.3 Dependencies

This stage depends on:

- Python being installed.
- Icarus Verilog being installed.
- A valid DeepSeek API key.
- Permission to write the local configuration directory.

### 4.4 Completion condition

Setup is done when the three values have been saved and have passed the validation checks in Stage 1.

---

## 5. Stage 1 — Mandatory Startup Preflight

Run the preflight:

- During first-time setup.
- Every time the agent starts.
- After any configuration change.
- When the user enters `/doctor`.

Do not display the main `rtl-agent>` prompt until all mandatory checks pass.

### 5.1 Validate `iverilog.exe`

Perform these checks:

1. The configured path exists.
2. The path points to a file.
3. The executable starts successfully.
4. It responds before the timeout.
5. Its version output can be captured.

Command:

```powershell
C:\msys64\ucrt64\bin\iverilog.exe -V
```

Done looks like:

```text
[PASS] Icarus Verilog compiler
Path: C:\msys64\ucrt64\bin\iverilog.exe
```

### 5.2 Validate `vvp.exe`

Perform the same checks for:

```powershell
C:\msys64\ucrt64\bin\vvp.exe -V
```

Done looks like:

```text
[PASS] VVP simulation runtime
Path: C:\msys64\ucrt64\bin\vvp.exe
```

The correct runtime executable is `vvp.exe`, not `vpp.exe`.

### 5.3 Run an end-to-end simulator smoke test

Version checks do not prove that the compiler and runtime work together. Create a temporary source file:

```verilog
module rtl_agent_smoke_test;
    initial begin
        $display("RTL_AGENT_SIMULATOR_READY");
        $finish;
    end
endmodule
```

Compile it using the exact configured compiler path:

```powershell
iverilog.exe -o rtl_agent_smoke_test.vvp rtl_agent_smoke_test.v
```

Run it using the exact configured runtime path:

```powershell
vvp.exe rtl_agent_smoke_test.vvp
```

The smoke test passes only if:

- Compilation return code is zero.
- Simulation return code is zero.
- Neither process times out.
- Standard output contains `RTL_AGENT_SIMULATOR_READY`.

Delete the temporary files after the test.

### 5.4 Validate the DeepSeek API key

Send a minimal request using the fixed URL and model:

```text
Return exactly: RTL_AGENT_API_READY
```

The check passes only if:

- Authentication succeeds.
- `deepseek-v4-pro` is accepted.
- Thinking mode is accepted.
- The response is parseable.
- The request completes before the timeout.

This startup call does not count as an RTL generation, simulation attempt, or reflection cycle.

### 5.5 Validate prompt files and local directories

Confirm that all required prompt files exist, are readable, and are non-empty:

```text
prompts/system_prompt.md
prompts/specification_prompt.md
prompts/verification_prompt.md
prompts/testbench_prompt.md
prompts/reflection_prompt.md
```

Also confirm that the agent can create and write:

```text
runs/
logs/
.rtl-agent/
```

A missing or empty prompt is a startup configuration failure because the agent must not silently fall back to a hardcoded prompt.

### 5.6 Failure handling

On a failed check, show:

```text
[R] Retry
[C] Reconfigure
[Q] Quit
```

Do not continue in a partially configured state.

### 5.7 Completion condition

The preflight is done when all required checks pass:

```text
[PASS] Configuration loaded
[PASS] iverilog.exe
[PASS] vvp.exe
[PASS] Compile-and-run smoke test
[PASS] DeepSeek API
[PASS] Prompt files
[PASS] Writable run directory

RTL Agent ready.
```

---

## 6. Stage 2 — Terminal-Native Interface

Implement a persistent terminal interface similar in use to Claude Code, but with a custom RTL-specific agent loop.

Recommended libraries:

- `prompt_toolkit` for interactive input and history.
- `rich` for formatted status panels and syntax highlighting.
- `typer` or `argparse` for startup commands.

The primary interaction is direct natural language:

```text
rtl-agent> Build a four-request round-robin arbiter with a synchronous
active-high reset. Grant exactly one active request per cycle.
```

Useful terminal commands:

| Command | Action |
|---|---|
| `/doctor` | Rerun the complete startup preflight |
| `/config` | Show paths and masked API-key status |
| `/config key` | Replace and test the API key |
| `/config iverilog` | Replace and test the compiler path |
| `/config vvp` | Replace and test the runtime path |
| `/status` | Show the active task and loop counters |
| `/show-rtl` | Show the current generated RTL |
| `/show-testbench` | Show the current development testbench |
| `/show-log` | Show the current human-readable log |
| `/prompts` | Show the master prompt directory and available prompt files |
| `/show-prompt <name>` | Display the active copy of a selected prompt |
| `/reload-prompts` | Reload master prompts for the next task only |
| `/new` | End the current context and start another request |
| `/quit` | Flush logs and exit |

The terminal should show concise progress summaries, not the full stored reasoning output.

---

## 7. Stage 3 — Accept an Arbitrary Natural-Language RTL Request

Each non-command terminal entry starts a new independent RTL task.

Examples:

```text
Build an 8-bit ALU supporting add, subtract, AND, OR and XOR.
Include zero and carry flags.
```

```text
Design a traffic-light controller. Green lasts three cycles, yellow
one cycle and red four cycles. Reset is active-low and asynchronous.
```

```text
Create a parameterised synchronous FIFO with full and empty flags.
```

For every new request:

1. Create a new run directory.
2. Load `system_prompt.md`, `specification_prompt.md`, `verification_prompt.md`, `testbench_prompt.md`, and `reflection_prompt.md`.
3. Validate, hash, and snapshot those prompts into the run directory.
4. Freeze the loaded prompt versions for the complete task.
5. Reset the reflection counter to zero.
6. Reset the simulation-attempt counter to zero.
7. Preserve all previous runs and logs.
8. Send the exact natural-language request to DeepSeek together with the loaded system prompt, the relevant stage instruction, and the controlled tool definitions.
9. Do not load a predefined task, manifest, or task catalogue.

### Completion condition

This stage is done when the request has been stored as the authoritative user specification and a new run context has been created.

---

## 8. Stage 4 — Interpret the Specification

Using the frozen `specification_prompt.md`, DeepSeek should convert the exact user prompt into a structured draft design contract and submit it through `save_design_spec`.

The contract should identify, where applicable:

- Module name.
- Input ports.
- Output ports.
- Signal widths.
- Parameters.
- Clock edge.
- Reset type and polarity.
- Reset values.
- Combinational behaviour.
- Sequential behaviour.
- State transitions.
- Timing requirements.
- Boundary conditions.
- Expected invalid or idle behaviour.
- Assumptions made because the prompt was incomplete.

Example internal representation:

```json
{
  "module_name": "counter_8bit",
  "inputs": [
    {"name": "clk", "width": 1},
    {"name": "rst_n", "width": 1},
    {"name": "enable", "width": 1}
  ],
  "outputs": [
    {"name": "count", "width": 8}
  ],
  "clocking": {
    "edge": "posedge"
  },
  "reset": {
    "type": "asynchronous",
    "polarity": "active_low",
    "output_values": {
      "count": 0
    }
  },
  "requirements": [
    "Increment on each rising clock edge when enable is high",
    "Hold value when enable is low",
    "Wrap from 255 to 0"
  ]
}
```

### Ambiguity policy

- Make and log conventional assumptions for minor omissions.
- Ask one concise clarification only when an omission would materially alter the module interface or core behaviour.
- Do not ask the user to produce implementation details that the agent can reasonably infer.

### Completion condition

This stage is done only when the local validator has saved `design_spec.json` and returned `ready_for_generation=true`.

---

## 8.1 Structured Design Specification, Provenance, and Field-Risk Policy

The agent must not generate RTL directly from an unvalidated natural-language request.

Use this sequence:

```text
Exact user prompt
        ↓
DeepSeek extracts a structured draft specification
        ↓
DeepSeek calls save_design_spec(...)
        ↓
Local JSON-schema validation
        ↓
Local engineering semantic validation
        ↓
Local field-risk policy
        ↓
Valid and ready?
  ├── Yes → Save design_spec.json and permit RTL generation
  ├── Model formatting defect → Return errors to DeepSeek for repair
  └── Critical ambiguity → Ask the user a focused clarification
```

DeepSeek proposes field values. The local Python controller is the authority that decides:

- Whether the structure is valid.
- Whether references between fields are consistent.
- Whether an omitted value may be derived.
- Whether an inferred value is permitted.
- Whether user clarification is mandatory.
- Whether RTL generation may begin.

DeepSeek must not set or override the final `ready_for_generation` result.

### 8.1.1 Complete normalized specification

The internal design specification should always contain these top-level fields:

```json
{
  "original_request": "Exact unmodified user prompt",
  "assessment_context": "self_verified",
  "module_name": null,
  "language_standard": null,
  "design_kind": null,
  "ports": [],
  "parameters": [],
  "clocking": [],
  "resets": [],
  "functional_requirements": [],
  "timing_requirements": [],
  "verification_requirements": [],
  "assumptions": [],
  "unresolved_questions": []
}
```

Use:

- A concrete value when known.
- An empty list `[]` when a category is confirmed not to apply.
- `null` when a scalar value is genuinely unknown.
- Never use an empty string as a substitute for missing information.

The controller supplies `assessment_context`; DeepSeek must not invent it.

Supported contexts:

| Context | Meaning |
|---|---|
| `self_verified` | The agent creates and runs its own development testbench |
| `external_testbench` | The final RTL must match an externally defined interface |
| `unknown` | The assessment mode has not been established |

For formal evaluation with a protected holdout testbench, use `external_testbench`.

### 8.1.2 Value provenance

Every value that may have been inferred should record its source.

Example explicit field:

```json
{
  "value": "active_low",
  "source": "explicit",
  "inference": null
}
```

Example inferred field:

```json
{
  "value": "posedge",
  "source": "inferred",
  "inference": {
    "basis": "Conventional synchronous RTL default",
    "candidate_risk": "medium"
  }
}
```

Valid sources:

```text
explicit
derived
inferred
controller_default
```

Definitions:

| Source | Meaning |
|---|---|
| `explicit` | Directly stated by the user |
| `derived` | Mathematically or logically determined from explicit requirements |
| `inferred` | Selected using a conventional interpretation |
| `controller_default` | Supplied by fixed project configuration |

DeepSeek may report a candidate risk and inference basis, but the local policy registry determines the authoritative risk.

### 8.1.3 Risk levels

Use three risk levels:

| Risk | Meaning | Required behaviour |
|---|---|---|
| `low` | A conventional choice does not change the required external interface or important observable behaviour | Infer automatically and record the assumption |
| `medium` | A conventional default exists, but the choice may affect observable behaviour | Infer with a warning for self-verification; require confirmation for external-testbench use |
| `high` | A wrong choice is likely to cause functional incompatibility or hidden-test failure | Require an explicit value from the user |

Before applying risk, first check whether the value is mathematically or logically derivable. A derived field does not need user clarification merely because the same field would otherwise be medium or high risk.

Decision rule:

```text
Explicit valid value
    → accept

Missing but mathematically/logically derivable
    → derive and record

Missing and low risk
    → infer and record

Missing and medium risk
    → infer with warning for self_verified
    → ask user for external_testbench

Missing and high risk
    → ask user
```

### 8.1.4 Authoritative field-risk registry

Store field policy locally, for example in:

```text
rtl_agent/risk_policy.py
```

DeepSeek must not be allowed to downgrade a field’s risk.

Recommended baseline policy:

#### General fields

| Field | Self-verified | External testbench |
|---|---:|---:|
| Original request | High | High |
| Module name | Medium | High |
| Language standard | Low | Low |
| Design kind | Low | Low |
| Assessment context | Controller-owned | Controller-owned |

#### Port fields

| Field | Self-verified | External testbench |
|---|---:|---:|
| Port name | Medium | High |
| Direction | Medium | High |
| Width | Medium | High |
| Signedness | Medium | High for arithmetic |
| Description | Low | Low |
| Port order | Low with named connections | Medium with positional connections |
| Observable default value | High | High |

#### Clock fields

| Field | Self-verified | External testbench |
|---|---:|---:|
| Whether a clock is required | Low when sequential behaviour is explicit | Medium |
| Clock signal name | Medium | High |
| Active edge | Medium | High |
| Number of clock domains | Medium | High |
| Clock frequency | Low for cycle-based behaviour | High when real-time behaviour is required |

#### Reset fields

| Field | Self-verified | External testbench |
|---|---:|---:|
| Reset signal name | High | High |
| Polarity | High | High |
| Synchronous or asynchronous | High | High |
| Assertion edge | High for asynchronous reset | High |
| Reset values or state | High | High |
| Reset priority | High | High |
| Reset-release behaviour | Medium | High |

#### Functional behaviour

| Field | Risk |
|---|---:|
| Main output behaviour | High |
| State-transition order | High |
| Enable behaviour | High |
| Overflow behaviour | High |
| Simultaneous-control priority | High |
| One-hot or mutual-exclusion requirement | High |
| Idle behaviour | Medium or high |
| Invalid-input behaviour | Medium or high |

#### Timing and latency

| Field | Risk |
|---|---:|
| State duration | High |
| Pipeline latency | High |
| Throughput | High when specified |
| Cycle-count reference point | Medium for self-test; high for external test |
| Registered versus combinational output | High |
| Handshake timing | High |
| Reference clock for cycle-based timing | High |

#### Parameters and verification

| Field | Risk |
|---|---:|
| Parameter name used externally | High |
| Parameter default value | High |
| Permitted parameter range | Medium or high |
| Mathematically derived internal width | Low |
| Verification cases derived from requirements | Low or medium |
| Expected verification values | High |
| Testbench clock period for cycle-based logic | Low |
| Testbench race-avoidance delays | Medium |
| Holdout test contents | Protected and never editable |

### 8.1.5 Example policy registry

```python
FIELD_RISK_POLICY = {
    "original_request": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "module_name": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "language_standard": {
        "self_verified": "low",
        "external_testbench": "low",
    },
    "design_kind": {
        "self_verified": "low",
        "external_testbench": "low",
    },
    "ports.*.name": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "ports.*.direction": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "ports.*.width": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "ports.*.signed": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "clocking.*.signal": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "clocking.*.edge": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
    "resets.*.signal": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "resets.*.polarity": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "resets.*.synchrony": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "resets.*.reset_values": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "functional_requirements": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "timing_requirements.*.duration": {
        "self_verified": "high",
        "external_testbench": "high",
    },
    "timing_requirements.*.reference_clock": {
        "self_verified": "medium",
        "external_testbench": "high",
    },
}
```

This registry is a starting policy and may be refined after testing. The important requirement is that it is local, deterministic, and version-controlled.

### 8.1.6 `save_design_spec` tool

Expose this tool before exposing RTL-writing tools:

```python
save_design_spec(specification: dict) -> dict
```

Purpose:

1. Receive DeepSeek’s complete draft.
2. Validate it against the JSON or Pydantic schema.
3. Validate engineering relationships between fields.
4. Apply derivation and inference policy.
5. Save a valid specification or invalid draft.
6. Return repair instructions or clarification questions.

The tool description supplied to DeepSeek should state:

```text
Submit a complete structured RTL design specification for local schema,
semantic, and field-risk validation. Valid specifications are saved as
design_spec.json. Invalid drafts are saved separately and returned with
repair instructions or user clarification questions. Do not generate RTL
until this tool returns ready_for_generation=true.
```

The tool must require all top-level specification fields, even when their values are `null` or empty lists.

### 8.1.7 Three-layer local validation

The local validator should distinguish three independent concepts:

```python
class ValidationResult:
    schema_valid: bool
    semantics_valid: bool
    ready_for_generation: bool
```

#### Layer 1 — Schema validation

Check:

- Required keys exist.
- Values have the correct data types.
- Enumerated values are permitted.
- Identifiers have legal syntax.
- Empty strings are rejected.
- Unexpected fields are rejected where appropriate.

#### Layer 2 — Engineering semantic validation

Check:

- Module and port names are valid Verilog identifiers.
- Port names are unique.
- Clock and reset signals refer to declared input ports.
- Reset polarity and assertion edge agree.
- Cycle-based timing has a declared reference clock.
- Reset values refer to declared outputs or internal state concepts.
- Sequential and FSM designs have clock definitions unless explicitly asynchronous.
- Requirements do not contradict one another.
- Every declared output has defined observable behaviour.
- Parameter-dependent widths and ranges are consistent.

#### Layer 3 — Readiness and risk validation

Check:

- No unresolved high-risk field remains.
- Medium-risk inference is allowed in the active assessment context.
- Every inference has a recorded basis.
- DeepSeek has not labelled a locally high-risk field as safely inferable.
- Required interface fields are explicit for external-testbench operation.

A specification can therefore be structurally valid but not ready:

```json
{
  "schema_valid": true,
  "semantics_valid": true,
  "ready_for_generation": false
}
```

### 8.1.8 Tool return structure

Return a structured result:

```json
{
  "validation_status": "valid_with_inferences",
  "schema_valid": true,
  "semantics_valid": true,
  "ready_for_generation": true,
  "saved_path": "runs/.../design_spec.json",
  "draft_path": null,
  "errors": [],
  "warnings": [],
  "derived_values": [],
  "accepted_inferences": [],
  "rejected_inferences": [],
  "clarification_questions": [],
  "normalized_specification": {}
}
```

Supported statuses:

| Status | Meaning |
|---|---|
| `valid` | Explicit and complete specification may proceed |
| `valid_with_inferences` | Permitted low- or medium-risk values were inferred and recorded |
| `model_repair_required` | DeepSeek produced malformed or internally inconsistent structured data |
| `user_clarification_required` | A critical value is absent or ambiguous |
| `rejected` | Requirements are contradictory or cannot be normalized safely |

### 8.1.9 Validator decision function

A simplified decision function is:

```python
def decide_missing_field(
    field_path: str,
    assessment_context: str,
    can_be_derived: bool,
) -> str:
    if can_be_derived:
        return "derive_and_record"

    risk = lookup_field_risk(
        field_path=field_path,
        assessment_context=assessment_context,
    )

    if risk == "low":
        return "infer_and_record"

    if risk == "medium":
        if assessment_context == "self_verified":
            return "infer_with_warning"
        return "ask_user"

    return "ask_user"
```

### 8.1.10 Repair versus clarification loop

Do not ask the user to correct errors caused by DeepSeek’s formatting.

#### Model repair

Example invalid value:

```json
{
  "direction": "in"
}
```

Local result:

```json
{
  "validation_status": "model_repair_required",
  "ready_for_generation": false,
  "errors": [
    {
      "path": "ports[0].direction",
      "code": "INVALID_ENUM",
      "message": "Expected input, output, or inout."
    }
  ]
}
```

The controller returns this tool result to DeepSeek and requests a corrected tool call.

#### User clarification

Example request:

```text
Build an 8-bit counter with reset.
```

For an external testbench, missing module name, port names, reset polarity, reset synchrony, and exact count behaviour are high risk.

Local result:

```json
{
  "validation_status": "user_clarification_required",
  "ready_for_generation": false,
  "clarification_questions": [
    {
      "field": "module_name",
      "question": "What module name should the external testbench instantiate?",
      "critical": true
    },
    {
      "field": "resets[0].polarity",
      "question": "Should reset be active-high or active-low?",
      "critical": true
    },
    {
      "field": "resets[0].synchrony",
      "question": "Should reset be synchronous or asynchronous?",
      "critical": true
    }
  ]
}
```

The terminal agent should combine related questions into one concise message rather than exposing raw schema details.

After the user answers:

1. Append the answer to the task conversation.
2. Ask DeepSeek to update the existing draft.
3. Call `save_design_spec` again.
4. Repeat until valid or explicitly rejected.

### 8.1.11 Derivable values

A missing field may be derived when the result follows unambiguously from explicit requirements.

For a maximum unsigned value \(M\), the minimum width is:

\[
W = \left\lceil \log_2(M+1) \right\rceil
\]

where:

- Counter width \(W\)（计数器位宽） is the required storage width; unit: bits（位）.
- Maximum value \(M\)（最大数值） is the largest required unsigned value; unit: integer counts（整数计数值）.

For \(N\) states, the minimum binary state width is:

\[
W_{\text{state}} = \left\lceil \log_2(N) \right\rceil
\]

where:

- State width \(W_{\text{state}}\)（状态位宽） is the number of state-register bits; unit: bits（位）.
- Number of states \(N\)（状态数量） is the number of distinct states; unit: states（个）.

Derived internal values should be stored in `derived_values` with their formulas and source requirements.

### 8.1.12 Generation gate

The controller must enforce:

```python
if not validation_result["ready_for_generation"]:
    block_rtl_generation()
```

The following tools must remain unavailable or reject calls until the design specification is ready:

```text
write_verilog_file
write_testbench_file
run_simulation
```

This prevents DeepSeek from bypassing specification validation.

### 8.1.13 Saved artifacts

Each run should preserve:

```text
design_spec.draft.json
design_spec.json
design_spec_validation.json
risk_policy_snapshot.json
```

Rules:

- `design_spec.draft.json` contains the latest invalid or incomplete proposal.
- `design_spec.json` exists only after readiness validation succeeds.
- `design_spec_validation.json` records all schema, semantic, inference, and clarification decisions.
- `risk_policy_snapshot.json` stores the exact policy used for the run.

### 8.1.14 Completion condition

This stage is complete when:

- DeepSeek has called `save_design_spec`.
- The local schema is valid.
- Engineering references are consistent.
- Every inference is recorded with its source and basis.
- No unresolved high-risk field remains.
- Any medium-risk inference is permitted by the active context.
- `ready_for_generation` is locally calculated as `true`.
- The normalized `design_spec.json` and validation report have been saved.

---

## 9. Stage 5 — Create, Validate, and Freeze the Verification Plan

Use the frozen `verification_prompt.md` together with the accepted `design_spec.json`.

This stage defines **what evidence is required to demonstrate correctness**. It must not generate Verilog or SystemVerilog testbench code.

### 9.1 Verification-plan structure

Every verification requirement must have a stable identifier:

```text
VP-001
VP-002
VP-003
```

Recommended structured form:

```json
{
  "plan_id": "traffic_light_ctrl_verification",
  "module_name": "traffic_light_ctrl",
  "requirements": [
    {
      "id": "VP-001",
      "title": "Asynchronous reset behaviour",
      "requirement_source": [
        "resets[0]",
        "functional_requirements[0]"
      ],
      "stimulus_intent": "Assert rst_n between active clock edges.",
      "expected_observations": [
        "green=1",
        "yellow=0",
        "red=0"
      ],
      "sampling_rule": "Observe before the next rising clock edge.",
      "priority": "required"
    }
  ]
}
```

Each required item should identify:

- Stable `VP-*` identifier.
- Source requirement in `design_spec.json`.
- Stimulus intent.
- Expected observable behaviour.
- Sampling or timing rule.
- Boundary or corner case.
- Whether the item is required or informational.

### 9.2 Required traffic-light coverage

For the example task, the plan should cover at least:

- Asynchronous reset immediately selecting Green.
- Reset counter value.
- Green lasting exactly three clock cycles.
- Yellow lasting exactly one clock cycle.
- Red lasting exactly four clock cycles.
- Transition back to Green.
- Repetition over multiple complete sequences.
- Exactly one light asserted at every observation.
- Reset asserted during active operation.

The plan should focus on observable behaviour rather than internal implementation choices such as a particular state encoding or counter architecture.

### 9.3 `save_verification_plan` tool

DeepSeek must call:

```python
save_verification_plan(plan: dict) -> dict
```

The local tool must:

- Validate the plan structure.
- Confirm every required design-spec requirement is covered.
- Confirm every `VP-*` identifier is unique.
- Reject references to nonexistent specification fields.
- Reject contradictory expected outcomes.
- Reject implementation-specific checks not justified by the specification.
- Save invalid drafts separately.
- Calculate `verification_plan_ready` locally.
- Save accepted forms as both JSON and human-readable Markdown.

Suggested tool result:

```json
{
  "validation_status": "valid",
  "verification_plan_ready": true,
  "saved_json_path": "runs/.../verification_plan.json",
  "saved_markdown_path": "runs/.../verification_plan.md",
  "errors": [],
  "warnings": [],
  "required_requirement_ids": [
    "VP-001",
    "VP-002",
    "VP-003"
  ]
}
```

### 9.4 Verification-plan gate

The controller must enforce:

```python
if not verification_result["verification_plan_ready"]:
    block_testbench_generation()
```

RTL generation may occur after the design specification is accepted, but testbench generation and simulation must remain blocked until the verification plan is accepted and frozen.

### 9.5 Frozen-plan rule

After acceptance:

- Save the plan and its hash.
- Do not permit DeepSeek to remove or weaken a requirement during reflection.
- Mechanical testbench repairs must still implement every frozen `VP-*` requirement.
- A plan change requires an explicit return to the verification-planning stage and must be logged as a specification-level revision, not an ordinary reflection.

### Completion condition

This stage is complete when:

- `save_verification_plan` succeeds.
- Every required design requirement has traceable verification coverage.
- `verification_plan_ready=true`.
- `verification_plan.json`, `verification_plan.md`, and their hash are saved.
- The frozen list of required `VP-*` identifiers is available to the testbench validator.

---

## 10. Stage 6 — Generate the Initial RTL and Development Testbench

### 10.1 Generate the DUT

Generate one complete synthesizable module matching the design contract.

The filename is derived from the generated module name:

```text
<module_name>.v
```

The model must return a complete replacement module, not an isolated patch.

### 10.2 Generate a self-checking development testbench using `testbench_prompt.md`

Only after the verification plan has been accepted and frozen should the controller load the frozen `testbench_prompt.md`.

Inputs to this stage are:

- Exact original user request.
- Accepted `design_spec.json`.
- Frozen `verification_plan.json`.
- Frozen `verification_plan.md`.
- Exact DUT module name and interface.
- Configured HDL language standard.
- Controlled tool definitions.

DeepSeek must translate every required `VP-*` item into executable HDL.

The testbench must:

- Instantiate the DUT using the exact validated module name.
- Use named port connections.
- Generate every required clock.
- Apply reset according to the validated synchrony and polarity.
- Exercise every frozen verification-plan item.
- Avoid reliance on DUT internal state encoding or counter implementation.
- Avoid race conditions when sampling nonblocking sequential updates.
- Use `!==` or equivalent checks so `x` and `z` values fail.
- Print `RTL_AGENT_TEST_FAIL` with the applicable `VP-*` identifier.
- Print `RTL_AGENT_TEST_PASS` exactly once, only after all required items pass.
- Finish automatically.
- Avoid GUI, waveform, or manual-inspection dependencies.

Suggested filename:

```text
tb_<module_name>.v
```

### 10.3 Plan-to-testbench traceability

DeepSeek must call:

```python
write_testbench_file(
    module_name: str,
    code_str: str,
    covered_requirements: list[str]
) -> dict
```

Example:

```json
{
  "module_name": "traffic_light_ctrl",
  "code_str": "<complete testbench source>",
  "covered_requirements": [
    "VP-001",
    "VP-002",
    "VP-003",
    "VP-004"
  ]
}
```

The local tool must compare `covered_requirements` against the frozen required list.

It must reject the tool call when:

- A required `VP-*` identifier is missing.
- An unknown identifier is supplied.
- The module name differs from `design_spec.json`.
- The source is empty or incomplete.
- The source contains an unconditional pass path.
- The pass marker can be reached without executing all required checks.
- The plan or testbench generation gate is closed.
- The testbench attempts to inspect prohibited DUT internals.
- The code attempts path traversal or arbitrary file access.

The tool should save:

```text
testbench_traceability.json
```

containing:

- Required plan identifiers.
- Declared covered identifiers.
- Missing identifiers.
- Unknown identifiers.
- Testbench source hash.
- Validation result.

### 10.4 External fixed testbench compatibility

If an external assessment harness later supplies a protected testbench, the agent-generated development testbench does not replace it. The external test is a separate final evaluation and is not editable by the agent.

### Completion condition

This stage is done when a complete DUT has been written, the frozen `testbench_prompt.md` has produced a complete self-checking testbench, and local traceability validation confirms that every required `VP-*` item is covered.

---

## 11. Stage 7 — Controlled Agent Tools

Expose only narrowly scoped tools. Tool availability should be state-dependent: specification tools are available first, while RTL writing and simulation remain blocked until the local generation gate opens.

### 11.1 Save and validate design specification

```python
save_design_spec(specification: dict) -> dict
```

The tool must:

- Validate structure and engineering semantics.
- Apply the authoritative local field-risk policy.
- Save invalid drafts separately from accepted specifications.
- Return model-repair errors or user clarification questions.
- Calculate `ready_for_generation` locally.
- Open the RTL-generation gate only after validation succeeds.

### 11.2 Save and validate verification plan

```python
save_verification_plan(plan: dict) -> dict
```

The tool must:

- Validate unique `VP-*` identifiers.
- Verify traceability to accepted design-spec requirements.
- Reject contradictory or unjustified expected outcomes.
- Save draft and accepted plan artifacts.
- Calculate `verification_plan_ready` locally.
- Open the testbench-generation gate only after validation succeeds.

### 11.3 Write RTL

```python
write_verilog_file(module_name: str, code_str: str) -> dict
```

The controller, not the model, determines the final path.

The tool should:

- Validate the module name.
- Reject path separators and traversal.
- Write UTF-8 text.
- Overwrite the current DUT atomically.
- Return path, byte count, hash, and error status.

### 11.4 Write development testbench

```python
write_testbench_file(
    module_name: str,
    code_str: str,
    covered_requirements: list[str],
) -> dict
```

The tool must:

- Apply the same path restrictions as the RTL-writing tool.
- Require the verification-plan gate to be open.
- Validate the module name against `design_spec.json`.
- Compare `covered_requirements` with the frozen required `VP-*` identifiers.
- Reject missing or unknown identifiers.
- Reject obvious unconditional pass-marker paths.
- Record the source hash.
- Save `testbench_traceability.json`.

### 11.5 Read current generated files

```python
read_current_design() -> dict
```

Return only the current task’s accepted design specification, frozen verification plan, DUT, testbench, traceability report, and latest simulation result.

### 11.6 Run simulation

```python
run_simulation() -> dict
```

Compile and execute using the configured simulator paths. Do not accept an arbitrary shell command from the model.

### 11.7 Prohibited unrestricted tools

Do not expose:

```text
run_shell(any_command)
write_file(any_path)
delete_file(any_path)
read_file(any_path)
```

### Completion condition

The agent can validate the design specification, then generate, inspect, write, and verify the current design without bypassing the generation gate or gaining unrestricted file-system or shell access.

---

## 12. Stage 8 — Compile and Simulate

### 12.1 Compile

Before compilation:

1. Delete any stale compiled simulation file.
2. Confirm the DUT and testbench exist.
3. Record their hashes.

Run the compiler through a direct subprocess argument list:

```powershell
iverilog.exe -g2012 -o simulation.vvp tb_<module_name>.v <module_name>.v
```

Capture:

- Complete command.
- Return code.
- Standard output.
- Standard error.
- Timeout state.
- Execution duration.

Do not execute `vvp.exe` if the current compilation fails.

### 12.2 Simulate

Run:

```powershell
vvp.exe simulation.vvp
```

Capture the same result fields.

### 12.3 Pass criteria

An internal development simulation passes only if:

1. Compilation succeeds.
2. Simulation starts successfully.
3. Simulation does not time out.
4. Output contains `RTL_AGENT_TEST_PASS`.
5. Output does not contain `RTL_AGENT_TEST_FAIL`.
6. No required verification-plan case reports failure.

A process return code of zero alone is insufficient.

### 12.4 Structured result

Return a structure such as:

```python
{
    "compile_succeeded": True,
    "compile_return_code": 0,
    "compile_stdout": "",
    "compile_stderr": "",
    "simulation_started": True,
    "simulation_return_code": 0,
    "simulation_stdout": "RTL_AGENT_TEST_PASS",
    "simulation_stderr": "",
    "timed_out": False,
    "passed": True,
    "failure_type": None
}
```

Suggested failure categories:

```text
compile_error
compile_timeout
simulation_error
simulation_timeout
functional_failure
missing_pass_marker
malformed_generated_code
api_error
file_system_error
```

### Completion condition

This stage is done when the controller has a reliable structured pass or failure result for the current generated design.

---

## 13. Stage 9 — Reflection and Correction Loop

### 13.1 Counter semantics

There is one initial generation and simulation, followed by at most five reflection cycles.

Therefore:

```text
Maximum reflection cycles: 5
Maximum simulation attempts: 6
```

Sequence:

```text
Initial generation
    ↓
Simulation attempt 1
    ↓ failure
Reflection cycle 1
    ↓
Simulation attempt 2
    ↓ failure
Reflection cycle 2
    ↓
Simulation attempt 3
    ↓ failure
Reflection cycle 3
    ↓
Simulation attempt 4
    ↓ failure
Reflection cycle 4
    ↓
Simulation attempt 5
    ↓ failure
Reflection cycle 5
    ↓
Simulation attempt 6
    ↓ failure
Terminate
```

API retries caused by transient network failures do not count as reflection cycles because no RTL correction has occurred.

### 13.2 Reflection process

After a failed development simulation, load the already frozen active copy of `reflection_prompt.md` and:

1. Read the frozen specification and verification plan.
2. Read the complete current DUT.
3. Read the complete current testbench.
4. Read compiler and simulator output.
5. Classify the failure.
6. Produce a concise engineering diagnosis.
7. Generate a complete corrected DUT.
8. Repair the development testbench only if the testbench itself is defective.
9. Save new source snapshots.
10. Rerun compilation and simulation.

### 13.3 Testbench integrity

The agent may repair the testbench when it:

- Does not compile.
- Instantiates the wrong generated port name.
- Contains a timing or clock-generation defect.
- Hangs due to a testbench bug.
- Cannot execute a frozen verification case.

It may not:

- Delete a failed functional case.
- Change the expected value to match incorrect RTL.
- Replace assertions with unconditional success.
- Remove requirements from the frozen verification plan.
- Print the pass marker without executing all required checks.

Record a diff whenever the testbench changes after the initial attempt.

### 13.4 Termination

Stop immediately when internal verification passes.

If simulation attempt 6 fails, terminate with:

- Final status `DEVELOPMENT_FAILED`.
- Last DUT.
- Last testbench.
- All six simulation results.
- Five reflection summaries.
- The final failure classification.

### Completion condition

The loop is done when internal verification passes or all five reflection cycles are exhausted.

---

## 14. Stage 10 — External Holdout Assessment

The agent will later be tested on many different natural-language prompts. Those assessment tasks are supplied after the agent has been built and are not encoded in its source.

For each assessment run:

1. The evaluator supplies one natural-language RTL request.
2. The agent performs its internal generation and development verification.
3. The evaluator takes the final RTL.
4. The evaluator runs a protected, unseen holdout testbench exactly once.

### Holdout success

If the protected holdout passes, mark the assessment run as `SUCCESS`.

### Holdout failure

If any final holdout test fails:

1. Capture the complete compiler and simulator error logs.
2. Preserve the final RTL and its hash.
3. Mark the run as `HOLDOUT_FAILED`.
4. End the entire current assessment run immediately.
5. Do not return the holdout testbench or failure to DeepSeek.
6. Do not permit another reflection cycle.
7. Do not modify or regenerate the RTL.
8. Do not retry the holdout automatically.

The next unrelated RTL task, when started as a separate assessment run, receives a fresh context and fresh reflection budget.

### Separation of responsibilities

The interactive agent owns:

- Natural-language interpretation.
- RTL generation.
- Development testbench generation.
- Development simulation.
- Reflection and correction.

The external assessment harness owns:

- Protected holdout testbench.
- One-time final evaluation.
- Immediate termination on holdout failure.
- Final assessment result.

---

## 15. Stage 11 — Logging and Artifacts

### 15.1 Run directory

Create one isolated directory per natural-language request:

```text
runs/
└── 2026-07-15_143500_counter_8bit/
    ├── counter_8bit.v
    ├── tb_counter_8bit.v
    ├── simulation.vvp
    ├── design_spec.draft.json
    ├── design_spec.json
    ├── design_spec_validation.json
    ├── risk_policy_snapshot.json
    ├── verification_plan.draft.json
    ├── verification_plan.json
    ├── verification_plan.md
    ├── verification_plan_validation.json
    ├── testbench_traceability.json
    ├── run_log.txt
    ├── api_calls.jsonl
    ├── prompts/
    │   ├── system_prompt.md
    │   ├── specification_prompt.md
    │   ├── verification_prompt.md
    │   ├── testbench_prompt.md
    │   └── reflection_prompt.md
    ├── prompt_hashes.json
    ├── rtl_versions/
    │   ├── attempt_01.v
    │   └── attempt_02.v
    ├── testbench_versions/
    │   ├── attempt_01.v
    │   └── attempt_02.v
    └── simulation_logs/
        ├── attempt_01.json
        └── attempt_02.json
```

### 15.2 Complete DeepSeek response storage

Store the complete response from every API call, including:

- Timestamp.
- Model identifier.
- Submitted messages.
- System prompt.
- User prompt.
- Tool definitions.
- `reasoning_content`.
- `content`.
- Tool calls.
- Tool-call arguments.
- Finish reason.
- Token usage.
- API request identifier, when available.
- API error body, when applicable.

Store this in machine-readable JSON or JSONL.

### 15.3 Human-readable execution log

`run_log.txt` should contain:

- Start and end timestamps.
- Masked configuration summary.
- User’s original prompt.
- Active prompt filenames and hashes.
- Exact snapshotted prompt versions used for the run.
- Draft and normalized design specifications.
- Field provenance, derived values, risk decisions, and clarification history.
- Frozen verification plan, plan identifiers, and plan-validation result.
- Plan-to-testbench traceability report.
- Attempt and reflection counters.
- Concise diagnoses.
- Tool names and sanitised arguments.
- Complete compiler output.
- Complete simulator output.
- File hashes.
- Final status and artifact locations.

### 15.4 Secret redaction

The API key may be stored in the prototype configuration file, but it must be redacted from:

- Terminal output.
- `run_log.txt`.
- `api_calls.jsonl`.
- Exception traces.
- Saved request headers.
- Holdout logs.

### Completion condition

The complete run can be reconstructed and diagnosed from saved artifacts without exposing the DeepSeek key.

---

## 16. Status Outcomes

Each interactive task should finish in one of these states:

| Status | Meaning |
|---|---|
| `SUCCESS_INTERNAL` | Generated RTL passed the agent’s development verification |
| `DEVELOPMENT_FAILED` | Five reflection cycles were exhausted |
| `CONFIGURATION_FAILED` | Startup dependency or API validation failed |
| `INFRASTRUCTURE_FAILED` | A non-RTL failure prevented valid simulation |
| `ABORTED` | The user intentionally stopped the run |

The external evaluator may replace `SUCCESS_INTERNAL` with:

| Status | Meaning |
|---|---|
| `SUCCESS` | Final RTL passed the protected holdout |
| `HOLDOUT_FAILED` | The one-time protected holdout failed |

---

## 17. Recommended Project Structure

```text
rtl-agent/
├── agent.py
├── requirements.txt
├── .gitignore
├── prompts/
│   ├── system_prompt.md
│   ├── specification_prompt.md
│   ├── verification_prompt.md
│   ├── testbench_prompt.md
│   └── reflection_prompt.md
├── rtl_agent/
│   ├── __init__.py
│   ├── config.py
│   ├── startup.py
│   ├── prompt_loader.py
│   ├── terminal_ui.py
│   ├── deepseek_client.py
│   ├── controller.py
│   ├── specification.py
│   ├── design_spec_schema.py
│   ├── design_spec_validator.py
│   ├── risk_policy.py
│   ├── verification.py
│   ├── verification_plan_schema.py
│   ├── verification_plan_validator.py
│   ├── testbench_validator.py
│   ├── tools.py
│   ├── simulator.py
│   ├── reflection.py
│   └── logger.py
├── runs/
└── .rtl-agent/
    └── config.json
```

Suggested prototype dependencies:

```text
openai
rich
prompt-toolkit
pydantic
```

The core simulator execution should use Python’s built-in `subprocess` module with direct argument lists, timeouts, and captured output.

---

## 18. Implementation Order

### Step 1 — Configuration subsystem

**Do:** Implement first-run setup, config loading, config saving, and masked display.

**Depends on:** Nothing.

**Done when:** The API key and two simulator paths survive application restart.

### Step 2 — Prompt subsystem

**Do:** Create the five Markdown prompt files, implement `prompt_loader.py`, validate prompt paths, reject missing or empty prompts, calculate prompt hashes, and snapshot prompts per run.

**Depends on:** Nothing.

**Done when:** The agent uses separate editable files for system, specification, verification planning, testbench generation, and reflection.

### Step 3 — Startup preflight

**Do:** Validate both executables, run the smoke test, validate the DeepSeek key, validate all five prompt files, and check writable directories.

**Depends on:** Steps 1–2.

**Done when:** The main prompt opens only after every mandatory check passes.

### Step 4 — Terminal interface

**Do:** Implement the persistent natural-language prompt and slash commands, including `/prompts`, `/show-prompt`, and `/reload-prompts`.

**Depends on:** Steps 1–3.

**Done when:** The user can enter a free-form request, inspect prompt locations, and issue `/doctor`, `/config`, `/status`, and `/quit`.

### Step 5 — DeepSeek client

**Do:** Implement V4-Pro thinking-mode requests, external prompt injection, stage-specific tool availability, complete response storage, and reasoning-content preservation across tool-call turns.

**Depends on:** Steps 1–2.

**Done when:** DeepSeek receives the correct frozen prompt for each stage and can request controlled tools without losing context.

### Step 6 — Run workspace and logging

**Do:** Create isolated run directories, snapshot active prompts, save prompt hashes, create JSONL API logs, version source files, and maintain `run_log.txt`.

**Depends on:** Steps 2 and 4.

**Done when:** Every generated artifact, prompt version, validation result, and execution result is attributable to one task run.

### Step 7 — Design-spec schema and provenance model

**Do:** Define the normalized design-spec schema, provenance values, identifier rules, and empty-list versus `null` conventions.

**Depends on:** Steps 5–6.

**Done when:** DeepSeek can submit a predictable structured draft and invalid empty strings are rejected.

### Step 8 — Local field-risk policy

**Do:** Implement `risk_policy.py` with low, medium, and high risk levels based on field path and assessment context.

**Depends on:** Step 7.

**Done when:** Risk decisions are deterministic and cannot be overridden by the model.

### Step 9 — `save_design_spec` and validation pipeline

**Do:** Implement schema validation, engineering semantic validation, derivation, risk evaluation, draft saving, normalized-spec saving, and structured tool results.

**Depends on:** Steps 7–8.

**Done when:** The tool distinguishes valid, valid-with-inferences, model-repair-required, user-clarification-required, and rejected specifications.

### Step 10 — Clarification and model-repair controller

**Do:** Return formatting defects to DeepSeek, combine critical ambiguity questions for the user, append answers, and resubmit until valid or rejected.

**Depends on:** Step 9.

**Done when:** The user is asked only for genuinely high-risk information.

### Step 11 — Design-spec generation gate

**Do:** Block RTL, verification-plan, testbench, and simulation stages until `ready_for_generation=true`.

**Depends on:** Steps 9–10.

**Done when:** DeepSeek cannot bypass specification validation.

### Step 12 — Verification-plan schema and `save_verification_plan`

**Do:** Define stable `VP-*` identifiers, traceability to design-spec requirements, expected observations, sampling rules, and local plan validation.

**Depends on:** Step 11.

**Done when:** The plan is saved as JSON and Markdown and `verification_plan_ready=true`.

### Step 13 — Verification-plan gate

**Do:** Block testbench generation and simulation until the verification plan is accepted and frozen.

**Depends on:** Step 12.

**Done when:** No testbench can be written against an unvalidated or changing plan.

### Step 14 — RTL generation and controlled RTL writing

**Do:** Generate a complete DUT from `design_spec.json` and save it through the restricted RTL-writing tool.

**Depends on:** Step 11.

**Done when:** The complete DUT is saved and versioned.

### Step 15 — Testbench generation prompt and traceability validator

**Do:** Use `testbench_prompt.md` to convert the frozen plan into a complete testbench and implement `covered_requirements` validation, marker checks, and `testbench_traceability.json`.

**Depends on:** Steps 12–14.

**Done when:** Every required `VP-*` item is mapped to executable testbench coverage.

### Step 16 — Simulator wrapper

**Do:** Implement separate compile and execution stages with timeouts, stale-binary deletion, complete output capture, and structured result classification.

**Depends on:** Steps 3, 14, and 15.

**Done when:** Compilation errors, functional failures, timeouts, and verified passes are correctly distinguished.

### Step 17 — Initial end-to-end generation flow

**Do:** Progress one natural-language prompt through design-spec validation, verification-plan validation, RTL generation, testbench generation, traceability validation, compilation, and simulation.

**Depends on:** Steps 5–16.

**Done when:** One arbitrary request completes an initial simulation attempt.

### Step 18 — Reflection loop

**Do:** Use the frozen `reflection_prompt.md` for up to five post-failure reflection cycles while preserving the design specification and verification plan.

**Depends on:** Step 17.

**Done when:** The agent stops on success or after six total simulation attempts.

### Step 19 — External assessment interface

**Do:** Expose final RTL and structured status so an independent harness can execute one unseen holdout.

**Depends on:** Steps 6 and 18.

**Done when:** Holdout failure terminates the assessment without returning control to the LLM.

### Step 20 — Generality, traceability, and prompt-change testing

**Do:** Test unrelated RTL requests, incomplete specifications, plan-validation failures, missing `VP-*` coverage, defective testbench mechanics, and prompt-file changes.

**Depends on:** Steps 1–19.

**Done when:** The same agent handles different designs and enforces the full specification-plan-testbench chain without hardcoded tasks.

---

## 19. Final Acceptance Criteria

The prototype is complete when all of the following are true:

1. First launch requests only the DeepSeek key, `iverilog.exe` path, and `vvp.exe` path.
2. The configuration is saved locally and loaded on later launches.
3. Every launch validates the compiler, runtime, their combined operation, and the DeepSeek API.
4. The main interface is terminal-native.
5. The user can enter an arbitrary natural-language RTL request directly.
6. No predefined task manifest or task catalogue is required.
7. DeepSeek-V4-Pro is used with thinking mode enabled and reasoning effort set to `max`.
8. Tool-call turns preserve the required `reasoning_content`.
9. The agent dynamically creates the design contract and verification plan.
10. The agent generates both RTL and a self-checking development testbench.
11. Icarus Verilog compilation and VVP execution are performed automatically.
12. Compilation and simulation outputs are captured completely.
13. Internal success requires explicit functional pass evidence, not merely return code zero.
14. The initial simulation may be followed by at most five reflection cycles.
15. The maximum number of internal simulation attempts is six.
16. Testbench changes cannot weaken the frozen functional requirements.
17. Complete DeepSeek responses are stored.
18. The API key is redacted from all logs.
19. Each new natural-language request receives a fresh run directory and reflection budget.
20. An external unseen holdout may run once after internal success.
21. Holdout failure is logged and ends the current assessment immediately without further correction.
22. The same completed agent can be evaluated with many different RTL requests that were not known during implementation.
23. All system and stage-specific instructions are stored in the top-level `prompts/` directory.
24. The Python source does not contain duplicated long-form system prompts that can diverge from the Markdown files.
25. Missing or empty prompt files prevent startup rather than triggering a silent fallback.
26. Every task loads and freezes its prompt versions before the first DeepSeek API call.
27. Each run directory contains the exact five prompt files used and their hashes.
28. Editing a master prompt affects the next task without requiring Python source changes or an application reinstall.
29. Prompt changes made during an active task do not alter that task’s remaining reflection cycles.
30. DeepSeek must submit a structured draft through `save_design_spec` before RTL generation.
31. The local controller, not DeepSeek, calculates `ready_for_generation`.
32. The design specification records whether each inferable value is explicit, derived, inferred, or supplied by a controller default.
33. The authoritative low/medium/high field-risk policy is stored locally and snapshotted per run.
34. DeepSeek cannot downgrade a locally high-risk field to make it inferable.
35. Missing derivable values are calculated and recorded rather than unnecessarily asked of the user.
36. Low-risk omissions may be inferred and recorded.
37. Medium-risk omissions are inferred only when permitted by the active assessment context.
38. High-risk omissions block generation and produce focused user clarification questions.
39. Model formatting and schema errors are returned to DeepSeek rather than presented to the user as requirement questions.
40. RTL-writing and simulation tools remain blocked until `ready_for_generation=true`.
41. Each run preserves draft and accepted specifications, the validation report, and the exact risk-policy snapshot.
42. Schema validity, engineering semantic validity, and readiness are reported separately.
43. Verification planning and testbench generation use separate prompt files and separate API stages.
44. `verification_prompt.md` must not generate HDL testbench code.
45. `testbench_prompt.md` must treat the accepted design specification and frozen verification plan as authoritative.
46. Every required verification item has a unique stable `VP-*` identifier.
47. `save_verification_plan` validates traceability, uniqueness, expected outcomes, and plan completeness before freezing the plan.
48. Testbench generation and simulation remain blocked until `verification_plan_ready=true`.
49. `write_testbench_file` requires `covered_requirements` and rejects missing or unknown `VP-*` identifiers.
50. Every run preserves the verification-plan draft, accepted JSON and Markdown plans, plan-validation report, and testbench traceability report.
51. The development testbench prints the relevant `VP-*` identifier in functional failure diagnostics.
52. The pass marker cannot be accepted unless every frozen required verification item has executed successfully.
53. Reflection may repair testbench mechanics but may not modify the frozen verification plan or weaken any expected result.
