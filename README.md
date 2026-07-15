# Autonomous RTL Generation & Verification Agent

A terminal-native AI agent that turns a plain-English hardware request into
**synthesizable Verilog plus a self-checking testbench**, then compiles and
simulates it with Icarus Verilog and **self-corrects** over up to five
reflection cycles — all from one prompt.

```
rtl-agent> Build an 8-bit synchronous up-counter with an active-low
asynchronous reset and an enable input. Wrap from 255 to 0.

[Agent] Interpreting the specification...
    tool: save_design_spec -> valid (ready=True)
[Agent] Creating a verification plan...
    tool: save_verification_plan -> ready=True, 6 required check(s)
[Agent] Generating RTL and self-checking testbench...
    tool: write_verilog_file -> counter_8bit.v (412 bytes)
    tool: write_testbench_file -> ok, covers 6 check(s)
    tool: run_simulation -> PASS
[Agent] [Simulation attempt 1/6] PASS

Final status: SUCCESS_INTERNAL
```

The agent is **task-agnostic**: there is no built-in catalogue of designs. Any
natural-language RTL request works, and the same agent can later be evaluated
against unseen holdout testbenches.

---

## How it works

Each request flows through a gated pipeline. The **local Python controller — not
the model — is the authority**: it validates every artifact and decides when the
next stage may begin. The model proposes; the controller disposes.

```
Natural-language request
        |
  Design specification   -->  save_design_spec   (schema + semantics + field-risk)
        |  (generation gate)
  Verification plan       -->  save_verification_plan  (unique VP-* ids, traceability)
        |  (verification gate)
  RTL + self-checking TB  -->  write_verilog_file / write_testbench_file
        |
  Compile & simulate      -->  run_simulation (iverilog -> vvp)
        |  fail
  Reflection loop         -->  up to 5 cycles (max 6 simulation attempts)
        |
  SUCCESS_INTERNAL / DEVELOPMENT_FAILED
```

Key guarantees:

- **Staged tool gating.** RTL-writing and simulation stay blocked until the design
  spec is accepted; the testbench and simulation stay blocked until the
  verification plan is frozen.
- **Field-risk policy.** Missing details are auto-derived or inferred when safe,
  and only genuinely high-risk gaps (reset polarity/synchrony, etc.) prompt you
  for clarification — as a selectable menu, not free-text guessing.
- **Evidence-based pass.** A run passes only if the simulation prints
  `RTL_AGENT_TEST_PASS` and never `RTL_AGENT_TEST_FAIL` — a zero exit code alone
  is not enough.
- **Frozen prompts & plans.** All agent instructions live in editable Markdown
  files under `prompts/`; each task snapshots and hashes the versions it used.

---

## Requirements

