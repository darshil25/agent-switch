"""The only place sw writes to disk: saved copies of the handoffs it sent (default ~/.sw).

Handoffs contain conversation text, so the folder is private (0700) and files are 0600.
Only files sw created (handoff-*.md and latest.md) are ever pruned or deleted."""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

DEFAULT_KEEP = 20


def home() -> Path:
    return Path(os.environ.get("SW_HOME") or Path.home() / ".sw")


def _keep() -> int:
    try:
        return max(1, int(os.environ.get("SW_KEEP", DEFAULT_KEEP)))
    except ValueError:
        return DEFAULT_KEEP


def _handoffs() -> List[Path]:
    d = home()
    return sorted(d.glob("handoff-*.md"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save(text: str) -> Path:
    """Save a copy of a handoff, refresh latest.md and prune old copies. Returns the new file."""
    d = home()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    path = d / datetime.now().strftime("handoff-%Y%m%d-%H%M%S-%f.md")
    _write_private(path, text)
    _write_private(d / "latest.md", text)
    for old in _handoffs()[: -_keep()]:
        old.unlink(missing_ok=True)
    return path


def clean(everything: bool = False, older_than_days: float = 7) -> Tuple[int, int]:
    """Delete saved handoffs. Returns (files removed, bytes freed)."""
    cutoff = time.time() - older_than_days * 86400
    count = freed = 0
    victims = [p for p in _handoffs() if everything or p.stat().st_mtime < cutoff]
    if everything:
        latest = home() / "latest.md"
        if latest.exists():
            victims.append(latest)
    for p in victims:
        try:
            size = p.stat().st_size
            p.unlink()
            count += 1
            freed += size
        except OSError:
            continue
    return count, freed
