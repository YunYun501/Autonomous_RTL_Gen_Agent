"""Prompt loader.

Loads agent instructions from the top-level ``prompts/`` directory. Rejects paths
outside the permitted directory and rejects missing or empty prompt files so the
agent never silently falls back to a hardcoded prompt.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIRECTORY = PROJECT_ROOT / "prompts"

REQUIRED_PROMPTS = (
    "system_prompt.md",
    "specification_prompt.md",
    "verification_prompt.md",
    "testbench_prompt.md",
    "reflection_prompt.md",
    "external_generation_prompt.md",
)


class PromptLoadError(RuntimeError):
    """Raised when a required prompt cannot be loaded safely."""


def load_prompt(filename: str) -> str:
    prompt_path = (PROMPT_DIRECTORY / filename).resolve()

    try:
        prompt_path.relative_to(PROMPT_DIRECTORY.resolve())
    except ValueError as exc:
        raise PromptLoadError(f"Invalid prompt path: {filename}") from exc

    if not prompt_path.is_file():
        raise PromptLoadError(f"Required prompt file was not found: {prompt_path}")

    try:
        content = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptLoadError(f"Could not read prompt file: {prompt_path}") from exc

    if not content:
        raise PromptLoadError(f"Prompt file is empty: {prompt_path}")

    return content


def load_all_prompts() -> dict[str, str]:
    """Load every required prompt. Raises PromptLoadError on any problem."""
    return {name: load_prompt(name) for name in REQUIRED_PROMPTS}


def hash_prompt(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_all(prompts: dict[str, str]) -> dict[str, str]:
    return {name: hash_prompt(content) for name, content in prompts.items()}
