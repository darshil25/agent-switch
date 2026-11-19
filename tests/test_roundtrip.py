"""Simulates real use: switch back and forth many times and check nothing nests or grows."""
import os
import unittest

from agent_switch.util import MARKER
from tests.helpers import BASE, EnvTest, cl_assistant, cl_user, ki_say, ki_user


class Sim:
    def __init__(self, env):
        self.env = env
        self.clock = 100
        self.claude, self.kiro = [], []

    def tick(self):
        self.clock += 10
        return self.clock

    def say_claude(self, user=None, assistant=None):
        if user:
            self.claude.append(cl_user(user, self.tick()))
        if assistant:
            self.claude.append(cl_assistant(assistant, self.tick()))
        self.env.claude_session("A", self.claude, mtime=self.clock)

    def say_kiro(self, user=None, assistant=None):
        if user:
            self.kiro.append(ki_user(user, self.tick()))
        if assistant:
            self.kiro.append(ki_say(assistant, self.tick()))
        self.env.kiro_session("K", self.kiro, title="Kiro work", mtime=self.clock)


def sent_message(result):
    return result.argv[-1] if result.argv and result.argv[-1].startswith(MARKER) else None


class RoundTrip(EnvTest):
    def test_claude_kiro_claude_kiro(self):
        s = Sim(self.env)
        s.say_claude("make a game", "started the game")

        r1 = self.env.run("kiro", "-y")                       # Kiro has no session yet: new session
        p1 = sent_message(r1)
        self.assertIsNotNone(p1)
        self.assertNotIn("--resume-id", r1.argv)
        s.say_kiro(p1, "got it")                              # what Kiro records when it receives p1
        s.say_kiro("make it blue", "made it blue")

        r2 = self.env.run("claude", "-y")                     # Claude resumes its own session
        p2 = sent_message(r2)
        self.assertEqual(r2.argv[:3], ["claude", "--resume", "A"])
        self.assertIn("make it blue", p2)
        self.assertNotIn("make a game", p2)                   # Claude already knows the goal
        self.assertEqual(p2.count(MARKER), 1)
        s.say_claude(p2, "applied")
        s.say_claude("add tests", "tests added")

        r3 = self.env.run("kiro", "-y")                       # Kiro resumes its own session
        p3 = sent_message(r3)
        self.assertEqual(r3.argv[3], "sess_K")
        self.assertIn("add tests", p3)
        self.assertIn("tests added", p3)
        self.assertNotIn("make it blue", p3)                  # that was Kiro's own message
        self.assertEqual(p3.count(MARKER), 1)
        self.assertEqual(p3.count("You are resuming"), 1)

    def test_many_hops_do_not_grow_or_nest(self):
        s = Sim(self.env)
        s.say_claude("make a game", "started")
        sizes = []
        p = sent_message(self.env.run("kiro", "-y"))
        s.say_kiro(p, "ok")
        for i in range(6):
            s.say_kiro(f"kiro ask {i}", f"kiro reply {i}")
            r = self.env.run("claude", "-y")
            p = sent_message(r)
            self.assertIsNotNone(p, f"hop {i} to claude sent nothing")
            self.assertEqual(p.count(MARKER), 1)
            sizes.append(len(p))
            s.say_claude(p, "claude ack")
            s.say_claude(f"claude ask {i}", f"claude reply {i}")
            r = self.env.run("kiro", "-y")
            p = sent_message(r)
            self.assertIsNotNone(p, f"hop {i} to kiro sent nothing")
            self.assertEqual(p.count(MARKER), 1)
            self.assertIn(f"claude ask {i}", p)
            self.assertNotIn(f"kiro ask {i}", p)
            sizes.append(len(p))
            s.say_kiro(p, "kiro ack")
        self.assertLess(max(sizes), 3000, sizes)

    def test_new_session_after_switching_keeps_the_original_goal(self):
        s = Sim(self.env)
        s.say_claude("make a game", "started")
        s.say_kiro(sent_message(self.env.run("kiro", "-y")), "ok")
        s.say_kiro("make it blue", "done")
        r = self.env.run("claude", "--new")                   # fresh Claude session from Kiro's work
        p = sent_message(r)
        self.assertIn("## Original goal (from the first session)\nmake a game", p)
        self.assertEqual(p.count(MARKER), 1)
        self.assertEqual(p.count("make a game"), 1)
        self.assertIn("make it blue", p)


if __name__ == "__main__":
    unittest.main()
