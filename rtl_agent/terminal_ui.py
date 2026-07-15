"""Terminal-native interface.

A persistent REPL: natural-language entries start a new RTL task; slash commands
handle configuration, prompt inspection, and diagnostics. Uses rich when available
and degrades to plain printing otherwise.
"""

from __future__ import annotations

import getpass
from pathlib import Path

from . import config as config_mod
from . import prompt_loader
from . import prompt_select
from .config import Config
from .controller import Controller
from .deepseek_client import DeepSeekClient
from .interrupt import CancelWatcher
from .startup import run_preflight, PreflightReport

try:  # optional pretty output
    from rich.console import Console

    _console = Console()

    def out(msg: str = "") -> None:
        _console.print(msg)
except Exception:  # noqa: BLE001
    def out(msg: str = "") -> None:
        print(msg)

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
        out(f"{tag} {r.name}" + (f" — {r.detail}" if r.detail else ""))


class TerminalUI:
    def __init__(self):
        self.config: Config | None = None
        self.prompts: dict[str, str] | None = None
        self._prefill: str = ""  # restores the prompt text after an Esc abort

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> int:
        out(BANNER)
        if not config_mod.config_exists():
            out("No configuration found — starting first-time setup.\n")
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
        out("\nRTL Agent ready. Type a request, or /help for commands.\n")
        return self._repl()

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
        session = PromptSession(history=InMemoryHistory()) if _HAS_PTK else None
        while True:
            prefill = self._prefill
            self._prefill = ""
            try:
                if session is not None:
                    line = session.prompt("rtl-agent> ", default=prefill).strip()
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
        out("  /help             Show this help")
        out("  /quit             Exit")
        out("")
        out("While a task is running, press Esc to terminate it and restore your prompt.")

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

        # Pause Esc-watching during clarification prompts so it doesn't steal keys.
        def ask(questions):
            watcher.pause()
            try:
                return self._ask_user(questions)
            finally:
                watcher.resume()

        controller = Controller(
            config=self.config,
            prompts=self.prompts,
            client=client,
            ask_user=ask,
            progress=lambda msg: out(f"[Agent] {msg}"),
        )
        if watcher.available:
            out("[Agent] Working... press Esc to terminate and restore your prompt.")

        try:
            with watcher:
                result = controller.run_task(request, should_cancel=lambda: watcher.cancelled)
        except Exception as exc:  # noqa: BLE001
            out(f"[ERROR] Task failed: {exc}")
            return

        if result.status == "ABORTED":
            out("\n[Agent] Terminated. Your prompt has been restored — edit it and press "
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
                out(f"  ✓ {field}: {ans}")
            else:
                out(f"  — {field}: (skipped)")
        out("")
        return "; ".join(answers)


def main() -> int:
    return TerminalUI().start()
