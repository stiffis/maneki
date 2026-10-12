from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

IDLE = "idle"
WORKING = "working"
ASKING = "asking"
DONE = "done"
INTERRUPTED = "interrupted"
STATES = (IDLE, WORKING, ASKING, DONE, INTERRUPTED)
EMOTIONS = (DONE, INTERRUPTED)
LIFT = 2
CYCLE = 12


@dataclass(frozen=True)
class Pose:
    parts: tuple
    dy: int = 0
    drop: int = 0
    bubble: str = ""


@dataclass(frozen=True)
class Sprite:
    name: str
    left: int
    width: int
    palette: dict
    base: tuple
    frame: Callable

    @property
    def height(self):
        return len(self.base)


def patch(row, col, rows):
    return tuple(
        (row + i, col + k, char) for i, line in enumerate(rows) for k, char in enumerate(line) if char != " "
    )


def column(rows, cells):
    return tuple(
        (row, col, char(i) if callable(char) else char) for i, row in enumerate(rows) for col, char in cells
    )


def stripe(i):
    return "s" if i % 2 else "b"


def stripe_back(i):
    return "b" if i % 2 else "s"


def mirrored(rows):
    return [line[::-1] for line in rows]


CAT_BASE = (
    "...o..........o...",
    "..oio........oio..",
    "..oiio......oiio..",
    "..oiiLooooooLiio..",
    ".obLLLsLssLsLLLbo.",
    "obbbbbbbssbbbbbbSo",
    "obbbbbbbbbbbbbbbSo",
    "obbbbbbbbbbbbbbbSo",
    "osbwwwwwnnwwwwwbso",
    ".obwwwwwwwwwwwwbo.",
    "..oowwwwwwwwwwoo..",
    "....obwwwwwwbo",
    "...obbwwwwwwbbo",
    "...obbwwwwwwbbboo",
    "...obbwwwwwbbsbbSo",
    "..obbbwwwwbbsbbbSSo",
    "..obbbwwwwbsbbsbbSo",
    "..obwwowwobbbbsbbSo",
    "..owwwowwwobbbbbbSo",
    "...ooo.ooo.oooooooo",
)


def cat_eyes(row, rows):
    return patch(row, 3, rows) + patch(row, 12, rows)


CAT_EYES = {
    "open": cat_eyes(5, ["ooo", "hPg", "gPg"]),
    "side": cat_eyes(5, ["ooo", "hgP", "ggP"]),
    "wide": cat_eyes(4, ["ooo", "hgg", "gPg", "ggg"]),
    "closed": cat_eyes(5, ["   ", "o o", " o "]),
    "happy": cat_eyes(5, ["   ", " o ", "o o"]),
    "heavy": cat_eyes(5, ["ooo", "ooo", "gPg"]),
}
CAT_MOUTHS = {
    "smile": patch(9, 6, ["m mm m", " m  m "]),
    "open": patch(9, 6, [" mmmm ", " mnnm "]),
    "flat": patch(9, 6, ["      ", "  mm  "]),
}
TAIL_JOIN = ((18, 19, "S"), (18, 20, "o"), (18, 21, "o"), (19, 19, "o"))
CAT_TAILS = {
    "low": patch(16, 18, ["   o", "oooS", "Ssbo", "oo  "]),
    "flick": patch(15, 18, ["   o", "  oS", "ooob", "Ssbo", "oo  "]),
    "up": column(range(10, 18), ((20, "o"), (21, stripe), (22, "o")))
    + ((9, 21, "o"), (9, 22, "o"), (10, 23, "o"))
    + TAIL_JOIN,
    "sway": column(range(14, 18), ((20, "o"), (21, stripe), (22, "o")))
    + column(range(10, 14), ((21, "o"), (22, stripe), (23, "o")))
    + ((9, 22, "o"), (9, 23, "o"), (10, 24, "o"))
    + TAIL_JOIN,
    "puff": column(range(9, 18), ((20, "o"), (21, stripe), (22, stripe_back), (23, "o")))
    + ((8, 21, "o"), (8, 22, "o"), (10, 24, "o"), (12, 24, "o"), (14, 24, "o"), (16, 24, "o"))
    + TAIL_JOIN
    + ((18, 22, "o"),),
}
CAT_EXTRAS = {
    "whiskers": ((8, -2, "k"), (8, -1, "k"), (9, -1, "k"), (10, -2, "k"), (8, 18, "k"), (8, 19, "k"), (9, 18, "k"), (10, 19, "k")),
    "twitch": ((0, 3, "."), (1, 2, "."), (1, 3, "o"), (2, 2, "o")),
    "ears_flat": tuple((row, col, ".") for row, cols in ((0, (3, 14)), (1, (2, 3, 4, 13, 14, 15)), (2, (2, 3, 4, 5, 12, 13, 14, 15))) for col in cols)
    + patch(2, -1, ["ooo"])
    + patch(3, -1, ["oii"])
    + patch(2, 16, ["ooo"])
    + patch(3, 16, ["iio"]),
    "paw_left": patch(18, 3, ["ooo"]) + ((19, 3, "."), (19, 4, "."), (19, 5, ".")),
    "paw_right": patch(18, 7, ["ooo"]) + ((19, 7, "."), (19, 8, "."), (19, 9, ".")),
    "blush": ((8, 2, "q"), (8, 15, "q")),
}


