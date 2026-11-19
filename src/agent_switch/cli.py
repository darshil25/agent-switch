"""Command line interface: sw <agent> | sw list | sw clean."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from typing import List, Optional, Tuple

from . import __version__, handoff, store
from .agents import AGENTS
from .agents.base import Agent
from .gitstate import repo_state
from .util import Parsed, Session, SwError, ago, norm_path

MENU_SIZE = 5


def _load_source(agent: Agent, cwd: str) -> Optional[Tuple[Session, Parsed]]:
    for s in agent.sessions(cwd)[:5]:
        parsed = agent.parse(s)
        if parsed.turns:
            return s, parsed
    return None


def _resumable(agent: Agent, cwd: str) -> List[Tuple[Session, str]]:
    out = []
    for s in agent.sessions(cwd):
        title = agent.describe(s)
        if title:
            out.append((s, title))
        if len(out) == MENU_SIZE:
            break
    return out


def _menu(agent: Agent, options: List[Tuple[Session, str]]) -> Optional[Session]:
    """Ask which earlier session to resume. Returns None for 'start a new one'."""
    now = time.time()
    err = sys.stderr
    print(f"\nEarlier {agent.label} sessions in this folder:", file=err)
    for i, (s, title) in enumerate(options, 1):
        print(f"  {i}) {title}   [{s.id[:13]}]  {ago(now - s.mtime)}" + ("   <- default" if i == 1 else ""), file=err)
    print("  n) start a new session with the full handoff", file=err)
    print("Choose [1]: ", end="", file=err, flush=True)
    answer = (sys.stdin.readline() or "").strip().lower()
    if answer in ("n", "new"):
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1][0]
    return options[0][0]


def _switch(args: argparse.Namespace) -> None:
    cwd = os.getcwd()
    target = AGENTS[args.cmd]
    if args.source == target.name:
        raise SwError("source and target are the same agent.")
    sources = [AGENTS[args.source]] if args.source else [a for a in AGENTS.values() if a is not target]

    found = []
    for a in sources:
        loaded = _load_source(a, cwd)
        if loaded:
            found.append((a, *loaded))
    if not found:
        hints = [f"latest {a.label} session was in: {a.latest_folder()}" for a in sources if a.latest_folder()]
        raise SwError(
            f"no earlier {' or '.join(a.label for a in sources)} session found for {cwd}\n"
            "Run sw from the folder where you started the agent."
            + ("\n" + "\n".join(hints) if hints else ""))
    source, _, parsed = max(found, key=lambda f: f[1].mtime)

    # Does the target already have a session in this folder? Resume it by default.
    chosen: Optional[Session] = None
    if args.session:
        matches = [s for s in target.sessions(cwd) if s.id.startswith(args.session)]
        if not matches:
            raise SwError(f"no {target.label} session starting with '{args.session}' in this folder (try: sw list)")
        chosen = matches[0]
    elif not args.new:
        options = _resumable(target, cwd)
        if options:
            chosen = options[0][0] if (args.yes or not sys.stdin.isatty()) else _menu(target, options)

    prompt = ""
    if chosen:
        since = target.parse(chosen).last_ts or chosen.mtime  # last real message, not file mtime
        delta = [t for t in parsed.turns if t["ts"] is None or t["ts"] > since]
        if delta:
            prompt = handoff.build(Parsed(turns=delta), source.label, cwd, args.max_chars,
                                   repo_state(cwd), resume=True)
        mode = (f"resuming session {chosen.id[:13]} with {len(delta)} new message(s) from {source.label}"
                if delta else f"resuming session {chosen.id[:13]}, nothing new to catch up")
    else:
        prompt = handoff.build(parsed, source.label, cwd, args.max_chars, repo_state(cwd))
        mode = f"new session with {len(parsed.turns)} message(s) from {source.label}"

    argv = target.argv(prompt, chosen.id if chosen else None, args.extra)
    if args.dry_run:
        print(prompt or "(no catch-up message: nothing new)")
        print(f"--- dry run: {source.name} -> {target.name}: {mode} ({len(prompt)} chars)", file=sys.stderr)
        return

    saved = store.save(prompt) if prompt else None
    if not shutil.which(target.binary):
        raise SwError(f"'{target.binary}' is not installed or not on PATH."
                      + (f" The handoff was saved to {saved}" if saved else ""))
    print(f"sw: {source.name} -> {target.name}: {mode}." + (f" Saved to {saved}" if saved else ""), file=sys.stderr)
    try:
        os.execvp(argv[0], argv)
    except OSError as e:
        raise SwError(f"could not start {target.binary}: {e}" + (f"\nThe handoff was saved to {saved}" if saved else ""))


def _list(_: argparse.Namespace) -> None:
    cwd = os.getcwd()
    now = time.time()
    print(f"Sessions in {norm_path(cwd)}")
    for a in AGENTS.values():
        rows = [(s, a.describe(s)) for s in a.sessions(cwd)]
        rows = [(s, t) for s, t in rows if t]
        print(f"\n{a.label}" + ("" if rows else "  (none)"))
        for s, t in rows[:10]:
            print(f"  {s.id}  {ago(now - s.mtime):<12} {t}")


def _clean(args: argparse.Namespace) -> None:
    count, freed = store.clean(everything=args.all, older_than_days=args.older_than)
    what = "all saved handoffs" if args.all else f"saved handoffs older than {args.older_than:g} days"
    print(f"Removed {count} file(s), {freed / 1024:.1f} KB ({what}) from {store.home()}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sw", description="Continue a coding session in a different AI coding CLI.")
    p.add_argument("--version", action="version", version=f"sw {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="{" + ",".join(list(AGENTS) + ["list", "clean"]) + "}")
    sub.required = True
    for a in AGENTS.values():
        sp = sub.add_parser(a.name, help=f"continue in {a.label}",
                            description=f"Continue the latest session from another agent in {a.label}.")
        sp.add_argument("--from", dest="source", choices=sorted(AGENTS), help="read this agent's session (default: the newest)")
        sp.add_argument("-n", "--new", action="store_true", help="do not resume; start a new session with the full handoff")
        sp.add_argument("-y", "--yes", action="store_true", help="do not ask; resume the most recent earlier session")
        sp.add_argument("--session", metavar="ID", help="resume this session (start of the id is enough; see sw list)")
        sp.add_argument("--max-chars", type=int, default=30000, help="size cap for the message sent (default 30000, max 100000)")
        sp.add_argument("--dry-run", action="store_true", help="print the message, start nothing, save nothing")
        sp.add_argument("extra", nargs="*", help="after --, extra flags passed to the agent")
    sub.add_parser("list", help="list sessions for this folder")
    c = sub.add_parser("clean", help="delete saved handoff copies")
    c.add_argument("--all", action="store_true", help="delete every saved handoff")
    c.add_argument("--older-than", type=float, default=7, metavar="DAYS", help="delete handoffs older than this (default 7)")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        {"list": _list, "clean": _clean}.get(args.cmd, _switch)(args)
    except SwError as e:
        sys.exit(f"sw: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
