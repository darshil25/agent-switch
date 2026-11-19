import json
import os
import unittest
from pathlib import Path

from agent_switch.agents import AGENTS
from agent_switch.util import MARKER, Session, extract_goal, is_handoff, iso_ts
from tests.helpers import (EnvTest, cl_assistant, cl_tool, cl_tool_result, cl_user, ki, ki_say, ki_tool,
                           ki_user)

claude, kiro = AGENTS["claude"], AGENTS["kiro"]


def parse_claude(env, records, sid="s1"):
    p = env.claude_session(sid, records)
    return claude.parse(Session(sid, p, 0))


def parse_kiro(env, records, sid="s1"):
    p = env.kiro_session(sid, records)
    return kiro.parse(Session(f"sess_{sid}", p, 0))


class ClaudeParse(EnvTest):
    def test_basic_turns_files_actions(self):
        r = parse_claude(self.env, [
            cl_user("build a thing", 1),
            cl_assistant([{"type": "thinking", "thinking": "SECRET"}, {"type": "text", "text": "ok"},
                          cl_tool("Edit", {"file_path": "/a.py"}), cl_tool("Read", {"file_path": "/b.py"}),
                          cl_tool("Bash", {"command": "ls\n-la"}), cl_tool("Write", {"file_path": "/c.py"})], 2),
            cl_tool_result(3),
            cl_assistant("done", 4),
        ])
        self.assertEqual([t["role"] for t in r.turns], ["user", "assistant"])
        a = r.turns[1]
        self.assertIn("ok", a["text"])
        self.assertIn("done", a["text"])
        self.assertNotIn("SECRET", a["text"])
        self.assertEqual(a["actions"], ["edited /a.py", "ran: ls -la", "wrote /c.py"])  # Read is silent
        self.assertEqual(r.files, ["/a.py", "/c.py"])

    def test_wrappers_and_noise_dropped(self):
        r = parse_claude(self.env, [
            cl_user("<system-reminder>x</system-reminder>real question", 1),
            cl_user("<command-name>/clear</command-name>", 2),
            cl_user("[Request interrupted by user]", 3),
            cl_user("<local-command-stdout>out</local-command-stdout>", 4),
            cl_user("   ", 5),
        ])
        self.assertEqual([t["text"] for t in r.turns], ["real question"])

    def test_text_blocks_in_user_array_kept_tool_results_dropped(self):
        rec = cl_user("x", 1)
        rec["message"]["content"] = [{"type": "tool_result", "content": "BIG"}, {"type": "text", "text": "look at this"}]
        r = parse_claude(self.env, [rec])
        self.assertEqual(r.turns[0]["text"], "look at this")

    def test_sidechain_and_meta_skipped(self):
        side = cl_assistant("subagent chatter", 2)
        side["isSidechain"] = True
        meta = cl_user("meta", 3)
        meta["isMeta"] = True
        r = parse_claude(self.env, [cl_user("hi", 1), side, meta])
        self.assertEqual(len(r.turns), 1)

    def test_bad_lines_and_truncated_tail(self):
        p = self.env.claude_session("s", [cl_user("one", 1), cl_assistant("two", 2)])
        with open(p, "a") as f:
            f.write("not json\n[1,2,3]\n\"str\"\n" + json.dumps(cl_user("three", 3))[:40])  # cut mid-record
        r = claude.parse(Session("s", p, 0))
        self.assertEqual([t["text"].strip() for t in r.turns], ["one", "two"])

    def test_empty_and_metadata_only(self):
        p = self.env.claude_session("e", [])
        self.assertEqual(claude.parse(Session("e", p, 0)).turns, [])
        r = parse_claude(self.env, [{"type": "attachment"}, {"type": "api-request-blob", "x": "y" * 10}, {"type": "last-prompt"}], "m")
        self.assertEqual(r.turns, [])
        self.assertIsNone(claude.describe(Session("m", self.env.claude_session("m", [{"type": "attachment"}]), 0)))

    def test_handoffs_are_not_nested_and_goal_is_carried(self):
        goal = "make a dashboard"
        for opening in (MARKER + "\nYou are taking over ...",
                        "You are taking over a coding task that was in progress in Kiro, which stopped",
                        "You are resuming your own earlier session in this folder. After"):
            text = opening + f"\n\n## Original goal (from the first session)\n{goal}\n\n## Recent conversation\nx"
            r = parse_claude(self.env, [cl_user(text, 1), cl_user("next ask", 2)], "h")
            self.assertEqual([t["text"] for t in r.turns], ["next ask"])
            self.assertEqual(r.carried_goal, goal)
            self.assertTrue(is_handoff(text))

    def test_compact_summary_is_summary_not_turn(self):
        rec = cl_user("This session is being continued... summary text", 1)
        rec["isCompactSummary"] = True
        r = parse_claude(self.env, [rec, cl_user("go on", 2)])
        self.assertEqual(len(r.turns), 1)
        self.assertIn("summary text", r.summary)

    def test_last_ts_counts_handoffs_and_tool_results(self):
        r = parse_claude(self.env, [cl_user("q", 10), cl_tool_result(50), cl_user(MARKER + " x", 90)])
        self.assertEqual(len(r.turns), 1)
        self.assertGreater(r.last_ts, r.turns[0]["ts"] + 70)  # the handoff at t=90 still counts as activity

    def test_unicode_quotes_huge_line(self):
        txt = "日本語 😀 \"quoted\" 'single' `tick` $HOME\nline2"
        r = parse_claude(self.env, [cl_user(txt, 1), cl_assistant("x" * 3_000_000, 2)])
        self.assertEqual(r.turns[0]["text"], txt)
        self.assertEqual(len(r.turns[1]["text"].strip()), 3_000_000)

    def test_non_string_content_shapes_do_not_crash(self):
        weird = [
            {"type": "user", "message": None},
            {"type": "user", "message": {"content": 5}},
            {"type": "assistant", "message": {"content": [None, 3, {"type": "tool_use", "input": "str"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": None}]}},
            {"type": "user", "timestamp": 12345, "message": {"content": "ok"}},
        ]
        r = parse_claude(self.env, weird)
        self.assertEqual([t["text"] for t in r.turns if t["role"] == "user"], ["ok"])


class ClaudeSessions(EnvTest):
    def test_finds_newest_first_and_ignores_agent_files(self):
        self.env.claude_session("old", [cl_user("a", 1)], mtime=10)
        self.env.claude_session("new", [cl_user("b", 2)], mtime=20)
        self.env.claude_session("agent-123", [cl_user("sub", 3)], mtime=30)
        ids = [s.id for s in claude.sessions(self.env.cwd)]
        self.assertEqual(ids, ["new", "old"])

    def test_other_folder_not_matched(self):
        self.env.claude_session("x", [cl_user("a", 1)], cwd=self.env.cwd + "-other")
        self.assertEqual(claude.sessions(self.env.cwd), [])

    def test_trailing_slash_and_symlink(self):
        self.env.claude_session("s", [cl_user("a", 1)])
        self.assertEqual(len(claude.sessions(self.env.cwd + "/")), 1)
        link = os.path.join(self.env.tmp, "link")
        os.symlink(self.env.cwd, link)
        self.assertEqual(len(claude.sessions(link)), 1)

    def test_fallback_matches_recorded_cwd(self):
        # stored under a folder key that does not match the usual rule
        d = os.path.join(self.env.home, ".claude", "projects", "weird-key")
        os.makedirs(d)
        with open(os.path.join(d, "s.jsonl"), "w") as f:
            f.write(json.dumps(cl_user("a", 1, cwd=self.env.cwd)) + "\n")
        self.assertEqual([s.id for s in claude.sessions(self.env.cwd)], ["s"])

    def test_describe_titles(self):
        p = self.env.claude_session("t", [cl_user("fix the login bug please", 1)])
        self.assertEqual(claude.describe(Session("t", p, 0)), "fix the login bug please")
        p = self.env.claude_session("c", [cl_user(MARKER + "\nx\n## Original goal\nbuild a game\n## Recent", 1)])
        self.assertEqual(claude.describe(Session("c", p, 0)), "continued: build a game")


class KiroParse(EnvTest):
    def test_basic(self):
        r = parse_kiro(self.env, [
            ki("agent_note", 0, content="note"),
            ki_user("do it", 1),
            ki("assistant", 2, content="thinking...", operationType="Reasoning"),
            ki_say("sure", 3),
            ki_tool(4, "read_file", {"path": "/r"}, "read"),
            ki_tool(5, "fs_write", {"path": "/w", "text": "t"}, "edit", filePath="/w"),
            ki_tool(6, "execute_bash", {"command": "ls"}, "execute"),
            ki_tool(7, "update_session_information", {}, "other"),
            ki("tool_result", 8, content="HUGE", toolCallId="c"),
        ])
        self.assertEqual([t["role"] for t in r.turns], ["user", "assistant"])
        self.assertNotIn("thinking...", r.turns[1]["text"])
        self.assertEqual(r.turns[1]["actions"], ["edited /w", "ran: ls"])
        self.assertEqual(r.files, ["/w"])
        self.assertGreater(r.last_ts, r.turns[0]["ts"])

    def test_handoff_filtered_goal_carried(self):
        text = MARKER + "\nYou are taking over\n\n## Original goal (first user message)\nmake x\n\n## Recent"
        r = parse_kiro(self.env, [ki_user(text, 1), ki_user("then do y", 2)])
        self.assertEqual([t["text"] for t in r.turns], ["then do y"])
        self.assertEqual(r.carried_goal, "make x")

    def test_garbage_tolerated(self):
        p = self.env.kiro_session("g", [ki_user("a", 1)])
        with open(p, "a") as f:
            f.write("garbage\n{\"payload\": 5}\n{\"payload\": {\"type\": \"user\", \"content\": 7}}\n{\"payload\":")
        self.assertEqual(len(kiro.parse(Session("sess_g", p, 0)).turns), 1)


class KiroSessions(EnvTest):
    def test_matching(self):
        self.env.kiro_session("a", [ki_user("x", 1)], mtime=5)
        self.env.kiro_session("b", [ki_user("y", 1)], cwd=self.env.cwd + "x", key="k2")
        self.assertEqual([s.id for s in kiro.sessions(self.env.cwd)], ["sess_a"])

    def test_broken_metadata_and_missing_messages_ignored(self):
        self.env.kiro_session("ok", [ki_user("x", 1)])
        d = os.path.join(self.env.home, ".kiro", "sessions", "k3", "sess_bad")
        os.makedirs(d)
        Path(d, "session.json").write_text("{not json")
        d2 = os.path.join(self.env.home, ".kiro", "sessions", "k4", "sess_nomsg")
        os.makedirs(d2)
        Path(d2, "session.json").write_text(json.dumps({"workspacePaths": [self.env.cwd]}))
        d3 = os.path.join(self.env.home, ".kiro", "sessions", "k5", "sess_wrongtype")
        os.makedirs(d3)
        Path(d3, "session.json").write_text(json.dumps({"workspacePaths": 5}))
        self.assertEqual([s.id for s in kiro.sessions(self.env.cwd)], ["sess_ok"])

    def test_describe(self):
        p = self.env.kiro_session("t", [ki_user("x", 1)], title="Improve animation")
        self.assertEqual(kiro.describe(Session("sess_t", p, 0)), "Improve animation")
        p = self.env.kiro_session("h", [ki_user("x", 1)], title=MARKER + " You are taking over")
        self.assertEqual(kiro.describe(Session("sess_h", p, 0)), "continued session")
        p = self.env.kiro_session("e", [ki("agent_note", 1, content="n")])
        self.assertIsNone(kiro.describe(Session("sess_e", p, 0)))


class Util(unittest.TestCase):
    def test_iso_ts(self):
        self.assertEqual(iso_ts("1970-01-01T00:00:01Z"), 1)
        self.assertAlmostEqual(iso_ts("1970-01-01T00:00:01.500Z"), 1.5)
        self.assertEqual(iso_ts("1970-01-01T01:00:00+01:00"), 0)
        self.assertEqual(iso_ts("1970-01-01T00:00:01.123456789Z") // 1, 1)
        for bad in (None, "", "yesterday", 5, "2026-13-45T99:99:99Z"):
            self.assertIsNone(iso_ts(bad))

    def test_extract_goal(self):
        self.assertEqual(extract_goal("a\n## Original goal (x)\nhello\nworld\n## Next"), "hello\nworld")
        self.assertEqual(extract_goal("no goal here"), "")


if __name__ == "__main__":
    unittest.main()