def cat_frame(state, frame, age, rest, blink):
    eyes = CAT_EYES["closed"] if blink else None
    whiskers = CAT_EXTRAS["whiskers"]
    if state == IDLE:
        tail = CAT_TAILS["flick"] if frame % 6 == 5 else CAT_TAILS["low"]
        ear = CAT_EXTRAS["twitch"] if frame % 10 == 7 else ()
        return Pose((whiskers, CAT_EYES["closed"], CAT_MOUTHS["smile"], tail, ear), bubble=("z", "zZ", "zZz")[frame % 3])
    if state == WORKING:
        paw = () if rest else CAT_EXTRAS["paw_left"] if frame % 2 else CAT_EXTRAS["paw_right"]
        tail = CAT_TAILS["up"] if frame % 4 < 2 else CAT_TAILS["sway"]
        return Pose((whiskers, eyes or CAT_EYES["side"], CAT_MOUTHS["smile"], paw, tail))
    if state == ASKING:
        return Pose((whiskers, eyes or CAT_EYES["wide"], CAT_MOUTHS["open"], CAT_TAILS["puff"]), dy=frame % 2, bubble="!")
    if state == DONE:
        parts = (whiskers, CAT_EYES["happy"], CAT_MOUTHS["open"], CAT_EXTRAS["blush"], CAT_TAILS["up"])
        return Pose(parts, dy=(0, 2, 3, 2)[frame % 4], bubble="" if rest else "✦")
    parts = (whiskers, eyes or CAT_EYES["heavy"], CAT_EXTRAS["ears_flat"], CAT_MOUTHS["flat"], CAT_TAILS["low"])
    return Pose(parts, bubble="…")


OSAKA_BASE = (
    ".......oooooo.......",
    ".....ooHHHHHHoo.....",
    "....oHHHHLLHHHHo....",
    "...oHHHHHLLHHHHHo...",
    "..oHHHHHHDDHHHHHHo..",
    "..oHHHfHHffHHfHHHo..",
    "..oHHffHffffHffHHo..",
    ".oHHfffHffffHfffHHo.",
    ".oHHffffffffffffHHo.",
    ".oHHffffffffffffHHo.",
    ".oHHffffffffffffHHo.",
    ".oHHffffffffffffHHo.",
    ".oHHfqffffFfffqfHHo.",
    ".oHHHffffffffffHHHo.",
    ".oHHHoffffffffoHHHo.",
    ".oHHHHooffffooHHHHo.",
    ".oHHHcCCFFFFCCcHHHo.",
    "oHHHccccCFFCccccHHHo",
    "oHHzcccccttccccczHHo",
    "oHjjzzzzcttczzzzjjHo",
    "oHjjjjjjjttjjjjjjjHo",
)


def osaka_eyes(rows):
    return patch(8, 5, rows) + patch(8, 11, rows)


OSAKA_EYES = {
    "open": osaka_eyes(["oooo", "WIIW", "WPPW", "WhIW"]),
    "up": osaka_eyes(["oooo", "WPPW", "WIIW", "WWWW"]),
    "wide": osaka_eyes(["oooo", "WWWW", "WPPW", "WWWW"]),
    "sleepy": osaka_eyes(["    ", "oooo", "WIIW", "WhIW"]),
    "closed": osaka_eyes(["    ", "    ", "o  o", " oo "]),
    "happy": osaka_eyes(["    ", " oo ", "o  o", "    "]),
}
OSAKA_MOUTHS = {
    "flat": patch(13, 8, ["    ", " mm "]),
    "small": patch(13, 8, [" mm ", " mm "]),
    "open": patch(13, 8, ["mmmm", "mnnm"]),
}
ARM = ["ff  ", "ff  ", "jj  ", "jj  ", " jj ", " jj ", "  jj", "  j "]
OSAKA_ARMS = patch(10, -2, ARM) + patch(10, 18, mirrored(ARM))
FLOATERS = ("°  ·", " ° ·", "·  °", "· ° ")
SLOW_SECONDS = 1.3
STARTLED_SECONDS = 1.7


