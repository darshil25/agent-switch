import os
import stat
import tempfile
import time
import unittest

from agent_switch import store


class Store(unittest.TestCase):
    def setUp(self):
        self.d = os.path.join(tempfile.mkdtemp(), "swhome")
        os.environ["SW_HOME"] = self.d
        os.environ.pop("SW_KEEP", None)
        self.addCleanup(lambda: os.environ.pop("SW_HOME", None))
        self.addCleanup(lambda: os.environ.pop("SW_KEEP", None))

    def test_private_permissions(self):
        p = store.save("secret conversation")
        self.assertEqual(stat.S_IMODE(os.stat(self.d).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "latest.md")).st_mode), 0o600)
        with open(os.path.join(self.d, "latest.md")) as f:
            self.assertEqual(f.read(), "secret conversation")

    def test_loosely_permissioned_dir_is_tightened(self):
        os.makedirs(self.d, mode=0o755)
        os.chmod(self.d, 0o755)
        store.save("x")
        self.assertEqual(stat.S_IMODE(os.stat(self.d).st_mode), 0o700)

    def test_prunes_to_keep(self):
        os.environ["SW_KEEP"] = "3"
        for i in range(7):
            store.save(f"h{i}")
        names = [n for n in os.listdir(self.d) if n.startswith("handoff-")]
        self.assertEqual(len(names), 3)

    def test_bad_keep_value_falls_back(self):
        os.environ["SW_KEEP"] = "abc"
        store.save("x")  # must not raise

    def test_two_saves_same_second_do_not_collide(self):
        a, b = store.save("a"), store.save("b")
        self.assertNotEqual(a, b)

    def test_clean_only_touches_own_files(self):
        store.save("x")
        other = os.path.join(self.d, "notes.txt")
        with open(other, "w") as f:
            f.write("mine")
        os.mkdir(os.path.join(self.d, "subdir"))
        count, _ = store.clean(everything=True)
        self.assertGreaterEqual(count, 2)  # the handoff and latest.md
        self.assertTrue(os.path.exists(other))
        self.assertTrue(os.path.isdir(os.path.join(self.d, "subdir")))
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith(("handoff-", "latest"))], [])

    def test_clean_older_than(self):
        old = store.save("old")
        time.sleep(0.01)
        new = store.save("new")
        past = time.time() - 10 * 86400
        os.utime(old, (past, past))
        count, _ = store.clean(older_than_days=7)
        self.assertEqual(count, 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_clean_on_missing_dir(self):
        self.assertEqual(store.clean(everything=True), (0, 0))


if __name__ == "__main__":
    unittest.main()
