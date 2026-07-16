# Autonomous RTL Generation & Verification Agent

A terminal agent that turns a plain-English hardware request into **synthesizable
Verilog**, generates (or uses your own) testbench, and **compiles + simulates** it
with Icarus Verilog, self-correcting until it passes.

---

## 1. Download

Get the code onto your machine:

```powershell
git clone <your-repo-url> Autonomous_RTL_Gen_Agent
cd Autonomous_RTL_Gen_Agent
```

(or download the ZIP from the repo page and extract it, then `cd` into the folder).

Install the Python dependencies (Python **3.11+**):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

You also need **Icarus Verilog** installed (provides `iverilog.exe` and `vvp.exe`).
The default install location this agent expects is `C:\msys64\ucrt64\bin\`.

---

## 2. Configure (Icarus paths + DeepSeek key)

The **first time** you run the agent it walks you through a one-time setup asking
for exactly three things:

```powershell
python agent.py
```

```
RTL Agent First-Time Setup

DeepSeek API key: ********************************         <- paste your key
Icarus Verilog path [C:\msys64\ucrt64\bin\iverilog.exe]:  <- Enter to accept default
VVP runtime path   [C:\msys64\ucrt64\bin\vvp.exe]:        <- Enter to accept default
```

- **DeepSeek API key** - required (used for `deepseek-v4-pro`).
- **iverilog path** / **vvp path** - press Enter to accept the defaults above, or
  paste your own paths if Icarus is installed elsewhere.

These are saved to `.rtl-agent/config.json` (gitignored; the key is masked in all
output and redacted from every log). On later launches setup is skipped.

**Changing config later:** inside the agent use `/config` to view, or
`/config key`, `/config iverilog`, `/config vvp` to update and re-check a value.
If a value is wrong, the startup preflight fails and offers `[R]etry / [C]onfigure
/ [Q]uit` instead of continuing.

---

## 3. Run

After setup, the agent runs a preflight (checks the compiler, runtime, an
end-to-end simulation smoke test, your API key, prompt files) and then asks how to
verify the generated RTL this session:

```
? How should generated RTL be verified this session?
 > 1. Artificial testbench  (the agent generates and self-checks one)
   2. Real testbench        (you provide the testbench file)
```

- **Artificial** - the agent writes its own self-checking testbench.
- **Real** - you give it a `.v` testbench path; it then generates ONLY the DUT and
  runs it against your testbench. (You can switch anytime with `/testbench <path>`
  or `/testbench off`.)

Then type (or paste) a request at the prompt:

```
rtl-agent> Build an 8-bit synchronous up-counter with an active-low asynchronous
reset and an enable input. Wrap from 255 to 0.
```

The agent creates the spec, generates RTL + testbench, compiles and simulates, and
reflects up to 5 times on failure. When done it prints the final status and the
paths to your files.

---

## 4. Where your generated files are

Every request creates its own **run directory** under `runs/`:

```
runs/2026-07-16_010824_<short-name>/
├── <module_name>.v            <- THE GENERATED RTL (the .v you want)
├── tb_<module_name>.v         <- agent's testbench   (artificial mode)
│   or  external_testbench.v   <- copy of YOUR testbench (real mode)
├── run_log.txt                <- full human-readable execution log  (see below)
├── design_spec.json           <- the structured design contract
├── verification_plan.json/.md <- the verification plan (artificial mode)
├── simulation.vvp             <- compiled simulation binary
├── api_calls.jsonl            <- raw DeepSeek request/response records
├── rtl_versions/              <- every RTL attempt (attempt_01.v, attempt_02.v, ...)
├── testbench_versions/
└── simulation_logs/           <- per-attempt compile/sim results (JSON)
```

- **The generated RTL** is `runs/<timestamp>_<name>/<module_name>.v` (e.g.
  `traffic_light_ctrl.v`). The final status line printed in the terminal also shows
  this exact path as `RTL: ...`.
- The agent prints `Run directory: ...` and `RTL: ...` / `Testbench: ...` at the
  end of each task so you can copy the paths directly.

### Where `run_log.txt` is stored

`run_log.txt` lives **inside each run directory**:

```
runs/<timestamp>_<name>/run_log.txt
```

It is a complete, human-readable transcript of that run: the system prompt, each
stage's instruction, every model turn (full thinking + response + tool-call
parameters), every tool result (with compile/simulation stdout/stderr), any
clarification Q&A, each reflection cycle, and a final result block. The raw
machine-readable version is alongside it in `api_calls.jsonl`.

You can also view files without leaving the terminal: `/show-rtl` style commands
are available via `/show-prompt`, and the run directory path is printed after every
task.

---

## Command reference

| Command | Action |
|---|---|
| `<text>` | Start a new RTL task from a natural-language request |
| `/testbench <path>` | Use a real testbench (DUT-only, no verification generated) |
| `/testbench off` | Clear the real testbench (back to self-verified) |
| `/doctor` | Re-run the startup preflight |
| `/config` | Show tool paths, masked API key, and current testbench mode |
| `/config key` \| `iverilog` \| `vvp` | Replace one value and re-check |
| `/prompts` / `/show-prompt <name>` | Show the prompt directory / one prompt |
| `/reload-prompts` | Reload editable prompts for the next task |
| `/summary on\|off` | Toggle the parallel per-stage summary agent |
| `/help` | List commands |
| `/quit` | Exit |

While a task runs, press **Esc** to terminate it and restore your prompt for editing.

---

## Notes

- **Config & runs are local and gitignored** - `.rtl-agent/` (your key + paths) and
  `runs/` are never committed.
- **API key safety** - masked in the terminal and redacted from `run_log.txt`,
  `api_calls.jsonl`, and any saved request headers.
- **Manual simulation** - you can always compile/run a pair yourself:
  ```powershell
  iverilog.exe -g2012 -o sim.vvp <testbench>.v <module>.v
  vvp.exe sim.vvp
  ```
- **Real-testbench pass/fail** is judged from the testbench's own output (it won't
  print the agent's internal markers): a non-zero exit code or failure keywords
  (`error`, `fail`, `mismatch`, ...) means fail; a clean run or an explicit success
  message means pass.

## License

See [LICENSE](LICENSE).
