"""Run workspace and logging.

Creates one isolated run directory per natural-language request, snapshots the
frozen prompts and their hashes, records every DeepSeek API call in JSONL, versions
generated source files, and maintains a human-readable ``run_log.txt``. The API key
must never reach these artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT

RUNS_DIR = PROJECT_ROOT / "runs"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(text: str, max_len: int = 32) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return (slug or "task")[:max_len]


class RunContext:
    """Owns the on-disk artifacts for a single task run."""

    def __init__(self, request: str, module_hint: str = "task"):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        name = f"{timestamp}_{_slug(module_hint)}"
        self.dir = RUNS_DIR / name
        self.dir.mkdir(parents=True, exist_ok=True)

        (self.dir / "prompts").mkdir(exist_ok=True)
        (self.dir / "rtl_versions").mkdir(exist_ok=True)
        (self.dir / "testbench_versions").mkdir(exist_ok=True)
        (self.dir / "simulation_logs").mkdir(exist_ok=True)

        self.request = request
        self.api_log_path = self.dir / "api_calls.jsonl"
        self.run_log_path = self.dir / "run_log.txt"
        self._rtl_attempts = 0
        self._tb_attempts = 0
        self._sim_attempts = 0

        self.log(f"Run directory: {self.dir}")
        self.log(f"Original request: {request}")

    # -- prompt snapshotting ------------------------------------------------
    def snapshot_prompts(self, prompts: dict[str, str]) -> dict[str, str]:
        hashes = {}
        dest_dir = self.dir / "prompts"
        for name, content in prompts.items():
            (dest_dir / name).write_text(content, encoding="utf-8")
            hashes[name] = sha256_text(content)
        (self.dir / "prompt_hashes.json").write_text(
            json.dumps(hashes, indent=2), encoding="utf-8"
        )
        self.log(f"Snapshotted and froze {len(prompts)} prompt(s).")
        return hashes

    # -- source versioning --------------------------------------------------
    def save_rtl_version(self, code: str) -> Path:
        self._rtl_attempts += 1
        path = self.dir / "rtl_versions" / f"attempt_{self._rtl_attempts:02d}.v"
        path.write_text(code, encoding="utf-8")
        return path

    def save_testbench_version(self, code: str) -> Path:
        self._tb_attempts += 1
        path = self.dir / "testbench_versions" / f"attempt_{self._tb_attempts:02d}.v"
        path.write_text(code, encoding="utf-8")
        return path

    def save_simulation_log(self, result: dict) -> Path:
        self._sim_attempts += 1
        path = self.dir / "simulation_logs" / f"attempt_{self._sim_attempts:02d}.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return path

    # -- generic artifact + json -------------------------------------------
    def write_json(self, filename: str, data: dict) -> Path:
        path = self.dir / filename
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    # -- logging ------------------------------------------------------------
    def log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.run_log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")

    def log_api_call(self, record: dict) -> None:
        record = {"timestamp": datetime.now().isoformat(), **record}
        with self.api_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
