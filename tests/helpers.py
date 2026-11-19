"""Test fixtures: fake agent session files and fake agent programs. No real transcripts are used."""
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

BASE = 1_700_000_000  # a fixed point in the past; fixtures use BASE + n seconds


def iso(n):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(BASE + n)) + ".123Z"


# ---- record builders (shapes copied from real session files) ----
def cl_user(text, n, cwd="/x"):
    return {"type": "user", "cwd": cwd, "isSidechain": False, "timestamp": iso(n),
            "message": {"role": "user", "content": text}}


def cl_tool_result(n):
    return {"type": "user", "isSidechain": False, "timestamp": iso(n),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "BIG OUTPUT"}]}}


def cl_assistant(blocks, n):
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    return {"type": "assistant", "isSidechain": False, "timestamp": iso(n),
            "message": {"role": "assistant", "content": blocks}}


def cl_tool(name, inp):
    return {"type": "tool_use", "id": "t", "name": name, "input": inp}


def ki(ptype, n, **payload):
    payload["type"] = ptype
    return {"id": f"id{n}", "timestamp": iso(n), "payload": payload}


def ki_user(text, n):
    return ki("user", n, content=text, documents=[], images=[])


def ki_say(text, n):
    return ki("assistant", n, content=text, operationType="Say")


def ki_tool(n, name, args, kind, filePath=None):
    extra = {"filePath": filePath} if filePath else {}
    return ki("tool_call", n, toolCallId="c", toolName=name, args=args, kind=kind, **extra)


class Env:
    """A throwaway HOME, project folder and fake agent programs."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="swtest-"))
        self.home = os.path.join(self.tmp, "home")
        self.cwd = os.path.join(self.tmp, "proj")
        self.bin = os.path.join(self.tmp, "bin")
        self.argv_out = os.path.join(self.tmp, "argv.json")
        for d in (self.home, self.cwd, self.bin):
            os.makedirs(d)
        for name in ("claude", "kiro-cli"):
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(f"#!{sys.executable}\nimport json, os, sys\n"
                        "open(os.environ['SW_ARGV_OUT'], 'w').write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]))\n")
            os.chmod(p, 0o755)
        self.sw_home = os.path.join(self.tmp, "swhome")

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- fixtures ----
    def claude_session(self, sid, records, cwd=None, mtime=None):
        cwd = cwd or self.cwd
        d = Path(self.home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in records))
        if mtime is not None:
            os.utime(p, (BASE + mtime, BASE + mtime))
        return p

    def kiro_session(self, sid, records, title="A task", cwd=None, mtime=None, key="abc123"):
        cwd = cwd or self.cwd
        d = Path(self.home, ".kiro", "sessions", key, f"sess_{sid}")
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.json").write_text(json.dumps({"id": f"sess_{sid}", "title": title, "workspacePaths": [cwd], "rootPaths": [cwd]}))
        p = d / "messages.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in records))
        if mtime is not None:
            os.utime(p, (BASE + mtime, BASE + mtime))
        return p

    # ---- running sw ----
    def run(self, *args, tty_input=None, path_has_agents=True, cwd=None, use_sw_home=True):
        env = dict(os.environ)
        env.update(HOME=self.home, PYTHONPATH=SRC, SW_ARGV_OUT=self.argv_out)
        env["PATH"] = (self.bin + os.pathsep if path_has_agents else "") + "/usr/bin:/bin"
        if use_sw_home:
            env["SW_HOME"] = self.sw_home
        else:
            env.pop("SW_HOME", None)
        if os.path.exists(self.argv_out):
            os.remove(self.argv_out)
        cmd = [sys.executable, "-m", "agent_switch", *args]
        if tty_input is None:
            r = subprocess.run(cmd, cwd=cwd or self.cwd, env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60)
            out, err, code = r.stdout, r.stderr, r.returncode
        else:
            master, slave = pty.openpty()
            p = subprocess.Popen(cmd, cwd=cwd or self.cwd, env=env, stdin=slave, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            os.close(slave)
            os.write(master, tty_input.encode())
            out, err = p.communicate(timeout=60)
            os.close(master)
            code = p.returncode
        argv = None
        if os.path.exists(self.argv_out):
            argv = json.loads(Path(self.argv_out).read_text())
        return Result(code, out, err, argv)


class Result:
    def __init__(self, code, out, err, argv):
        self.code, self.out, self.err, self.argv = code, out, err, argv


class EnvTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.cleanup)
        # make HOME-based lookups in-process point at the fake home too
        old = os.environ.get("HOME")
        os.environ["HOME"] = self.env.home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old else os.environ.pop("HOME", None))
