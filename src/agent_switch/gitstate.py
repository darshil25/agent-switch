"""Short description of uncommitted work, so the next agent sees the real state of the code."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

MAX_REPOS = 8
MAX_CHARS = 4000


def _git(args: List[str], cwd: str) -> Optional[str]:
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def repo_state(cwd: str) -> str:
    """Status of cwd if it is a git repo, else of git repos one level below it. Empty if clean."""
    roots: List[str] = []
    if _git(["rev-parse", "--is-inside-work-tree"], cwd) == "true":
        roots = [cwd]
    else:
        try:
            children = sorted(Path(cwd).iterdir())
        except OSError:
            children = []
        for d in children:
            if len(roots) >= MAX_REPOS:
                break
            if not d.name.startswith(".") and (d / ".git").exists():
                roots.append(str(d))
    parts = []
    for root in roots:
        status = _git(["status", "--short"], root)
        if not status:
            continue
        lines = status.splitlines()
        block = f"[{root}]\n" + "\n".join(lines[:20])
        if len(lines) > 20:
            block += f"\n... +{len(lines) - 20} more"
        stat = _git(["diff", "--stat"], root)
        if stat:
            block += "\n" + "\n".join(stat.splitlines()[-8:])
        parts.append(block)
    text = "\n\n".join(parts)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS].rstrip() + "\n... (truncated)"
