"""Small helpers shared by the rest of the package."""
from __future__ import annotations

import calendar
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

# Every handoff starts with this line, so a later switch can recognise it and not nest it again.
MARKER = "[sw-handoff v1]"
# Openings used by earlier prototypes, still recognised so old sessions stay clean.
_LEGACY_OPENINGS = (
    "You are taking over a coding task that was in progress in",
    "You are resuming your own earlier session in this folder",
)

_WRAPPER_RE = re.compile(r"<(system-reminder|local-command-stdout|local-command-caveat)>.*?</\1>", re.S)
_WRAPPER_START = ("<command-name>", "<command-message>", "<local-command", "[Request interrupted")
_ISO_RE = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?\s*(Z|[+-]\d\d:?\d\d)?$"
)


class SwError(Exception):
    """A problem the user should read about, without a traceback."""


@dataclass
class Session:
    id: str
    path: Path
    mtime: float


@dataclass
class Parsed:
    turns: List[Dict] = field(default_factory=list)  # {"role","text","actions","ts"}
    files: List[str] = field(default_factory=list)
    summary: Optional[str] = None
    carried_goal: Optional[str] = None  # goal taken from an earlier handoff inside this session
    last_ts: Optional[float] = None  # time of the last real message, handoffs included


def clip(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[: n - 20].rstrip() + " ...[truncated]"


def short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def is_handoff(text: str) -> bool:
    s = text.lstrip()
    return s.startswith(MARKER) or s.startswith(_LEGACY_OPENINGS)


def extract_goal(handoff: str) -> str:
    m = re.search(r"## Original goal[^\n]*\n(.*?)(?=\n## |\Z)", handoff, re.S)
    return m.group(1).strip() if m else ""


def clean_user_text(text: str) -> str:
    text = _WRAPPER_RE.sub("", text).strip()
    if not text or text.startswith(_WRAPPER_START):
        return ""
    return text


def iso_ts(value) -> Optional[float]:
    """ISO-8601 string to epoch seconds. Works the same on every Python version."""
    m = _ISO_RE.match(str(value or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    try:
        secs = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s)))
    except (ValueError, OverflowError):
        return None
    if frac:
        secs += float("0." + frac)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        secs -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return secs


def read_jsonl(path: Path) -> Iterator[dict]:
    """Yield JSON objects, skipping bad lines (the file may still be growing)."""
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def norm_path(p: str) -> str:
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return p


def ago(secs: float) -> str:
    secs = max(0, int(secs))
    for unit, n in (("day", 86400), ("hour", 3600), ("min", 60)):
        if secs >= n:
            k = secs // n
            return f"{k} {unit}{'s' if k != 1 and unit != 'min' else ''} ago"
    return "just now"
