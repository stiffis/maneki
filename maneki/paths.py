import os
from pathlib import Path

from . import proto

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "maneki"
THEMES = ROOT / "themes"
ICONS = ROOT / "icons"
INTEGRATIONS = ROOT / "integrations"


def runtime_dir() -> Path:
    return Path(proto.runtime_dir())


def socket_path() -> Path:
    return Path(proto.socket_file())


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "maneki"
