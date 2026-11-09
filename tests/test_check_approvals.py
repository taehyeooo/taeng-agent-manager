"""scripts/check_approvals.py — the CI backstop: approvals are append-only."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_approvals.py"


class CheckApprovalsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.git("init", "-q")
        self.f = self.d / "projects" / "a" / "project.json"
        self.f.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=self.d,
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, approvals):
        self.f.write_text(json.dumps({"approvals": approvals}), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "x")
        return self.git("rev-parse", "HEAD")

    def check(self, base):
        return subprocess.run([sys.executable, str(SCRIPT), base, "HEAD"], cwd=self.d, capture_output=True,
                              text=True, encoding="utf-8").returncode

    def test_append_ok_tamper_fails(self):
        base = self.commit([{"gate": "push", "ref": "x"}])
        self.commit([{"gate": "push", "ref": "x"}, {"gate": "spec", "ref": None}])
        self.assertEqual(self.check(base), 0)
        self.commit([{"gate": "push", "ref": None}])  # ref 넓히기 = 기존 기록 변경
        self.assertEqual(self.check(base), 1)
        self.commit([])  # 지우기
        self.assertEqual(self.check(base), 1)

    def test_first_push_skips(self):
        self.commit([])
        self.assertEqual(self.check("0" * 40), 0)
        self.assertEqual(self.check(""), 0)


if __name__ == "__main__":
    unittest.main()
