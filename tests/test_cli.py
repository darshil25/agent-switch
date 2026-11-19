import os
import unittest

from agent_switch.util import MARKER
from tests.helpers import (EnvTest, cl_assistant, cl_user, ki, ki_say, ki_user)


def claude_conv(env, sid, n0, texts, mtime=None, cwd=None):
    recs = []
    n = n0
    for i, t in enumerate(texts):
        recs.append(cl_user(t, n) if i % 2 == 0 else cl_assistant(t, n))
        n += 1
    return env.claude_session(sid, recs, mtime=mtime if mtime is not None else n, cwd=cwd)


def kiro_conv(env, sid, n0, texts, title="A task", mtime=None, key="abc"):
    recs = []
    n = n0
    for i, t in enumerate(texts):
        recs.append(ki_user(t, n) if i % 2 == 0 else ki_say(t, n))
        n += 1
    return env.kiro_session(sid, recs, title=title, mtime=mtime if mtime is not None else n, key=key)


class NewSession(EnvTest):
    def test_claude_to_kiro_new_session(self):
        claude_conv(self.env, "a", 1, ["build a game", "started it"])
        r = self.env.run("kiro")
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.argv[:2], ["kiro-cli", "chat"])
        self.assertEqual(len(r.argv), 3)
        self.assertTrue(r.argv[2].startswith(MARKER))
        self.assertIn("build a game", r.argv[2])
        self.assertIn("started it", r.argv[2])
        self.assertIn("kiro", r.err)

    def test_kiro_to_claude_new_session(self):
        kiro_conv(self.env, "k", 1, ["make a site", "done"])
        r = self.env.run("claude")
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.argv[0], "claude")
        self.assertEqual(len(r.argv), 2)
        self.assertIn("make a site", r.argv[1])

    def test_saved_copy_matches_what_was_sent(self):
        claude_conv(self.env, "a", 1, ["goal", "reply"])
        r = self.env.run("kiro")
        with open(os.path.join(self.env.sw_home, "latest.md")) as f:
            self.assertEqual(f.read(), r.argv[2])

    def test_special_characters_survive_exactly(self):
        txt = "日本語 😀 \"dq\" 'sq' `bt` $HOME $(whoami) \\n back\\slash\nsecond line; | & > <"
        claude_conv(self.env, "a", 1, [txt, "ok\x00null"])
        r = self.env.run("kiro")
        self.assertEqual(r.code, 0, r.err)
        self.assertIn(txt, r.argv[2])
        self.assertNotIn("\x00", r.argv[2])
        self.assertIn("oknull", r.argv[2])

    def test_size_cap_enforced(self):
        claude_conv(self.env, "a", 1, ["goal"] + ["x" * 1400 for _ in range(400)])
        for cap, ok in (("999999999", 100000), ("30000", 30000), ("10", 2000)):
            r = self.env.run("kiro", "--max-chars", cap)
            self.assertEqual(r.code, 0, r.err)
            self.assertLessEqual(len(r.argv[2]), ok, cap)

    def test_dry_run_starts_nothing_and_saves_nothing(self):
        claude_conv(self.env, "a", 1, ["goal", "reply"])
        r = self.env.run("kiro", "--dry-run")
        self.assertEqual(r.code, 0)
        self.assertIsNone(r.argv)
        self.assertTrue(r.out.startswith(MARKER))
        self.assertFalse(os.path.exists(self.env.sw_home))

    def test_git_state_included_when_repo_dirty(self):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.env.cwd, check=True)
        with open(os.path.join(self.env.cwd, "tracked_by_nobody.txt"), "w") as f:
            f.write("x")
        claude_conv(self.env, "a", 1, ["goal", "reply"])
        r = self.env.run("kiro")
        self.assertIn("## Current git state", r.argv[2])
        self.assertIn("tracked_by_nobody.txt", r.argv[2])


