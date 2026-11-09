"""config/agents.json 배정 선호: Codex를 기한까지 우선(디자인 포함), 기한 뒤에는 Claude로."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from am import state as S  # noqa: E402

REAL = json.loads((Path(__file__).resolve().parent.parent / "config" / "agents.json").read_text(encoding="utf-8"))


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        (Path(self.root) / "config").mkdir()
        (Path(self.root) / "config" / "agents.json").write_text(json.dumps(REAL), encoding="utf-8")
        self.p = S.new_project("app", "app", "x", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, d):
        """Pin 'today' for routing()."""
        real = S.routing
        return mock.patch.object(S, "routing", lambda root=None, today=None: real(root, d))

    def test_repo_config_prefers_codex_for_everything_until_nov_30(self):
        self.assertEqual(REAL["prefer_until"], "2026-11-30")
        with self.at(date(2026, 11, 30)):
            for kind in S.TASK_KINDS:
                self.assertEqual(S.pick_agent(kind, self.root), "codex", kind)
            t = S.add_task(self.p, "화면 시안", kind="design", root=self.root)
            self.assertEqual(t["agent"], "codex")
            self.assertIn("자동 배정", t["note"])

    def test_after_deadline_everything_goes_to_claude(self):
        with self.at(date(2026, 12, 1)):
            self.assertEqual(S.pick_agent("design", self.root), "claude")
            with self.assertRaises(S.StateError):  # 기간 끝난 에이전트에 직접 배정 불가
                S.add_task(self.p, "x", "codex", root=self.root)

    def test_warnings(self):
        with self.at(date(2026, 11, 28)):
            self.assertIn("D-2", S.routing_warning(self.p, self.root))
        with self.at(date(2026, 11, 20)):
            self.assertEqual(S.routing_warning(self.p, self.root), "")
        with self.at(date(2026, 11, 20)):
            S.add_task(self.p, "API", root=self.root)
        with self.at(date(2026, 12, 2)):
            w = S.routing_warning(self.p, self.root)
            self.assertIn("끝났습니다", w)
            self.assertIn("T1", w)  # codex에 남은 작업 재배정 안내

    def test_cross_verification_relaxes_only_when_other_model_unavailable(self):
        with self.at(date(2026, 11, 20)):
            S.add_task(self.p, "UI", "claude", root=self.root)
            with self.assertRaises(S.StateError):
                S.verify_task(self.p, "T1", "claude:other-session", "pass", "pass", "pass", root=self.root)
        with self.at(date(2026, 12, 2)):  # codex 기간 끝 → claude 작업을 claude의 다른 세션이 검증 가능
            S.verify_task(self.p, "T1", "claude:other-session", "pass", "pass", "pass", root=self.root)
            S.update_task(self.p, "T1", root=self.root, status="review")

    def test_invariants_checked_after_change(self):
        with self.at(date(2026, 11, 20)):
            S.add_task(self.p, "x", "codex", root=self.root)
            S.verify_task(self.p, "T1", "claude", "pass", "pass", "pass", root=self.root)
            with self.assertRaises(S.StateError):  # 검증 뒤 구현자를 검증자와 같게 바꾸기
                S.update_task(self.p, "T1", root=self.root, status="review", agent="claude")
            S.approve(self.p, "merge_main", "PR#3")
            S.update_task(self.p, "T1", root=self.root, status="done", pr="3")
            with self.assertRaises(S.StateError):  # done 뒤 승인 없는 PR로 바꾸기
                S.update_task(self.p, "T1", root=self.root, pr="4")


if __name__ == "__main__":
    unittest.main()
