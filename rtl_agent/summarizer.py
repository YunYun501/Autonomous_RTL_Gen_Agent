"""Parallel, independent summary agent.

Summarizes each step the main agent takes, running in background threads so it
never blocks the main workflow. It is deliberately isolated: it uses its own
DeepSeek client and each summary is a fresh, stateless request containing only the
one step being described. It never shares the main agent's conversation history.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable

from .deepseek_client import DeepSeekClient, DeepSeekError

SUMMARY_SYSTEM_PROMPT = (
    "You summarize a single step taken by an autonomous RTL (Verilog) design "
    "agent. You are given only that one step: its private reasoning and the action "
    "it took. Reply with ONE concise, information-dense sentence (max 30 words) "
    "stating what the step was actually about and what it concluded or did. Focus "
    "on concrete design/verification decisions (ports, reset, timing, FSM states, "
    "checks, failures). No preamble, no markdown, no quotes."
)

OnSummary = Callable[[int, str, str], None]


class SummaryAgent:
    def __init__(
        self,
        api_key: str,
        on_summary: OnSummary,
        max_workers: int = 2,
        enabled: bool = True,
    ):
        self._on_summary = on_summary
        self._enabled = enabled
        self._executor: ThreadPoolExecutor | None = (
            ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="summary")
            if enabled
            else None
        )
        # Independent client -> no shared state with the main agent.
        self._client = DeepSeekClient(api_key) if enabled else None
        self._futures: list[Future] = []
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def submit(self, seq: int, stage: str, text: str) -> None:
        if not self._enabled or self._executor is None:
            return
        fut = self._executor.submit(self._run, seq, stage, text)
        with self._lock:
            self._futures.append(fut)

    def _run(self, seq: int, stage: str, text: str) -> None:
        try:
            raw = self._client.simple_completion(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:6000]},
                ],
                timeout=45.0,
                max_tokens=120,
            )
            summary = " ".join((raw or "").split()) or "(no summary returned)"
        except DeepSeekError as exc:
            summary = f"(summary unavailable: {exc})"
        except Exception as exc:  # noqa: BLE001
            summary = f"(summary error: {exc})"
        try:
            self._on_summary(seq, stage, summary)
        except Exception:  # noqa: BLE001 - never let a UI callback kill the worker
            pass

    def drain(self, timeout: float = 8.0) -> None:
        """Wait briefly for in-flight summaries so they print before we move on."""
        if not self._enabled:
            return
        with self._lock:
            pending = [f for f in self._futures if not f.done()]
        if pending:
            wait(pending, timeout=timeout)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)
