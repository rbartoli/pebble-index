import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import index_to_daily as m  # noqa: E402

CAPTURE = "---\ncreated: 2026-09-03T10:59\ntags: [index]\n---\n\nBuy milk.\n"


class Helpers(unittest.TestCase):
    def test_split_frontmatter(self):
        fm, body = m.split_frontmatter(CAPTURE)
        self.assertIn("tags: [index]", fm)
        self.assertEqual(body, "\nBuy milk.\n")
        self.assertEqual(m.split_frontmatter("plain"), ("", "plain"))

    def test_capture_date_prefers_created_then_filename(self):
        self.assertEqual(m.capture_date("2026-09-04 0101.md", "created: 2026-09-03T23:59"), "2026-09-03")
        self.assertEqual(m.capture_date("2026-09-04 0101.md", ""), "2026-09-04")

    def test_appended_formats(self):
        self.assertEqual(m.appended("a\n", "b"), "a\n\nb\n")
        self.assertEqual(m.appended("a", "b"), "a\n\nb\n")
        self.assertEqual(m.appended("a\n\n\n", "b"), "a\n\nb\n")
        self.assertEqual(m.appended(None, "b"), m.DAILY_STUB + "\nb\n")


class Process(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "Voice notes").mkdir()
        (self.vault / "Daily notes").mkdir()
        self.state = m.State(self.vault / "state.json")
        self.old = time.time() - 3600

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, name, text=CAPTURE, age=None):
        p = self.vault / "Voice notes" / name
        p.write_text(text)
        t = self.old if age is None else time.time() - age
        os.utime(p, (t, t))
        return p

    def daily(self, day, text, age=3600):
        p = self.vault / "Daily notes" / f"{day}.md"
        p.write_text(text)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_append_and_trash(self):
        self.capture("2026-09-03 1059.md")
        d = self.daily("2026-09-03", "---\nprocessed: false\n---\nfirst\n")
        n = m.process(self.vault, self.state)
        self.assertEqual(n, 1)
        self.assertEqual(d.read_text(), "---\nprocessed: false\n---\nfirst\n\nBuy milk.\n")
        self.assertFalse((self.vault / "Voice notes" / "2026-09-03 1059.md").exists())
        self.assertTrue((self.vault / ".trash" / "2026-09-03 1059.md").exists())
        self.assertEqual(json.loads((self.vault / "state.json").read_text()), [])

    def test_creates_missing_daily_with_stub(self):
        self.capture("2026-09-03 1059.md")
        m.process(self.vault, self.state)
        self.assertEqual((self.vault / "Daily notes" / "2026-09-03.md").read_text(), m.DAILY_STUB + "\nBuy milk.\n")

    def test_ignores_non_captures_and_unsettled(self):
        self.capture("7 Aug at 21-11.md", "hello")
        self.capture("2026-09-03 1200.md", "---\ncreated: x\n---\nno tag")
        self.capture("2026-09-03 1201.md", age=10)
        self.assertEqual(m.process(self.vault, self.state), 0)
        self.assertEqual(len(list((self.vault / "Voice notes").iterdir())), 3)

    def test_waits_while_daily_is_being_edited(self):
        self.capture("2026-09-03 1059.md")
        self.daily("2026-09-03", "x\n", age=5)
        self.assertEqual(m.process(self.vault, self.state), 0)
        self.assertTrue((self.vault / "Voice notes" / "2026-09-03 1059.md").exists())

    def test_same_minute_duplicates_in_order(self):
        self.capture("2026-09-03 2042.md", CAPTURE.replace("Buy milk.", "Mont Jack"))
        self.capture("2026-09-03 2042 (2).md", CAPTURE.replace("Buy milk.", "Munch Jack"))
        m.process(self.vault, self.state)
        self.assertEqual(
            (self.vault / "Daily notes" / "2026-09-03.md").read_text(),
            m.DAILY_STUB + "\nMunch Jack\n\nMont Jack\n",
        )

    def test_already_appended_is_only_trashed(self):
        self.capture("2026-09-03 1059.md")
        self.state.done.add("2026-09-03 1059.md@2026-09-03")
        m.process(self.vault, self.state)
        self.assertFalse((self.vault / "Daily notes" / "2026-09-03.md").exists())
        self.assertTrue((self.vault / ".trash" / "2026-09-03 1059.md").exists())

    def test_dry_run_changes_nothing(self):
        self.capture("2026-09-03 1059.md")
        m.process(self.vault, self.state, dry_run=True)
        self.assertTrue((self.vault / "Voice notes" / "2026-09-03 1059.md").exists())
        self.assertFalse((self.vault / "Daily notes" / "2026-09-03.md").exists())

    def test_trash_collision_suffix(self):
        (self.vault / ".trash").mkdir()
        (self.vault / ".trash" / "2026-09-03 1059.md").write_text("old")
        self.capture("2026-09-03 1059.md")
        m.process(self.vault, self.state)
        self.assertTrue((self.vault / ".trash" / "2026-09-03 1059 (trash 2).md").exists())


class Main(unittest.TestCase):
    def test_vault_is_required(self):
        env = {k: v for k, v in os.environ.items() if k != "VAULT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(m.main(["--state", "/nonexistent/state.json"]), 2)


if __name__ == "__main__":
    unittest.main()
