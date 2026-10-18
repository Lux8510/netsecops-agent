"""Project-local DeepSeek configuration; never print or persist the API key."""

from __future__ import annotations

import os
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
SETTINGS_PATH = PROJECT_ROOT / "agent-settings.json"
KEY_NAME = "DEEPSEEK_API_KEY"
VALID_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class AgentConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AgentSettings:
    model: str
    base_url: str
    max_rounds: int
    max_tool_calls: int
    max_tokens: int
    request_timeout_seconds: int
    command_timeout_seconds: int
    total_timeout_seconds: int
    max_retries: int


def load_settings(path: Path = SETTINGS_PATH) -> AgentSettings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentConfigError(f"Cannot read project settings: {type(exc).__name__}") from None
    if not isinstance(raw, dict) or set(raw) != set(AgentSettings.__dataclass_fields__):
        raise AgentConfigError("Project settings have missing or unsupported fields")
    if not isinstance(raw["model"], str) or not raw["model"].strip():
        raise AgentConfigError("Model name must be a nonempty string")
    if raw["base_url"] != "https://api.deepseek.com":
        raise AgentConfigError("Only the official DeepSeek base_url is permitted")
    bounds = {
        "max_rounds": (1, 30), "max_tool_calls": (1, 50),
        "max_tokens": (100, 4096), "request_timeout_seconds": (3, 120),
        "command_timeout_seconds": (2, 60), "total_timeout_seconds": (10, 1800),
        "max_retries": (0, 5),
    }
    for name, (lower, upper) in bounds.items():
        value = raw[name]
        if type(value) is not int or not lower <= value <= upper:
            raise AgentConfigError(f"Invalid {name}; expected integer between {lower} and {upper}")
    return AgentSettings(**raw)


def read_local_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Read simple KEY=VALUE lines without shell evaluation or interpolation."""
    if not path.exists():
        return {}
    if not path.is_file():
        raise AgentConfigError("Project .env path is not a file")
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AgentConfigError(f"Invalid .env syntax on line {line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not VALID_NAME.fullmatch(name):
            raise AgentConfigError(f"Invalid .env variable name on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
            value = value[1:-1]
        values[name] = value
    return values


def get_deepseek_api_key(*, path: Path = ENV_PATH,
                         environ: Mapping[str, str] | None = None,
                         required: bool = False) -> str | None:
    """Environment wins; otherwise use the project .env. Empty placeholders are unset."""
    supplied = os.environ if environ is None else environ
    from_environment = supplied.get(KEY_NAME, "").strip()
    key = from_environment or read_local_env(path).get(KEY_NAME, "").strip()
    if not key and required:
        raise AgentConfigError(f"{KEY_NAME} is not configured in this process or the project .env")
    return key or None


def configuration_status(*, path: Path = ENV_PATH,
                         environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Safe metadata for CLI checks; key material is deliberately omitted."""
    supplied = os.environ if environ is None else environ
    env_set = bool(supplied.get(KEY_NAME, "").strip())
    local_set = bool(read_local_env(path).get(KEY_NAME, "").strip())
    return {
        "runtime": "Windows" if sys.platform == "win32" else
                   "Ubuntu/WSL2" if sys.platform == "linux" and "microsoft-standard-WSL2" in os.uname().release
                   else sys.platform,
        "env_file": str(path),
        "configured": env_set or local_set,
        "source": "process_environment" if env_set else "project_env" if local_set else "none",
    }
