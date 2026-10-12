import sys
import tomllib

from .mascots import SPRITES
from .paths import config_dir

DEFAULTS = {"mascot": "cat", "mascot_scale": 1}
MASCOTS = (*SPRITES, "none")
SCALES = range(1, 5)


def config_file():
    return config_dir() / "config.toml"


def parse(text):
    settings = dict(DEFAULTS)
    problems = []
    data = tomllib.loads(text)
    mascot = data.get("mascot", DEFAULTS["mascot"])
    if mascot in MASCOTS:
        settings["mascot"] = mascot
    else:
        problems.append(f"mascot must be one of {', '.join(MASCOTS)}")
    scale = data.get("mascot_scale", DEFAULTS["mascot_scale"])
    if isinstance(scale, int) and not isinstance(scale, bool) and scale in SCALES:
        settings["mascot_scale"] = scale
    else:
        problems.append(f"mascot_scale must be a whole number from {SCALES.start} to {SCALES.stop - 1}")
    return settings, problems


def load():
    path = config_file()
    try:
        settings, problems = parse(path.read_text())
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"perch: ignoring {path}: {error}", file=sys.stderr)
        return dict(DEFAULTS)
    for problem in problems:
        print(f"perch: {path}: {problem}", file=sys.stderr)
    return settings
