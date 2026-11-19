import os
import subprocess
import tempfile
import unittest

from agent_switch import handoff
from agent_switch.gitstate import repo_state
from agent_switch.util import MARKER, Parsed


def turn(role, text, actions=None):
    return {"role": role, "text": text, "actions": actions or [], "ts": 1}


class Build(unittest.TestCase):
    def parsed(self, n=3):
        turns = [turn("user", "build a dashboard")]
        for i in range(n):
            turns.append(turn("assistant", f"reply {i}", ["edited /f.py"]))
            turns.append(turn("user", f"ask {i}"))
        return Parsed(turns=turns, files=["/f.py"])

    def test_marker_goal_files(self):
        t = handoff.build(self.parsed(), "Claude Code", "/proj", 30000)
        self.assertTrue(t.startswith(MARKER))
        self.assertEqual(t.count("build a dashboard"), 1)  # goal is shown once, not repeated in the chat
        self.assertIn("- /f.py", t)
        self.assertIn("## Conversation in Claude Code (recent)", t)

    def test_resume_header_has_no_goal(self):
        t = handoff.build(self.parsed(), "Kiro", "/proj", 30000, resume=True)
        self.assertIn("You are resuming your own earlier session", t)
        self.assertNotIn("Original goal", t)
        self.assertIn("since you were last active", t)

    def test_carried_goal_used(self):
        p = self.parsed()
        p.carried_goal = "the very first goal"
        t = handoff.build(p, "Kiro", "/proj", 30000)
        self.assertIn("(from the first session)\nthe very first goal", t)
        self.assertIn("build a dashboard", t)  # the first message is now ordinary recent context

    def test_size_cap_and_recent_kept(self):
        turns = [turn("user", "GOAL")] + [turn("assistant", f"msg{i} " + "x" * 1400) for i in range(500)]
        t = handoff.build(Parsed(turns=turns), "Claude Code", "/p", 20000)
        self.assertLessEqual(len(t), 20000)
        self.assertIn("msg499", t)
        self.assertNotIn("msg3 ", t)
        self.assertRegex(t, r"\[\.\.\. \d+ earlier messages omitted \.\.\.\]")

    def test_limits_are_clamped(self):
        turns = [turn("assistant", "y" * 1400) for _ in range(400)]
        self.assertLessEqual(len(handoff.build(Parsed(turns=turns), "K", "/p", 10**9)), handoff.MAX_CHARS_LIMIT)
        small = handoff.build(Parsed(turns=turns), "K", "/p", 5)  # clamped up to MIN_CHARS
        self.assertLessEqual(len(small), handoff.MIN_CHARS)
        self.assertIn("y" * 100, small)  # the newest message survives, clipped

    def test_nul_stripped(self):
        t = handoff.build(Parsed(turns=[turn("user", "a\x00b")]), "K", "/p\x00", 30000)
        self.assertNotIn("\x00", t)

    def test_bytes_cap_with_wide_characters(self):
        turns = [turn("assistant", "日本語" * 480) for _ in range(300)]
        t = handoff.build(Parsed(turns=turns), "K", "/p", 100000)
        self.assertLessEqual(len(t.encode("utf-8")), handoff.MAX_BYTES)

    def test_oversized_repo_state_still_fits(self):
        t = handoff.build(self.parsed(), "K", "/p", 5000, repo_state="M file\n" * 20000)
        self.assertLessEqual(len(t), 5000)
        self.assertTrue(t.startswith(MARKER))

    def test_empty_turns(self):
        t = handoff.build(Parsed(), "K", "/p", 30000)
        self.assertIn("## Now", t)


class GitState(unittest.TestCase):
    def git(self, cwd, *a):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

    def repo(self, path):
        os.makedirs(path, exist_ok=True)
        self.git(path, "init", "-q")
        with open(os.path.join(path, "a.txt"), "w") as f:
            f.write("1\n")
        self.git(path, "add", ".")
        self.git(path, "commit", "-qm", "init")

    def test_clean_repo_and_non_repo_are_empty(self):
        d = os.path.realpath(tempfile.mkdtemp())
        self.assertEqual(repo_state(d), "")
        self.repo(os.path.join(d, "r"))
        self.assertEqual(repo_state(os.path.join(d, "r")), "")

    def test_dirty_repo(self):
        d = os.path.realpath(tempfile.mkdtemp())
        r = os.path.join(d, "r")
        self.repo(r)
        with open(os.path.join(r, "a.txt"), "w") as f:
            f.write("2\n")
        open(os.path.join(r, "new file.txt"), "w").close()
        s = repo_state(r)
        self.assertIn("a.txt", s)
        self.assertIn("new file.txt", s)

    def test_child_repos_when_cwd_is_not_a_repo(self):
        d = os.path.realpath(tempfile.mkdtemp())
        self.repo(os.path.join(d, "one"))
        with open(os.path.join(d, "one", "a.txt"), "w") as f:
            f.write("changed\n")
        self.repo(os.path.join(d, "two"))  # clean, must not appear
        s = repo_state(d)
        self.assertIn("one", s)
        self.assertNotIn("two", s)

    def test_output_is_capped(self):
        d = os.path.realpath(tempfile.mkdtemp())
        self.repo(d)
        for i in range(300):
            open(os.path.join(d, f"untracked_file_with_a_long_name_{i}.txt"), "w").close()
        self.assertLessEqual(len(repo_state(d)), 4100)

    def test_unreadable_dir_does_not_crash(self):
        self.assertEqual(repo_state("/definitely/not/here"), "")


if __name__ == "__main__":
    unittest.main()