class Resume(EnvTest):
    def setUp(self):
        super().setUp()
        # Kiro already worked here (older). Claude continued afterwards (newer).
        kiro_conv(self.env, "k1", 1, ["kiro task", "kiro reply"], title="Kiro task", mtime=3)
        claude_conv(self.env, "c1", 1, ["old claude ask", "old claude reply", "NEW CLAUDE ASK", "NEW CLAUDE REPLY"], mtime=20)
        # make the first two Claude messages older than Kiro's last activity
        self.env.claude_session("c1", [
            cl_user("old claude ask", 1), cl_assistant("old claude reply", 2),
            cl_user("NEW CLAUDE ASK", 10), cl_assistant("NEW CLAUDE REPLY", 11)], mtime=20)

    def test_resumes_with_only_the_new_messages(self):
        r = self.env.run("kiro", "-y")
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.argv[:4], ["kiro-cli", "chat", "--resume-id", "sess_k1"])
        p = r.argv[4]
        self.assertIn("NEW CLAUDE ASK", p)
        self.assertIn("NEW CLAUDE REPLY", p)
        self.assertNotIn("old claude", p)
        self.assertIn("since you were last active", p)
        self.assertNotIn("Original goal", p)

    def test_nothing_new_resumes_without_a_message(self):
        self.env.kiro_session("k1", [ki_user("kiro task", 1), ki_say("later reply", 500)], mtime=500)
        r = self.env.run("kiro", "-y")
        self.assertEqual(r.argv, ["kiro-cli", "chat", "--resume-id", "sess_k1"])
        self.assertIn("nothing new", r.err)
        self.assertFalse(os.path.exists(self.env.sw_home))

    def test_file_mtime_does_not_hide_new_messages(self):
        # Kiro's file was touched after Claude's messages (e.g. metadata written on open),
        # but its last real message is older than the Claude messages.
        self.env.kiro_session("k1", [ki_user("kiro task", 1), ki_say("kiro reply", 3)], mtime=9999)
        r = self.env.run("kiro", "-y")
        self.assertIn("NEW CLAUDE ASK", r.argv[4])

    def test_new_flag_ignores_earlier_session(self):
        r = self.env.run("kiro", "--new")
        self.assertEqual(r.argv[:2], ["kiro-cli", "chat"])
        self.assertNotIn("--resume-id", r.argv)
        self.assertIn("old claude ask", r.argv[-1])

    def test_passthrough_flags_go_before_the_message(self):
        r = self.env.run("kiro", "-y", "--", "--trust-all-tools", "--model", "m1")
        self.assertEqual(r.argv[:7], ["kiro-cli", "chat", "--resume-id", "sess_k1", "--trust-all-tools", "--model", "m1"])
        self.assertTrue(r.argv[-1].startswith(MARKER))

    def test_claude_resume_argv(self):
        claude_conv(self.env, "c0", 1, ["claude earlier", "reply"], mtime=3)
        os.remove(os.path.join(self.env.home, ".claude", "projects",
                               self.env.cwd.replace("/", "-").replace(".", "-").replace("_", "-"), "c1.jsonl"))
        self.env.kiro_session("k1", [ki_user("kiro task", 10), ki_say("kiro newer", 11)], mtime=20)
        r = self.env.run("claude", "-y")
        self.assertEqual(r.argv[:3], ["claude", "--resume", "c0"])
        self.assertIn("kiro newer", r.argv[3])


class Selection(EnvTest):
    def setUp(self):
        super().setUp()
        claude_conv(self.env, "src", 100, ["from claude", "reply"], mtime=200)
        kiro_conv(self.env, "old", 1, ["older task", "x"], title="Older task", mtime=10)
        kiro_conv(self.env, "new", 20, ["newer task", "x"], title="Newer task", mtime=50)

    def test_menu_default_and_choices(self):
        r = self.env.run("kiro", tty_input="\n")
        self.assertIn("1) Newer task", r.err)
        self.assertIn("2) Older task", r.err)
        self.assertIn("n) start a new session", r.err)
        self.assertEqual(r.argv[3], "sess_new")
        self.assertEqual(self.env.run("kiro", tty_input="2\n").argv[3], "sess_old")
        self.assertEqual(self.env.run("kiro", tty_input="9\n").argv[3], "sess_new")
        self.assertEqual(self.env.run("kiro", tty_input="junk\n").argv[3], "sess_new")
        new = self.env.run("kiro", tty_input="n\n")
        self.assertNotIn("--resume-id", new.argv)
        self.assertIn("from claude", new.argv[-1])

    def test_no_tty_defaults_to_latest_without_asking(self):
        r = self.env.run("kiro")
        self.assertEqual(r.argv[3], "sess_new")
        self.assertNotIn("Choose", r.err)

    def test_empty_sessions_are_not_offered(self):
        self.env.kiro_session("blank", [ki("agent_note", 1, content="n")], mtime=100)
        r = self.env.run("kiro", tty_input="\n")
        self.assertNotIn("blank", r.err)
        self.assertEqual(r.argv[3], "sess_new")

    def test_session_flag_finds_sessions_beyond_the_menu(self):
        for i in range(8):
            kiro_conv(self.env, f"x{i}", 1, [f"task {i}", "r"], title=f"T{i}", mtime=300 + i, key=f"kk{i}")
        r = self.env.run("kiro", "--session", "sess_x0")
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.argv[3], "sess_x0")
        bad = self.env.run("kiro", "--session", "nope")
        self.assertEqual(bad.code, 1)
        self.assertIn("no Kiro session starting with 'nope'", bad.err)


