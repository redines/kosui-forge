from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys


def _home_path(home: Path | None) -> Path:
    return Path.home() if home is None else home


def default_config_path(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-appropriate non-secret user configuration path."""
    platform_name = sys.platform if platform_name is None else platform_name
    environment = {} if environ is None else environ
    home_path = _home_path(home)
    if platform_name == "win32":
        root = Path(environment.get("APPDATA", home_path / "AppData/Roaming"))
    elif platform_name == "darwin":
        root = home_path / "Library/Application Support"
    else:
        root = Path(environment.get("XDG_CONFIG_HOME", home_path / ".config"))
    return root / "repo-bootstrap/config.toml"


def default_state_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-appropriate directory for non-secret run journals."""
    platform_name = sys.platform if platform_name is None else platform_name
    environment = {} if environ is None else environ
    home_path = _home_path(home)
    if platform_name == "win32":
        root = Path(environment.get("LOCALAPPDATA", home_path / "AppData/Local"))
        return root / "repo-bootstrap/state"
    if platform_name == "darwin":
        return home_path / "Library/Application Support/repo-bootstrap/state"
    root = Path(environment.get("XDG_STATE_HOME", home_path / ".local/state"))
    return root / "repo-bootstrap"
