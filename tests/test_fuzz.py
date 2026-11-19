"""Feed damaged session files to the parsers. They must never crash."""
import json
import random
import unittest

from agent_switch import handoff
from agent_switch.agents import AGENTS
from agent_switch.util import Session
from tests.helpers import EnvTest, cl_assistant, cl_tool, cl_tool_result, cl_user, ki, ki_say, ki_tool, ki_user

CLAUDE_RECS = [cl_user("hello", 1), cl_assistant([{"type": "text", "text": "hi"}, cl_tool("Edit", {"file_path": "/a"})], 2),
               cl_tool_result(3), cl_user("more", 4)]
KIRO_RECS = [ki_user("hello", 1), ki_say("hi", 2), ki_tool(3, "fs_write", {"path": "/a"}, "edit", "/a"), ki_user("more", 4)]


def mutate(rng, data):
    data = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        kind = rng.choice(["truncate", "garbage", "flip", "dupe", "delete", "binary"])
        if not data:
            break
        i = rng.randrange(len(data))
        if kind == "truncate":
            del data[i:]
        elif kind == "garbage":
            data[i:i] = b"\n{{{ not json ]]\n"
        elif kind == "flip":
            data[i] = rng.randrange(256)
        elif kind == "dupe":
            data[i:i] = data[: rng.randint(1, 200)]
        elif kind == "delete":
            del data[i: i + rng.randint(1, 80)]
        else:
            data[i:i] = bytes(rng.randrange(256) for _ in range(30))
    return bytes(data)


class Fuzz(EnvTest):
    def test_damaged_files_never_crash(self):
        rng = random.Random(1234)
        for agent_name, recs, make in (("claude", CLAUDE_RECS, self.env.claude_session),
                                       ("kiro", KIRO_RECS, self.env.kiro_session)):
            agent = AGENTS[agent_name]
            base = "".join(json.dumps(r) + "\n" for r in recs).encode()
            for i in range(150):
                path = make(f"f{i}", [])
                path.write_bytes(mutate(rng, base))
                s = Session(f"f{i}", path, 0)
                parsed = agent.parse(s)
                agent.describe(s)
                handoff.build(parsed, agent.label, "/p", 5000)
            self.assertTrue(agent.sessions(self.env.cwd))

    def test_random_json_shapes(self):
        rng = random.Random(99)

        def val(depth=0):
            c = rng.choice(["s", "i", "n", "b", "l", "d"] if depth < 3 else ["s", "i", "n"])
            return {"s": lambda: rng.choice(["", "user", "assistant", "text", "x" * 50]), "i": lambda: rng.randint(-5, 5),
                    "n": lambda: None, "b": lambda: rng.random() < .5,
                    "l": lambda: [val(depth + 1) for _ in range(rng.randint(0, 3))],
                    "d": lambda: {rng.choice(["type", "message", "content", "payload", "timestamp", "input", "text", "name", "args", "kind", "filePath"]): val(depth + 1)
                                  for _ in range(rng.randint(0, 4))}}[c]()

        for agent_name in ("claude", "kiro"):
            agent = AGENTS[agent_name]
            make = self.env.claude_session if agent_name == "claude" else self.env.kiro_session
            for i in range(100):
                recs = [val() for _ in range(rng.randint(1, 8))]
                path = make(f"r{i}", recs)
                agent.parse(Session(f"r{i}", path, 0))
                agent.describe(Session(f"r{i}", path, 0))


if __name__ == "__main__":
    unittest.main()
