"""Configuration subsystem.

Handles the first-run wizard, loading and saving the local configuration, and
masked display of the API key. The configuration file lives in a gitignored
directory and holds the DeepSeek API key plus the two simulator executable paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / ".rtl-agent"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_IVERILOG = r"C:\msys64\ucrt64\bin\iverilog.exe"
DEFAULT_VVP = r"C:\msys64\ucrt64\bin\vvp.exe"


@dataclass
class Config:
    deepseek_api_key: str
    iverilog_path: str
    vvp_path: str

    def masked_key(self) -> str:
        return mask_secret(self.deepseek_api_key)


def mask_secret(secret: str | None) -> str:
    """Return a display-safe representation of a secret."""
    if not secret:
        return "<not set>"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


def config_exists() -> bool:
    return CONFIG_PATH.is_file()


def load_config() -> Config | None:
    if not config_exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return Config(
            deepseek_api_key=data["deepseek_api_key"],
            iverilog_path=data["iverilog_path"],
            vvp_path=data["vvp_path"],
        )
    except KeyError:
        return None


def save_config(config: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), indent=2),
        encoding="utf-8",
    )


def run_first_time_setup(input_fn=input, getpass_fn=None) -> Config:
    """Interactive first-run configuration wizard.

    Asks for exactly three values: the DeepSeek API key and the two simulator
    executable paths. Pressing Enter accepts the suggested executable path.
    """
    import getpass as _getpass

    getpass_fn = getpass_fn or _getpass.getpass

    print("RTL Agent First-Time Setup\n")

    api_key = ""
    while not api_key.strip():
        api_key = getpass_fn("DeepSeek API key: ").strip()
        if not api_key:
            print("An API key is required.")

    iverilog = input_fn(
        f"Icarus Verilog path\n[{DEFAULT_IVERILOG}]: "
    ).strip() or DEFAULT_IVERILOG

    vvp = input_fn(
        f"VVP runtime path\n[{DEFAULT_VVP}]: "
    ).strip() or DEFAULT_VVP

    config = Config(
        deepseek_api_key=api_key,
        iverilog_path=iverilog,
        vvp_path=vvp,
    )
    save_config(config)
    return config
