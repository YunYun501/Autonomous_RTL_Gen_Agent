"""Parallel, independent stage-summary agent.

Summarizes each STAGE of the main agent's work (specification, verification
planning, RTL/testbench generation, and each reflection cycle), running in
background threads so it never blocks the main workflow. It is deliberately
isolated: it uses its own DeepSeek client and each summary is a fresh, stateless
request containing only the one stage being described. It never shares the main
agent's conversation history.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable

from .deepseek_client import DeepSeekClient, DeepSeekError

SUMMARY_SYSTEM_PROMPT = (
    "You are a technical writer summarizing ONE stage of an autonomous RTL "
    "(Verilog) design-and-verification agent's work. You are given that stage's "
    "private reasoning together with the tool actions and results it produced.\n\n"
    "Write ONE to TWO short paragraphs (roughly 90-170 words total) in plain "
    "technical English covering: (1) what the agent was trying to accomplish in "
    "this stage; (2) the concrete design or verification decisions it made -- e.g. "
    "module interface and ports, reset scheme (polarity/synchrony), clocking and "
    "timing, FSM states and durations, counter widths, specific VP-* checks, and "
    "any failures diagnosed and how they were fixed; and (3) the outcome of the "
    "stage. Be specific and grounded in the provided material; never invent "
    "details. Do not use markdown, headings, bullet lists, or any preamble -- "
    "return only the summary prose."
)

OnSummary = Callable[[str, str], None]


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

    def submit(self, label: str, text: str) -> None:
        if not self._enabled or self._executor is None:
            return
        fut = self._executor.submit(self._run, label, text)
        with self._lock:
            self._futures.append(fut)

    def _run(self, label: str, text: str) -> None:
        try:
            summary = self._client.simple_completion(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:16000]},
                ],
                timeout=60.0,
                max_tokens=900,
            )
            summary = summary.strip() or "(no summary produced)"
        except DeepSeekError as exc:
            summary = f"(summary unavailable: {exc})"
        except Exception as exc:  # noqa: BLE001
            summary = f"(summary error: {exc})"
        try:
            self._on_summary(label, summary)
        except Exception:  # noqa: BLE001 - never let a UI callback kill the worker
            pass

    def drain(self, timeout: float = 30.0) -> None:
        """Wait for in-flight stage summaries so they print before we move on."""
        if not self._enabled:
            return
        with self._lock:
            pending = [f for f in self._futures if not f.done()]
        if pending:
            wait(pending, timeout=timeout)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)
