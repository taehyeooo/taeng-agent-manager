"""python -m unittest discover -s tests (from repo root)"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from am import cli, state as S  # noqa: E402


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def am(self, *args):
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                cli.main(["--root", self.root, *args])
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue()

    def test_new_and_list(self):
        self.assertEqual(self.am("new", "todo-app", "--idea", "할 일 앱")[0], 0)
        code, out = self.am("list")
        self.assertIn("todo-app", out)
        self.assertIn("planning", out)

    def test_bad_slug_and_duplicate(self):
        self.assertEqual(self.am("new", "Bad Slug", "--idea", "x")[0], 2)
        self.am("new", "app1", "--idea", "x")
        self.assertEqual(self.am("new", "app1", "--idea", "x")[0], 2)

    def test_phase_gate_requires_approval(self):
        self.am("new", "app1", "--idea", "x")
        code, out = self.am("phase", "app1", "spec_ready")
        self.assertEqual(code, 2)
        self.assertIn("spec", out)
        self.am("approve", "app1", "spec", "--note", "사용자 확정")
        self.assertEqual(self.am("phase", "app1", "spec_ready")[0], 0)
        # repo_ready needs both approval and a recorded repo
        self.assertEqual(self.am("phase", "app1", "repo_ready")[0], 2)
        self.am("approve", "app1", "repo_create")
        self.assertEqual(self.am("phase", "app1", "repo_ready")[0], 2)
        self.am("set", "app1", "--repo", "owner/app1")
        self.assertEqual(self.am("phase", "app1", "repo_ready")[0], 0)

    def test_require_with_ref(self):
        self.am("new", "app1", "--idea", "x")
        self.assertEqual(self.am("require", "app1", "merge_main", "--ref", "PR#1")[0], 1)
        self.am("approve", "app1", "merge_main", "--ref", "PR#1")
        self.assertEqual(self.am("require", "app1", "merge_main", "--ref", "PR#1")[0], 0)
        self.assertEqual(self.am("require", "app1", "merge_main", "--ref", "PR#2")[0], 1)

    def test_tasks_and_next_action(self):
        self.am("new", "app1", "--idea", "x")
        self.am("task", "add", "app1", "로그인 API", "--agent", "codex")
        self.am("task", "set", "app1", "T1", "--status", "dispatched", "--dispatch", "d_1")
        p = S.load("app1", self.root)
        p["phase"] = "developing"
        self.assertIn("d_1", S.next_action(p))
        self.assertEqual(self.am("task", "set", "app1", "T9", "--status", "done")[0], 2)
        code, out = self.am("status", "app1")
        self.assertIn("[dispatched", out)

    def test_state_is_utf8_json_and_local_paths_separate(self):
        self.am("new", "app1", "--idea", "한글 아이디어")
        self.am("set", "app1", "--local-path", self.root)
        raw = (Path(self.root) / "projects/app1/project.json").read_text(encoding="utf-8")
        self.assertIn("한글 아이디어", raw)
        self.assertNotIn(self.root.replace("\\", "\\\\"), raw)  # machine path not in shared state
        self.assertIn("app1", json.loads((Path(self.root) / ".am/local.json").read_text(encoding="utf-8"))["paths"])

    def test_restore_dry_run(self):
        self.am("new", "app1", "--idea", "x")
        self.am("set", "app1", "--repo", "owner/app1")
        self.assertEqual(self.am("restore")[0], 1)  # no workspace yet
        code, out = self.am("restore", "--workspace", self.root)
        self.assertEqual(code, 0)
        self.assertIn("gh repo clone owner/app1", out)
        self.assertIn("--apply", out)


if __name__ == "__main__":
    unittest.main()
