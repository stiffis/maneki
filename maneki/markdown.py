import html
import re

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBERED = re.compile(r"^(\s*)(\d+[.)])\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
CODE = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
ITALIC = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])|(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)")
STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
TAG = re.compile(r"<[^>]+>")
INDENT = "  "
CODE_OPEN = '<span font_family="monospace" background="#808080" bgalpha="25%">'
CODE_CLOSE = "</span>"


def escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def styled(text):
    text = LINK.sub(r"<u>\1</u>", text)
    text = BOLD.sub(lambda match: f"<b>{match.group(1) or match.group(2)}</b>", text)
    text = ITALIC.sub(lambda match: f"<i>{match.group(1) or match.group(2)}</i>", text)
    return STRIKE.sub(r"<s>\1</s>", text)


def inline(text):
    pieces = CODE.split(escape(text))
    return "".join(f"{CODE_OPEN}{piece}{CODE_CLOSE}" if index % 2 else styled(piece) for index, piece in enumerate(pieces))


def line_markup(line):
    if RULE.match(line) or TABLE_RULE.match(line):
        return "──────"
    if match := HEADING.match(line):
        return f"<b>{inline(match.group(1))}</b>"
    if match := BULLET.match(line):
        depth = len(match.group(1).expandtabs(4)) // 2
        return f"{INDENT * depth}• {inline(match.group(2))}"
    if match := NUMBERED.match(line):
        depth = len(match.group(1).expandtabs(4)) // 2
        return f"{INDENT * depth}{match.group(2)} {inline(match.group(3))}"
    if match := QUOTE.match(line):
        return f"▎ <i>{inline(match.group(1))}</i>"
    return inline(line)


def to_markup(text):
    lines = []
    in_code = False
    for line in (text or "").splitlines():
        if FENCE.match(line):
            in_code = not in_code
            continue
        lines.append(f"{CODE_OPEN}{escape(line)}{CODE_CLOSE}" if in_code else line_markup(line))
    return "\n".join(lines).strip("\n")


def to_plain(text):
    return html.unescape(TAG.sub("", to_markup(text)))