- **Python 3.11+**
- **Icarus Verilog** (`iverilog.exe` + `vvp.exe`) — default paths
  `C:\msys64\ucrt64\bin\`
- **A DeepSeek API key** (uses `deepseek-v4-pro` in thinking mode via the
  OpenAI-compatible API)

> Primarily developed and tested on **Windows**. The Esc-to-terminate feature uses
> `msvcrt`; on other platforms it is a no-op and Ctrl-C interrupts instead.

## Install & run

```powershell
# from a fresh checkout of this repo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python agent.py
```

On first launch a short wizard asks for three values (press Enter to accept the
default `iverilog`/`vvp` paths):

```
DeepSeek API key: ********************************
Icarus Verilog path [C:\msys64\ucrt64\bin\iverilog.exe]:
VVP runtime path   [C:\msys64\ucrt64\bin\vvp.exe]:
```

It then runs a mandatory preflight (compiler, runtime, an end-to-end simulator
smoke test, API key, prompt files, writable directories). The `rtl-agent>` prompt
only opens once every check passes. If a check fails you get
`[R]etry / [C]onfigure / [Q]uit` and can fix the config in place.

Then just type (or paste) a request.

## Terminal features

- **Multi-line paste** — paste a whole spec block; it is captured as one request
  (bracketed paste; needs a modern terminal such as Windows Terminal).
- **Live status bar** — shows the current stage and a throttled peek at what the
  model is generating.
- **Per-step summaries** — a concise line after each tool call, plus an optional
  **parallel summary agent** that recaps each reasoning step (runs in the
  background with its own stateless client — no shared conversation history).
- **Esc to terminate** — abort a running task and get your prompt text restored
  for editing.
- **Selectable clarifications** — categorical questions are arrow-key menus.

### Commands

| Command | Action |
|---|---|
| `<text>` | Start a new RTL task from a natural-language request |
| `/doctor` | Re-run the startup preflight |
| `/config` | Show tool paths and masked API-key status |
| `/config key` \| `iverilog` \| `vvp` | Replace one value and re-check |
| `/prompts` | Show the prompt directory and filenames |
| `/show-prompt <name>` | Print one active prompt (e.g. `system`, `reflection`) |
| `/reload-prompts` | Reload master prompts for the next task |
| `/summary on\|off` | Toggle the parallel per-step summary agent |
| `/help` | List commands |
| `/quit` | Exit |

## Project layout

```
agent.py                      # entry point
requirements.txt
prompts/                      # editable Markdown instructions (never hardcoded)
  system_prompt.md            #  - only this one is sent as API role "system"
  specification_prompt.md
  verification_prompt.md
  testbench_prompt.md
  reflection_prompt.md
rtl_agent/
  config.py                   # first-run wizard, load/save, API-key masking
  startup.py                  # preflight checks
  prompt_loader.py            # path-safe loader, hashing
  deepseek_client.py          # thinking-mode client + streaming + reasoning preservation
  controller.py               # the pipeline + gates + reflection loop
  design_spec_schema.py       # normalized spec + provenance
  design_spec_validator.py    #   3-layer: schema / semantics / readiness+risk
  risk_policy.py              # authoritative local field-risk registry
  verification_plan_validator.py
  testbench_validator.py      # covered_requirements + pass-marker checks
  tools.py                    # gated, narrowly-scoped tool dispatch
  simulator.py                # iverilog/vvp subprocess wrapper
  reflection.py               # counter semantics (1 initial + 5 reflections)
  summarizer.py               # parallel, stateless summary agent
  terminal_ui.py              # REPL, status bar, selection widgets
  logger.py                   # per-run workspace + logging
runs/                         # one folder per task (gitignored)
.rtl-agent/config.json        # local config incl. API key (gitignored)
```

## Run artifacts

Every request creates an isolated `runs/<timestamp>_<name>/` containing the
design spec, verification plan (JSON + Markdown), validation reports, the DUT and
testbench, per-attempt version snapshots, simulation logs, the frozen prompt
snapshots and their hashes, `run_log.txt`, and `api_calls.jsonl`. A run can be
fully reconstructed from these artifacts, and the API key is redacted from all of
them.

## Final statuses

| Status | Meaning |
|---|---|
| `SUCCESS_INTERNAL` | Generated RTL passed the agent's development verification |
| `DEVELOPMENT_FAILED` | Five reflection cycles exhausted without passing |
| `CONFIGURATION_FAILED` | Startup dependency or API validation failed |
| `INFRASTRUCTURE_FAILED` | A non-RTL failure prevented a valid simulation |
| `ABORTED` | You terminated the run (Esc) |

## Notes

- **Fixed model config.** `deepseek-v4-pro`, thinking enabled, reasoning effort
  `max`. Sampling controls (e.g. `temperature`) are not sent while thinking is on.
- **API-key storage.** For this prototype the key is stored in
  `.rtl-agent/config.json` (gitignored) and redacted from all logs and prompts.
- **Summary agent cost.** The parallel summary agent makes one lightweight extra
  API call per reasoning step; disable it with `/summary off` if you want to save
  tokens.
- **Manual simulation.** You can compile/run any pair directly:
  ```powershell
  iverilog.exe -g2012 -o sim.vvp tb_<module>.v <module>.v
  vvp.exe sim.vvp
  ```

## License

See [LICENSE](LICENSE).
