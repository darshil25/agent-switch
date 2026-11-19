"""Kiro CLI (v3 engine, the default). Sessions live at ~/.kiro/sessions/<hash>/sess_<id>/
with session.json (metadata, workspace folder) and messages.jsonl (the conversation)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..util import (Parsed, Session, clean_user_text, extract_goal, is_handoff, iso_ts, norm_path,
                    read_jsonl, short)
from .base import Agent


def _meta(session: Session) -> dict:
    try:
        m = json.loads((session.path.parent / "session.json").read_text())
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


class KiroAgent(Agent):
    name = "kiro"
    label = "Kiro"
    binary = "kiro-cli"

    def _root(self) -> Path:
        return Path.home() / ".kiro" / "sessions"

    def sessions(self, cwd: str) -> List[Session]:
        want = norm_path(cwd)
        out = []
        for meta in self._root().glob("*/sess_*/session.json"):
            log = meta.parent / "messages.jsonl"
            try:
                m = json.loads(meta.read_text())
                paths = (m.get("workspacePaths") or []) + (m.get("rootPaths") or [])
                if want in {norm_path(p) for p in paths if isinstance(p, str)} and log.exists():
                    out.append(Session(meta.parent.name, log, log.stat().st_mtime))
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        return sorted(out, key=lambda s: s.mtime, reverse=True)

    def describe(self, session: Session) -> Optional[str]:
        if not any(isinstance(r.get("payload"), dict) and r["payload"].get("type") == "user"
                   for r in read_jsonl(session.path)):
            return None
        title = str(_meta(session).get("title") or "")
        if not title or is_handoff(title):
            return "continued session"
        return short(title, 60)

    def parse(self, session: Session) -> Parsed:
        out = Parsed()
        files: Dict[str, bool] = {}
        cur = None
        for r in read_jsonl(session.path):
            p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            t = p.get("type")
            ts = iso_ts(r.get("timestamp"))
            if t in ("user", "assistant", "tool_call", "tool_result") and ts is not None \
                    and (out.last_ts is None or ts > out.last_ts):
                out.last_ts = ts
            if t == "user":
                c = p.get("content")
                text = clean_user_text(c if isinstance(c, str) else "")
                if not text:
                    continue
                if is_handoff(text):
                    out.carried_goal = out.carried_goal or extract_goal(text) or None
                    continue
                out.turns.append({"role": "user", "text": text, "actions": [], "ts": ts})
                cur = None
            elif t in ("assistant", "tool_call"):
                if t == "assistant" and p.get("operationType") == "Reasoning":
                    continue
                if cur is None:
                    cur = {"role": "assistant", "text": "", "actions": [], "ts": ts}
                    out.turns.append(cur)
                cur["ts"] = ts
                if t == "assistant":
                    cur["text"] += str(p.get("content") or "") + "\n"
                    continue
                name = str(p.get("toolName", "tool"))
                args = p.get("args") if isinstance(p.get("args"), dict) else {}
                fp = p.get("filePath") or (args.get("path") if p.get("kind") == "edit" else None)
                if fp:
                    files[fp] = True
                    cur["actions"].append(f"edited {fp}")
                elif name == "execute_bash":
                    cur["actions"].append("ran: " + str(args.get("command", "")).replace("\n", " ")[:80])
                elif p.get("kind") not in ("read", "other"):
                    cur["actions"].append(name)
        out.turns = [t for t in out.turns if t["text"].strip() or t["actions"]]
        out.files = list(files)
        return out

    def latest_folder(self) -> Optional[str]:
        newest = None
        for meta in self._root().glob("*/sess_*/session.json"):
            try:
                m = meta.stat().st_mtime
            except OSError:
                continue
            if newest is None or m > newest[0]:
                newest = (m, meta)
        if newest:
            try:
                paths = json.loads(newest[1].read_text()).get("workspacePaths") or []
                return paths[0] if paths else None
            except (OSError, ValueError, AttributeError, IndexError):
                return None
        return None

    def argv(self, prompt: str, resume_id: Optional[str], extra: Sequence[str]) -> List[str]:
        cmd = [self.binary, "chat"]
        if resume_id:
            cmd += ["--resume-id", resume_id]
        cmd += list(extra)
        if prompt:
            cmd.append(prompt)
        return cmd