def osaka_frame(state, frame, age, rest, blink):
    closed = OSAKA_EYES["closed"]
    if state == IDLE:
        nodding = frame % 4 >= 2
        return Pose((closed, OSAKA_MOUTHS["small"]), drop=15 if nodding else 0, bubble="zz…" if nodding else "z")
    if state == WORKING:
        eyes = closed if blink else OSAKA_EYES["open"] if rest or frame % 4 < 2 else OSAKA_EYES["up"]
        return Pose((eyes, OSAKA_MOUTHS["flat"]), bubble="" if rest else FLOATERS[frame % 4])
    if state == ASKING:
        if age < SLOW_SECONDS:
            return Pose((OSAKA_EYES["sleepy"], OSAKA_MOUTHS["small"]), bubble="…")
        eyes = closed if blink else OSAKA_EYES["wide"]
        return Pose((eyes, OSAKA_MOUTHS["open"]), dy=3 if age < STARTLED_SECONDS else frame % 2, bubble="huh?!")
    if state == DONE:
        parts = (OSAKA_EYES["happy"], OSAKA_MOUTHS["open"], OSAKA_ARMS)
        return Pose(parts, dy=0 if rest else (0, 2, 3, 2)[frame % 4], bubble="sata andagi!")
    eyes = closed if blink else OSAKA_EYES["sleepy"]
    return Pose((eyes, OSAKA_MOUTHS["flat"]), bubble="where'd it go?")


SPRITES = {
    "cat": Sprite(
        "cat",
        left=2,
        width=27,
        palette={
            "o": "#3b1f12", "L": "#f4b664", "b": "#e48c35", "S": "#b7621f", "s": "#a24f1a", "w": "#fcebd2",
            "i": "#ec9c9c", "g": "#a6c93a", "P": "#1c120b", "h": "#ffffff", "n": "#e57d86", "m": "#3b1f12",
            "k": "#f6ead6", "q": "#f19a8a",
        },
        base=CAT_BASE,
        frame=cat_frame,
    ),
    "osaka": Sprite(
        "osaka",
        left=2,
        width=24,
        palette={
            "o": "#2a2321", "H": "#463d37", "D": "#2b2522", "L": "#645850", "f": "#f3e3d6", "F": "#d9bfae",
            "W": "#ffffff", "I": "#8b7266", "P": "#3d2e28", "h": "#ffffff", "m": "#7d4540", "n": "#c46a6a",
            "q": "#f2b0aa", "j": "#d9868f", "c": "#f5f2f4", "C": "#cfc8d6", "z": "#c8505e", "t": "#6b4a2e",
        },
        base=OSAKA_BASE,
        frame=osaka_frame,
    ),
}


def rgb(color):
    return tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))


@lru_cache(maxsize=64)
def pixels(name, parts, drop):
    sprite = SPRITES[name]
    grid = [list(("." * sprite.left + line).ljust(sprite.width, ".")) for line in sprite.base]
    for part in parts:
        for row, col, char in part:
            col += sprite.left
            if 0 <= row < len(grid) and 0 <= col < sprite.width:
                grid[row][col] = char
    if drop:
        grid[1 : drop + 1] = grid[:drop]
        grid[0] = ["."] * sprite.width
    cells = {}
    for y, line in enumerate(grid):
        for x, char in enumerate(line):
            if char in sprite.palette:
                cells.setdefault(char, []).append((x, y))
    return tuple((rgb(sprite.palette[char]), tuple(found)) for char, found in cells.items())


def pose(name, state, frame, age, rest, blink):
    return SPRITES[name].frame(state, frame % CYCLE, age, rest, blink)


def mood(statuses, asking):
    statuses = set(statuses)
    if asking or statuses & {"asking", "attention"}:
        return ASKING
    if "working" in statuses:
        return WORKING
    if "done" in statuses:
        return DONE
    return IDLE


def emotion(before, after):
    found = None
    for session_id, (status, activity) in after.items():
        old = before.get(session_id)
        if old is None:
            continue
        if status == "done" and old[0] != "done":
            found = DONE
        elif activity == "Interrupted" and old[1] != "Interrupted" and found is None:
            found = INTERRUPTED
    return found


def bubbles(name):
    frame = SPRITES[name].frame
    found = {
        frame(state, step, age, rest, blink).bubble
        for state in STATES
        for step in range(CYCLE)
        for age in (0.0, SLOW_SECONDS, STARTLED_SECONDS, 9.0)
        for rest in (False, True)
        for blink in (False, True)
    }
    return sorted(found - {""})