class Errors(EnvTest):
    def test_nothing_to_continue_from(self):
        r = self.env.run("kiro")
        self.assertEqual(r.code, 1)
        self.assertIn("no earlier Claude Code session found", r.err)
        self.assertNotIn("Traceback", r.err)

    def test_hint_points_at_the_folder_that_has_sessions(self):
        other = os.path.join(self.env.tmp, "elsewhere")
        os.makedirs(other)
        claude_conv(self.env, "a", 1, ["x", "y"], cwd=other)
        # claude_conv sets the record cwd to "/x"; write one that records the real folder
        self.env.claude_session("b", [cl_user("hello", 1, cwd=other), cl_assistant("hi", 2)], cwd=other)
        r = self.env.run("kiro")
        self.assertEqual(r.code, 1)
        self.assertIn(f"latest Claude Code session was in: {other}", r.err)

    def test_same_agent(self):
        kiro_conv(self.env, "k", 1, ["a", "b"])
        r = self.env.run("kiro", "--from", "kiro")
        self.assertEqual(r.code, 1)
        self.assertIn("same agent", r.err)

    def test_target_not_installed_saves_handoff_and_says_so(self):
        claude_conv(self.env, "a", 1, ["goal", "reply"])
        r = self.env.run("kiro", path_has_agents=False)
        self.assertEqual(r.code, 1)
        self.assertIn("'kiro-cli' is not installed", r.err)
        self.assertIn("saved to", r.err)
        self.assertTrue(os.path.exists(os.path.join(self.env.sw_home, "latest.md")))

    def test_session_with_only_metadata_is_skipped_for_an_older_real_one(self):
        claude_conv(self.env, "real", 1, ["real goal", "reply"], mtime=10)
        self.env.claude_session("empty", [{"type": "attachment"}], mtime=50)
        r = self.env.run("kiro")
        self.assertEqual(r.code, 0, r.err)
        self.assertIn("real goal", r.argv[2])

    def test_unknown_command_and_missing_command(self):
        self.assertNotEqual(self.env.run("bogus").code, 0)
        self.assertNotEqual(self.env.run().code, 0)


class Commands(EnvTest):
    def test_list(self):
        claude_conv(self.env, "cid1", 1, ["claude topic", "r"])
        kiro_conv(self.env, "kid1", 1, ["x", "y"], title="Kiro topic")
        r = self.env.run("list")
        self.assertEqual(r.code, 0)
        self.assertIn("cid1", r.out)
        self.assertIn("claude topic", r.out)
        self.assertIn("sess_kid1", r.out)
        self.assertIn("Kiro topic", r.out)

    def test_clean(self):
        claude_conv(self.env, "a", 1, ["goal", "reply"])
        self.env.run("kiro")
        self.assertTrue(os.path.exists(os.path.join(self.env.sw_home, "latest.md")))
        r = self.env.run("clean", "--all")
        self.assertEqual(r.code, 0)
        self.assertIn("Removed 2 file(s)", r.out)
        self.assertEqual([n for n in os.listdir(self.env.sw_home)], [])

    def test_version(self):
        self.assertIn("sw 0.1.0", self.env.run("--version").out)

    def test_default_storage_location_is_dot_sw_in_home(self):
        claude_conv(self.env, "a", 1, ["goal", "reply"])
        self.env.run("kiro", use_sw_home=False)
        self.assertTrue(os.path.exists(os.path.join(self.env.home, ".sw", "latest.md")))
        self.assertEqual(oct(os.stat(os.path.join(self.env.home, ".sw")).st_mode & 0o777), "0o700")


if __name__ == "__main__":
    unittest.main()
