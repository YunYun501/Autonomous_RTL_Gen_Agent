"""Terminal-native interface.

A persistent REPL: natural-language entries start a new RTL task; slash commands
handle configuration, prompt inspection, and diagnostics. Plain-text output only
(no rich, no Unicode glyphs) so it never fights the terminal.
"""

from __future__ import annotations

import getpass
import re
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path

from . import config as config_mod
from . import prompt_loader
from . import prompt_select
from .config import Config
from .controller import Controller
from .deepseek_client import DeepSeekClient
from .interrupt import CancelWatcher
from .startup import run_preflight, PreflightReport
from .summarizer import SummaryAgent


def out(msg: str = "") -> None:
    print(msg)


class StatusDisplay:
    """A plain-text status line pinned to the bottom while a task runs.

    Shows the current stage plus a rolling peek at what DeepSeek is streaming,
    updated in place with a carriage return. Each stage is left on its own line as
    history when the next stage begins. ASCII only.
    """

    _KIND_LABEL = {"reasoning": "thinking", "content": "writing", "tool": "tool"}
    # Minimum seconds between live redraws (throttles the fast-scrolling preview).
    _MIN_REDRAW_INTERVAL = 0.16

    def __init__(self):
        self._stage = ""
        self._kind = ""
        self._buf = ""
        self._active = False  # currently on an unfinished (live) line
        self._tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._lock = threading.RLock()  # note() is called from summary worker threads
        self._last_draw = 0.0

    def __enter__(self) -> "StatusDisplay":
        return self

    def __exit__(self, *exc) -> None:
        with self._lock:
            self._finalize()

    def _width(self) -> int:
        try:
            return max(20, shutil.get_terminal_size().columns)
        except Exception:  # noqa: BLE001
            return 80

    def _line(self) -> str:
        text = f"[Agent] {self._stage}"
        if self._buf:
            label = self._KIND_LABEL.get(self._kind, self._kind)
            text += f"  {label}: {self._buf}"
        w = self._width() - 1
        if len(text) > w:
            text = text[: w - 3] + "..."
        return text.ljust(w)  # pad to overwrite any leftover characters

    def _redraw(self, force: bool = False) -> None:
        if not self._tty:
            return
        now = time.monotonic()
        if not force and (now - self._last_draw) < self._MIN_REDRAW_INTERVAL:
            return  # throttle: skip this frame, the buffer already holds latest text
        self._last_draw = now
        sys.stdout.write("\r" + self._line())
        sys.stdout.flush()
        self._active = True

    def _finalize(self) -> None:
        if self._active:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._active = False

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._finalize()  # freeze the previous stage line as history
            self._stage = stage
            self._kind = ""
            self._buf = ""
            if self._tty:
                self._redraw(force=True)
            else:
                print(f"[Agent] {stage}")

    def set_preview(self, kind: str, delta: str) -> None:
        if not self._tty:
            return
        with self._lock:
            force = kind != self._kind
            if force:
                self._kind = kind
                self._buf = ""
            combined = (self._buf + delta).replace("\r", " ").replace("\n", " ")
            self._buf = re.sub(r"[ \t]{2,}", " ", combined)[-160:]
            self._redraw(force=force)

    def note(self, text: str) -> None:
        """Print a persistent summary line above the live status bar (thread-safe)."""
        with self._lock:
            was_active = self._active
            self._finalize()
            print(text)
            sys.stdout.flush()
            if was_active:
                self._redraw(force=True)

    def pause(self) -> None:
        with self._lock:
            self._finalize()

    def resume(self) -> None:
        with self._lock:
            if self._stage and self._tty:
                self._redraw(force=True)


try:  # paste-friendly line editing (bracketed paste + history)
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    _HAS_PTK = True
except Exception:  # noqa: BLE001
    _HAS_PTK = False


BANNER = "RTL Agent startup checks"


def _print_report(report: PreflightReport) -> None:
    for r in report.results:
        tag = "[PASS]" if r.passed else "[FAIL]"
        out(f"{tag} {r.name}" + (f" - {r.detail}" if r.detail else ""))


