import os
import re
import sys
import tempfile
import tomllib

from .mascots import SPRITES
from .paths import config_dir

DEFAULTS = {"mascot": "cat", "mascot_scale": 2}
MASCOTS = (*SPRITES, "none")
SCALES = range(1, 5)
MASCOT_LINE = re.compile(r"^mascot\s*=.*$", re.M)


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
        print(f"maneki: ignoring {path}: {error}", file=sys.stderr)
        return dict(DEFAULTS)
    for problem in problems:
        print(f"maneki: {path}: {problem}", file=sys.stderr)
    return settings


def next_mascot(current):
    names = tuple(SPRITES)
    return names[(names.index(current) + 1) % len(names)] if current in names else names[0]


def with_mascot(text, name):
    line = f'mascot = "{name}"'
    if MASCOT_LINE.search(text):
        return MASCOT_LINE.sub(line, text, count=1)
    return f"{line}\n{text}"


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def rotate_mascot():
    path = config_file()
    try:
        text = path.read_text()
    except FileNotFoundError:
        text = ""
    name = next_mascot(load()["mascot"])
    write_atomic(path, with_mascot(text, name))
    return name
