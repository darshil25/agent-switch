"""Claude Code. Sessions are JSONL files at ~/.claude/projects/<folder-key>/<session-id>.jsonl."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..util import (Parsed, Session, clean_user_text, extract_goal, is_handoff, iso_ts, norm_path,
                    read_jsonl, short)
from .base import Agent

_SILENT_TOOLS = {"Read", "Grep", "Glob", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"}


def _action(name: str, inp: dict, files: Dict[str, bool]) -> Optional[str]:
    path = inp.get("file_path") or inp.get("notebook_path")
    if name in ("Edit", "MultiEdit", "NotebookEdit") and path:
        files[path] = True
        return f"edited {path}"
    if name == "Write" and path:
        files[path] = True
        return f"wrote {path}"
    if name == "Bash":
        return "ran: " + str(inp.get("command") or "").replace("\n", " ")[:80]
    if name in _SILENT_TOOLS:
        return None
    return name


class ClaudeAgent(Agent):
    name = "claude"
    label = "Claude Code"
    binary = "claude"

    def _root(self) -> Path:
        return Path.home() / ".claude" / "projects"

    def _files_in(self, d: Path) -> List[Path]:
        # agent-*.jsonl are subagent transcripts that older versions stored next to sessions
        return [p for p in d.glob("*.jsonl") if not p.name.startswith("agent-")]

    def sessions(self, cwd: str) -> List[Session]:
        root = self._root()
        folders = {norm_path(cwd), cwd.rstrip("/") or "/"}
        files: Dict[Path, None] = {}
        for folder in folders:
            d = root / re.sub(r"[^A-Za-z0-9]", "-", folder)
            if d.is_dir():
                for p in self._files_in(d):
                    files[p] = None
        if not files and root.is_dir():  # folder key did not match, look at the recorded cwd instead
            want = norm_path(cwd)
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                for p in self._files_in(d):
                    for i, r in enumerate(read_jsonl(p)):
                        if r.get("cwd"):
                            if norm_path(r["cwd"]) == want:
                                files[p] = None
                            break
                        if i > 40:
                            break
        out = []
        for p in files:
            try:
                out.append(Session(p.stem, p, p.stat().st_mtime))
            except OSError:
                continue
        return sorted(out, key=lambda s: s.mtime, reverse=True)

    def describe(self, session: Session) -> Optional[str]:
        for i, r in enumerate(read_jsonl(session.path)):
            if r.get("type") == "user" and not r.get("isSidechain") and not r.get("isMeta"):
                msg = r.get("message")
                c = msg.get("content") if isinstance(msg, dict) else None
                text = clean_user_text(c) if isinstance(c, str) else ""
                if text:
                    if is_handoff(text):
                        return "continued: " + short(extract_goal(text) or "earlier session", 45)
                    return short(text, 60)
            if i > 400:
                break
        return None

    def parse(self, session: Session) -> Parsed:
        out = Parsed()
        files: Dict[str, bool] = {}
        cur = None
        for r in read_jsonl(session.path):
            t = r.get("type")
            if t == "summary":
                out.summary = r.get("summary") or out.summary
                continue
            if t not in ("user", "assistant") or r.get("isSidechain") or r.get("isMeta"):
                continue
            ts = iso_ts(r.get("timestamp"))
            if ts is not None and (out.last_ts is None or ts > out.last_ts):
                out.last_ts = ts
            msg = r.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, (str, list)):
                content = None
            if t == "user":
                if isinstance(content, str):
                    text = clean_user_text(content)
                else:  # list: keep text blocks, drop tool results
                    text = clean_user_text("\n".join(
                        b.get("text", "") for b in content or []
                        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)))
                if not text:
                    continue
                if r.get("isCompactSummary"):
                    out.summary = text
                    continue
                if is_handoff(text):  # an earlier sw handoff: keep its goal, never nest it
                    out.carried_goal = out.carried_goal or extract_goal(text) or None
                    continue
                out.turns.append({"role": "user", "text": text, "actions": [], "ts": ts})
                cur = None
                continue
            if cur is None:
                cur = {"role": "assistant", "text": "", "actions": [], "ts": ts}
                out.turns.append(cur)
            cur["ts"] = ts
            if isinstance(content, str):
                cur["text"] += content + "\n"
                continue
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    cur["text"] += b["text"] + "\n"
                elif b.get("type") == "tool_use":
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    a = _action(str(b.get("name", "tool")), inp, files)
                    if a:
                        cur["actions"].append(a)
        out.turns = [t for t in out.turns if t["text"].strip() or t["actions"]]
        out.files = list(files)
        return out

    def latest_folder(self) -> Optional[str]:
        root = self._root()
        if not root.is_dir():
            return None
        newest = None
        for p in root.glob("*/*.jsonl"):
            if p.name.startswith("agent-"):
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if newest is None or m > newest[0]:
                newest = (m, p)
        if newest:
            for r in read_jsonl(newest[1]):
                if r.get("cwd"):
                    return r["cwd"]
        return None

    def argv(self, prompt: str, resume_id: Optional[str], extra: Sequence[str]) -> List[str]:
        cmd = [self.binary]
        if resume_id:
            cmd += ["--resume", resume_id]
        cmd += list(extra)
        if prompt:
            cmd.append(prompt)
        return cmd