class TerminalUI:
    def __init__(self):
        self.config: Config | None = None
        self.prompts: dict[str, str] | None = None
        self._prefill: str = ""  # restores the prompt text after an Esc abort
        self._summaries_enabled: bool = True  # parallel per-step summary agent
        self._external_tb: str | None = None  # real testbench path, if provided

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> int:
        out(BANNER)
        if not config_mod.config_exists():
            out("No configuration found - starting first-time setup.\n")
            self.config = config_mod.run_first_time_setup()
        else:
            self.config = config_mod.load_config()
            if self.config is None:
                out("[FAIL] Configuration file is corrupt. Reconfiguring.")
                self.config = config_mod.run_first_time_setup()

        if not self._preflight_gate():
            return 1

        try:
            self.prompts = prompt_loader.load_all_prompts()
        except prompt_loader.PromptLoadError as exc:
            out(f"[FAIL] Prompt files: {exc}")
            return 1

        out("\nModel: deepseek-v4-pro")
        out("Thinking mode: enabled")
        out("Reasoning effort: max")

        self._choose_testbench_mode()

        out("\nRTL Agent ready. Type a request, or /help for commands.")
        out("(You can switch modes anytime with /testbench <path> or /testbench off.)\n")
        return self._repl()

    def _choose_testbench_mode(self) -> None:
        """One-time verification-mode choice presented at session start."""
        out("")
        choice = prompt_select.select_option(
            "How should generated RTL be verified this session?",
            [
                "Artificial testbench  (the agent generates and self-checks one)",
                "Real testbench        (you provide the testbench file)",
            ],
            allow_other=False,
        )
        if choice and choice.startswith("Real"):
            self._prompt_for_testbench_path()
        else:
            self._external_tb = None
            out("Mode: artificial testbench - the agent will generate and self-check its own.")

    def _prompt_for_testbench_path(self) -> None:
        while True:
            raw = prompt_select.ask_free_text("Path to your testbench .v file (Enter to skip)")
            if not raw:
                self._external_tb = None
                out("No path given; using an artificial testbench instead.")
                return
            path = Path(raw.strip().strip('"').strip("'")).expanduser()
            if path.is_file():
                self._external_tb = str(path)
                out(f"Mode: real testbench -> {path}")
                out("The agent will generate ONLY the DUT and run it against this testbench;")
                out("no verification plan or agent testbench will be generated.")
                return
            out(f"[FAIL] Not a file: {path}. Try again, or press Enter to use artificial.")

    def _preflight(self, skip_api: bool = False) -> bool:
        report = run_preflight(self.config, skip_api=skip_api)
        _print_report(report)
        return report.passed

    def _preflight_gate(self) -> bool:
        """Run the preflight, offering Retry/Reconfigure/Quit until it passes."""
        while True:
            if self._preflight():
                return True
            out("\nOne or more mandatory checks failed.")
            out("  [R] Retry   [C] Reconfigure   [Q] Quit")
            try:
                choice = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if choice == "q":
                return False
            if choice == "c":
                self._reconfigure()
            out("")  # blank line, then retry

    def _reconfigure(self) -> None:
        out("\nReconfigure:")
        out("  [1] DeepSeek API key")
        out("  [2] iverilog path")
        out("  [3] vvp path")
        out("  [4] Everything (full setup)")
        try:
            choice = input("Select > ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if choice == "4":
            self.config = config_mod.run_first_time_setup()
        else:
            self._reconfigure_field(choice)

    def _reconfigure_field(self, choice: str) -> None:
        if choice == "1":
            key = getpass.getpass("DeepSeek API key: ").strip()
            if key:
                self.config.deepseek_api_key = key
                config_mod.save_config(self.config)
                out("API key updated.")
        elif choice == "2":
            path = input(f"iverilog path [{self.config.iverilog_path}]: ").strip()
            if path:
                self.config.iverilog_path = path
                config_mod.save_config(self.config)
                out("iverilog path updated.")
        elif choice == "3":
            path = input(f"vvp path [{self.config.vvp_path}]: ").strip()
            if path:
                self.config.vvp_path = path
                config_mod.save_config(self.config)
                out("vvp path updated.")

    # -- REPL ---------------------------------------------------------------
    def _repl(self) -> int:
        # A prompt_toolkit session gives bracketed paste, so a pasted multi-line
        # block is captured as ONE request instead of being split across lines.
        toolbar = lambda: "deepseek-v4-pro | thinking:max | Enter to run | Esc cancels a running task"
        session = PromptSession(history=InMemoryHistory()) if _HAS_PTK else None
        while True:
            prefill = self._prefill
            self._prefill = ""
            try:
                if session is not None:
                    line = session.prompt(
                        "rtl-agent> ", default=prefill, bottom_toolbar=toolbar
                    ).strip()
                else:
                    line = input("rtl-agent> ").strip()
            except (EOFError, KeyboardInterrupt):
                out("\nExiting.")
                return 0
            if not line:
                continue
            if line.startswith("/"):
                if self._handle_command(line):
                    return 0
                continue
            self._run_request(line)

    # -- commands -----------------------------------------------------------
    def _handle_command(self, line: str) -> bool:
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd in ("/quit", "/exit"):
            out("Flushing logs and exiting.")
            return True
        if cmd == "/help":
            self._help()
        elif cmd == "/doctor":
            self._preflight_gate()
        elif cmd == "/config":
            if args and args[0] in ("key", "iverilog", "vvp"):
                mapping = {"key": "1", "iverilog": "2", "vvp": "3"}
                self._reconfigure_field(mapping[args[0]])
                self._preflight()
            else:
                out(f"iverilog: {self.config.iverilog_path}")
                out(f"vvp:      {self.config.vvp_path}")
                out(f"api key:  {self.config.masked_key()}")
                out(f"testbench: {self._external_tb or '(self-verified; agent generates its own)'}")
        elif cmd == "/prompts":
            out(f"Prompt directory: {prompt_loader.PROMPT_DIRECTORY}")
            for name in prompt_loader.REQUIRED_PROMPTS:
                out(f"  - {name}")
        elif cmd == "/show-prompt":
            self._show_prompt(args)
        elif cmd == "/reload-prompts":
            try:
                self.prompts = prompt_loader.load_all_prompts()
                out("Master prompts reloaded for the next task.")
            except prompt_loader.PromptLoadError as exc:
                out(f"[FAIL] {exc}")
        elif cmd == "/summary":
            if args and args[0] in ("on", "off"):
                self._summaries_enabled = args[0] == "on"
            state = "on" if self._summaries_enabled else "off"
            out(f"Parallel summary agent is {state}.")
        elif cmd == "/testbench":
            self._handle_testbench(args)
        else:
            out(f"Unknown command: {cmd} (try /help)")
        return False

    def _help(self) -> None:
        out("Commands:")
        out("  /doctor           Rerun the startup preflight")
        out("  /config           Show paths and masked API-key status")
        out("  /config key       Replace the DeepSeek API key and re-check")
        out("  /config iverilog  Replace the compiler path and re-check")
        out("  /config vvp       Replace the runtime path and re-check")
        out("  /prompts          Show the prompt directory and filenames")
        out("  /show-prompt <n>  Show one prompt (e.g. system, reflection)")
        out("  /reload-prompts   Reload master prompts for the next task")
        out("  /summary on|off   Toggle the parallel per-step summary agent")
        out("  /testbench <path> Use a real testbench (DUT-only, no verification gen)")
        out("  /testbench off    Clear the real testbench (back to self-verified)")
        out("  /help             Show this help")
        out("  /quit             Exit")
        out("")
        out("While a task is running, press Esc to terminate it and restore your prompt.")

    def _handle_testbench(self, args: list[str]) -> None:
        if not args:
            if self._external_tb:
                out(f"Real testbench: {self._external_tb}")
                out("Next task will match this testbench; no verification is generated.")
            else:
                out("No real testbench set. The agent generates and self-checks its own.")
                out("Usage: /testbench <path>   (or /testbench off to clear)")
            return
        if args[0] == "off":
            self._external_tb = None
            out("Real testbench cleared. Back to self-verified mode.")
            return
        path = Path(" ".join(args)).expanduser()
        if not path.is_file():
            out(f"[FAIL] Not a file: {path}")
            return
        self._external_tb = str(path)
        out(f"Real testbench set: {path}")
        out("For the next task the agent will generate ONLY the DUT, run it against this")
        out("testbench, and skip verification-plan/testbench generation.")

    def _show_prompt(self, args: list[str]) -> None:
        if not args:
            out("Usage: /show-prompt <name>")
            return
        key = args[0]
        name = key if key.endswith(".md") else f"{key}_prompt.md"
        if name == "system_prompt.md" or key == "system":
            name = "system_prompt.md"
        content = (self.prompts or {}).get(name)
        if content is None:
            out(f"No such prompt: {name}")
            return
        out(content)

    # -- task execution -----------------------------------------------------
    def _run_request(self, request: str) -> None:
        client = DeepSeekClient(self.config.deepseek_api_key)
        watcher = CancelWatcher()
        status = StatusDisplay()

        # Pause Esc-watching and the live bar during clarification prompts so
        # they don't fight prompt_toolkit for the terminal or steal keystrokes.
        def ask(questions):
            watcher.pause()
            status.pause()
            try:
                return self._ask_user(questions)
            finally:
                status.resume()
                watcher.resume()

        def on_step(kind, summary):
            status.note(f"    {kind}: {summary}")

        # Parallel, independent summary agent (its own client, no shared history).
        def on_summary(label, text):
            body = "\n".join(
                textwrap.fill(line, width=88, initial_indent="      ",
                              subsequent_indent="      ")
                for line in text.splitlines() or [text]
            )
            status.note(f"    --- summary: {label} ---\n{body}")

        summarizer = SummaryAgent(
            self.config.deepseek_api_key, on_summary, enabled=self._summaries_enabled
        )

        controller = Controller(
            config=self.config,
            prompts=self.prompts,
            client=client,
            ask_user=ask,
            progress=status.set_stage,
            on_stream=status.set_preview,
            on_step=on_step,
            on_stage=summarizer.submit,
        )
        if watcher.available:
            out("[Agent] Working... press Esc to terminate and restore your prompt.")

        if self._external_tb:
            out(f"[Agent] Using real testbench: {self._external_tb} (no verification generated)")

        try:
            with watcher, status:
                result = controller.run_task(
                    request,
                    should_cancel=lambda: watcher.cancelled,
                    external_testbench=self._external_tb,
                )
            summarizer.drain()  # let in-flight summaries print before the result block
        except Exception as exc:  # noqa: BLE001
            out(f"[ERROR] Task failed: {exc}")
            return
        finally:
            summarizer.shutdown()

        if result.status == "ABORTED":
            out("\n[Agent] Terminated. Your prompt has been restored - edit it and press "
                "Enter to run again, or clear it to start over.")
            self._prefill = request
            out("")
            return

        out("")
        out(f"Final status: {result.status}")
        out(f"Run directory: {result.run_dir}")
        if result.dut_path:
            out(f"RTL: {result.dut_path}")
        if result.testbench_path:
            out(f"Testbench: {result.testbench_path}")
        if result.detail:
            out(f"Detail: {result.detail}")
        out("")

    def _ask_user(self, questions: list[dict]) -> str:
        out("\n[Agent] I need a few details to continue:\n")
        answers = []
        for q in questions:
            field = q.get("field")
            question = q.get("question", field)
            options = q.get("options") or []
            if options:
                ans = prompt_select.select_option(question, options)
            else:
                ans = prompt_select.ask_free_text(question)
            if ans:
                answers.append(f"{field}: {ans}")
                out(f"  [x] {field}: {ans}")
            else:
                out(f"  [ ] {field}: (skipped)")
        out("")
        return "; ".join(answers)


def main() -> int:
    return TerminalUI().start()
