"""Turns parsed turns into the text sent to the next agent."""
from __future__ import annotations

from typing import Dict, List

from .util import MARKER, Parsed, clip

MAX_CHARS_LIMIT = 100_000  # Linux allows ~128 KB per argument; stay well below
MAX_BYTES = 110_000
MIN_CHARS = 2_000


def _render_turn(t: Dict, limit: int) -> str:
    if t["role"] == "user":
        return "USER: " + clip(t["text"], limit)
    s = ("ASSISTANT: " + clip(t["text"], limit)) if t["text"].strip() else "ASSISTANT:"
    acts: List[str] = t.get("actions") or []
    if acts:
        s += "\n  [actions: " + "; ".join(acts[:8]) + (f"; (+{len(acts) - 8} more)" if len(acts) > 8 else "") + "]"
    return s


def fit(text: str, max_chars: int) -> str:
    """Hard guarantees: no NUL bytes, at most max_chars characters and MAX_BYTES bytes."""
    text = text.replace("\x00", "")
    limit = min(max_chars, MAX_CHARS_LIMIT)
    marker = "\n[... middle of the handoff cut to fit the size limit ...]\n"
    while len(text) > limit or len(text.encode("utf-8", "replace")) > MAX_BYTES:
        keep = min(len(text), limit) - len(marker)
        if len(text.encode("utf-8", "replace")) > MAX_BYTES:
            keep = min(keep, MAX_BYTES // 4 - len(marker))
        keep = max(keep, 200)
        text = text[: keep * 2 // 5] + marker + text[-(keep * 3 // 5):]
    return text


def build(parsed: Parsed, source_label: str, cwd: str, max_chars: int, repo_state: str = "",
          resume: bool = False) -> str:
    max_chars = max(MIN_CHARS, min(max_chars, MAX_CHARS_LIMIT))
    turns = parsed.turns
    files = parsed.files
    first_user = None

    if resume:
        # catch-up for an agent that already has its own session in this folder
        files = list(dict.fromkeys(a.split(" ", 1)[1] for t in turns for a in t.get("actions", [])
                                   if a.startswith(("edited ", "wrote "))))
        head = (
            f"{MARKER}\nYou are resuming your own earlier session in this folder. After you stopped, the work "
            f"continued in {source_label}. Below is what happened there since you were last active, then the "
            f"current state of the repo. Catch up, check the actual files before editing, then continue.\n\n"
            f"Working folder: {cwd}\n"
        )
    else:
        head = (
            f"{MARKER}\nYou are taking over a coding task that was in progress in {source_label}, which stopped "
            f"(for example a usage limit). Below is a condensed record of that session and the current state of "
            f"the repo. Continue from where it left off. Do not redo work that is already done. Check the actual "
            f"files before editing, since the record is a summary and may be incomplete. If the last assistant "
            f"message was mid-task, finish that task first.\n\nWorking folder: {cwd}\n"
        )
        if parsed.summary:
            head += f"\nEarlier summary of the session:\n{clip(parsed.summary, 3000)}\n"
        if parsed.carried_goal:
            head += f"\n## Original goal (from the first session)\n{clip(parsed.carried_goal, 3000)}\n"
        else:
            first_user = next((t for t in turns if t["role"] == "user"), None)
            if first_user:
                head += f"\n## Original goal (first user message)\n{clip(first_user['text'], 3000)}\n"
    if files:
        head += "\n## Files changed " + ("since you were last active" if resume else "in the session") + "\n"
        head += "\n".join(f"- {f}" for f in files[:40])
        head += (f"\n- (+{len(files) - 40} more)\n" if len(files) > 40 else "\n")

    tail = f"\n## Current git state\n{repo_state}\n" if repo_state else ""
    closing = "\n## Now\nContinue the task. Start by briefly saying what you understand the current state to be.\n"

    budget = max_chars - len(head) - len(tail) - len(closing) - 200
    picked: List[str] = []
    used = 0
    pool = [t for t in turns if t is not first_user]
    for i, t in enumerate(reversed(pool)):
        text = _render_turn(t, 4000 if i == 0 and t["role"] == "assistant" else 1500)
        if used + len(text) + 2 > budget:
            if not picked:  # even the newest message is too big: keep a clipped version, never nothing
                picked.append(_render_turn(t, max(300, budget - 40)))
            break
        picked.append(text)
        used += len(text) + 2
    picked.reverse()
    omitted = len(pool) - len(picked)
    convo = "\n## Conversation in " + source_label + (" since you stopped" if resume else " (recent)") + "\n"
    if omitted > 0:
        convo += f"[... {omitted} earlier messages omitted ...]\n\n"
    convo += "\n\n".join(picked) + "\n"
    return fit(head + convo + tail + closing, max_chars)
