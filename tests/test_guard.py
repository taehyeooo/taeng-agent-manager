"""Execution-time guard: real git repos in a temp dir, payloads shaped like Claude Code / Codex hooks."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from am import cli, guard as G, report, state as S  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = str(base / "mgr")
        self.app = base / "app"
        self.other = base / "other"
        for repo in (self.app, self.other):
            repo.mkdir(parents=True)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        git(self.app, "checkout", "-q", "-b", "feat/x")
        S.new_project("app", "app", "x", self.root)
        cfg = S.load_local(self.root)
        cfg["paths"]["app"] = str(self.app)
        S.save_local(cfg, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def bash(self, cmd, cwd=None, agent="claude"):
        return G.decide({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd or self.app)}, agent, self.root)

    def tool(self, name, path, cwd=None):
        return G.decide({"tool_name": name, "tool_input": {"file_path": str(path)}, "cwd": str(cwd or self.app)}, "claude", self.root)

    def approve(self, gate, ref=None):
        p = S.load("app", self.root)
        S.approve(p, gate, ref)
        S.save(p, self.root)

    # --- allowed without asking
    def test_safe_work_is_allowed(self):
        for cmd in ("python -m unittest", "npm test", "git status", "git commit -m 'feat: x'", "git diff projects/a/project.json 2>&1",
                    "rm -rf node_modules dist", "git push --help | head"):
            if "push" in cmd:
                continue
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)
        self.assertEqual(self.tool("Edit", self.app / "src" / "a.py")[0], "allow")
        self.assertEqual(self.tool("Read", self.app / ".env.example")[0], "allow")

    # --- forbidden even with approvals
    def test_forbidden_always_denied(self):
        for gate in S.GATES:
            self.approve(gate)
        for cmd in ("claude --dangerously-skip-permissions", "codex --dangerously-bypass-approvals-and-sandbox exec x",
                    "git commit --no-verify -m x", "gh auth token", "echo $GITHUB_TOKEN", "git push --force origin main",
                    f"echo '{{}}' > projects/app/project.json", "sed -i s/a/b/ .am/local.json"):
            d, msg = self.bash(cmd, cwd=self.root if "project" in cmd or ".am" in cmd else None)
            self.assertEqual(d, "deny", cmd)
            self.assertIn("금지", msg)
        self.assertEqual(self.bash("git push --force-with-lease origin feat/x")[0], "allow")

    def test_state_files_and_outside_paths_denied_for_write_tools(self):
        self.assertEqual(self.tool("Write", Path(self.root) / "projects/app/project.json", cwd=self.root)[0], "deny")
        self.assertEqual(self.tool("Edit", self.other / "main.py")[0], "deny")  # 관련 없는 프로젝트
        self.assertEqual(self.tool("Write", Path(tempfile.gettempdir()) / "brief.md")[0], "allow")

    # --- gated: blocked until approved, request recorded with command/impact/risk
    def test_push_requires_matching_approval(self):
        d, msg = self.bash("git push -u origin feat/x")
        self.assertEqual(d, "deny")
        for part in ("명령: git push -u origin feat/x", "영향 범위", "위험", "python -m am approve app push --ref feat/x"):
            self.assertIn(part, msg)
        req = S.pending_requests(S.load("app", self.root))
        self.assertEqual((req[0]["gate"], req[0]["ref"]), ("push", "feat/x"))
        self.approve("push", "other-branch")
        self.assertEqual(self.bash("git push -u origin feat/x")[0], "deny")
        self.approve("push", "feat/x")
        self.assertEqual(self.bash("git push -u origin feat/x")[0], "allow")
        self.assertEqual(self.bash("git push")[0], "allow")  # current branch = feat/x
        self.assertEqual(S.pending_requests(S.load("app", self.root)), [])
        self.assertIn("승인된 작업 실행", S.load("app", self.root)["history"][-1]["event"])

    def test_merge_is_per_pr(self):
        self.approve("merge_main", "PR#3")
        self.assertEqual(self.bash("gh pr merge 3 --squash")[0], "allow")
        self.assertEqual(self.bash("gh pr merge 4 --squash")[0], "deny")

    def test_gates_detected(self):
        cases = {"gh repo create me/x --private": "repo_create", "gh repo edit --visibility public": "repo_create",
                 "rm -rf src": "destructive_delete", "Remove-Item -Recurse -Force src": "destructive_delete",
                 "git reset --hard HEAD~1": "destructive_delete", "git push origin --delete feat/x": "destructive_delete",
                 "vercel --prod": "deploy", "npx prisma migrate reset": "db_change", "npm install -g foo": "system_permission",
                 "Set-ExecutionPolicy RemoteSigned": "system_permission", "cat .env": "credentials"}
        for cmd, gate in cases.items():
            d, msg = self.bash(cmd)
            self.assertEqual(d, "deny", cmd)
            self.assertIn(gate, msg, cmd)
        self.assertNotIn("■ push", self.bash("git push origin --delete feat/x")[1])
        self.assertEqual(self.tool("Read", self.app / ".env.local")[0], "deny")
        self.assertIn("architecture_policy", self.tool("Write", self.app / "knowledge-base/decisions/0017-x.md")[1])

    def test_git_merge_on_main_is_gated(self):
        git(self.app, "checkout", "-q", "main")
        self.assertEqual(self.bash("git merge feat/x")[0], "deny")

    def test_chained_commands_each_checked(self):
        self.assertEqual(self.bash("python -m unittest && git push origin feat/x")[0], "deny")

    # --- approvals can't be self-served
    def test_approve_needs_human(self):
        self.assertEqual(self.bash("python -m am approve app push")[0], "ask")
        self.assertEqual(self.bash("python -m am approve app push", agent="codex")[0], "deny")

    def test_unmanaged_repo(self):
        self.assertEqual(self.bash("git push", cwd=self.other)[0], "ask")
        self.assertEqual(self.bash("git push", cwd=self.other, agent="codex")[0], "deny")
        self.assertEqual(self.bash("git status", cwd=self.other)[0], "allow")

    def test_worktree_resolves_to_project(self):
        wt = Path(self.tmp.name) / "wt"
        git(self.app, "worktree", "add", "-q", "-b", "feat/y", str(wt))
        self.assertEqual(G.resolve_project(wt, self.root), "app")
        self.approve("push", "feat/y")
        self.assertEqual(self.bash("git push origin feat/y", cwd=wt)[0], "allow")

    # --- hook protocol
    def test_main_exit_codes(self):
        def run(payload, agent="claude"):
            out, err = io.StringIO(), io.StringIO()
            old = sys.stdin
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = G.main([agent])
            finally:
                sys.stdin = old
            return code, out.getvalue(), err.getvalue()
        code, _, err = run({"tool_name": "Bash", "tool_input": {"command": "gh auth token"}, "cwd": str(self.app)})
        self.assertEqual(code, 2)
        self.assertIn("인증정보", err)
        code, out, _ = run({"tool_name": "Bash", "tool_input": {"command": "python -m am approve x spec"}, "cwd": str(self.other)})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        code, out, err = run({"tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "ls"]}, "cwd": str(self.other)}, "codex")
        self.assertEqual((code, out, err), (0, "", ""))


class VerifierFindingsTest(GuardTest):
    """Regression tests for the codex cross-verification report (ctx_ea5039169e86) — each bypass it found."""

    def test_quotes_options_and_wrappers_dont_hide_push(self):
        for cmd in ('"git" "push" origin main', "git -C . push origin main", "git -c x=y push origin main",
                    'bash -c "git push origin main"', "powershell -Command git push origin main", "cmd /c git push origin main",
                    "env A=1 git push origin main", "& git push origin main"):
            d, msg = self.bash(cmd)
            self.assertEqual(d, "deny", cmd)
            self.assertIn("push", msg, cmd)

    def test_git_dash_C_judged_in_target_repo(self):
        d, msg = self.bash(f"git -C {self.other} push origin main")
        self.assertEqual(d, "deny")  # 다른 저장소 변경 = 관련 없는 프로젝트
        self.assertIn("관련 없는 프로젝트", msg)
        self.assertEqual(self.bash(f"git -C {self.other} log --oneline -1")[0], "allow")  # 읽기는 허용

    def test_variables_and_eval_need_a_human(self):
        for cmd in ("$g push origin main", "& $g push origin main", "iex 'git push'", "eval git push", "git $sub origin main"):
            self.assertEqual(self.bash(cmd)[0], "ask", cmd)
            self.assertEqual(self.bash(cmd, agent="codex")[0], "deny", cmd)

    def test_gh_api_mutations_are_gated(self):
        self.assertIn("repo_create", self.bash("gh api user/repos -X POST -f name=x")[1])
        self.assertIn("merge_main", self.bash("gh api repos/me/x/pulls/3/merge -X PUT")[1])
        self.assertIn("destructive_delete", self.bash("gh api repos/me/x -X DELETE")[1])
        self.assertEqual(self.bash("gh api repos/me/x/issues -f title=t")[0], "deny")  # -f implies POST
        self.assertEqual(self.bash("gh api repos/me/x/pulls")[0], "allow")  # GET

    def test_scripts_cant_forge_state_or_self_approve(self):
        for body in ("from pathlib import Path; Path('projects/app/' + 'project' + '.json').write_text('{}')",
                     "from am import state as S; p=S.load('app'); S.approve(p,'push'); S.save(p)"):
            d, msg = self.bash(f'python -c "{body}"')
            self.assertEqual(d, "deny", body)
        heredoc = "python - <<'EOF'\nfrom am import state as S\nEOF"
        self.assertEqual(self.bash(heredoc)[0], "deny")
        self.assertEqual(self.bash('python -c "print(1+1)"')[0], "allow")

    def test_shell_write_targets_and_patches_checked(self):
        self.assertEqual(self.bash(f"Set-Content {self.other / 'main.py'} x")[0], "deny")
        self.assertEqual(self.bash(f"echo x > {self.other / 'main.py'}")[0], "deny")
        self.assertEqual(self.bash(f"cp a.py {self.other / 'a.py'}")[0], "deny")
        self.assertEqual(self.bash("echo x > out.txt")[0], "allow")  # 작업 폴더 안
        patch = f"*** Begin Patch\n*** Update File: {self.other / 'main.py'}\n@@\n-a\n+b\n*** End Patch"
        d = G.decide({"tool_name": "apply_patch", "tool_input": {"patch": patch}, "cwd": str(self.app)}, "codex", self.root)[0]
        self.assertEqual(d, "deny")
        d = G.decide({"tool_name": "shell", "tool_input": {"command": ["apply_patch", patch]}, "cwd": str(self.app)}, "codex", self.root)[0]
        self.assertEqual(d, "deny")
        ok = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-a\n+b\n*** End Patch"
        self.assertEqual(G.decide({"tool_name": "apply_patch", "tool_input": {"patch": ok}, "cwd": str(self.app)}, "codex", self.root)[0], "allow")

    def test_state_file_name_case_insensitive(self):
        self.assertEqual(self.tool("Write", Path(self.root) / "projects/app/PROJECT.JSON", cwd=self.root)[0], "deny")

    def test_every_pushed_ref_needs_approval(self):
        self.approve("push", "feat/x")
        self.assertEqual(self.bash("git push origin feat/x main")[0], "deny")
        self.assertEqual(self.bash("git push origin HEAD:main")[0], "deny")
        self.assertEqual(self.bash("git push --all origin")[0], "deny")  # 대상 불분명 → 전체 승인 필요

    def test_unknown_target_needs_blanket_approval(self):
        self.approve("merge_main", "PR#3")
        self.assertEqual(self.bash("gh pr merge --squash")[0], "deny")
        self.assertFalse(S.has_approval(S.load("app", self.root), "merge_main", None))

    def test_force_push_to_main_in_any_form_forbidden(self):
        self.approve("push")
        for cmd in ("git push --force-with-lease origin main", "git push origin +HEAD:main", "git push -f origin main",
                    "git push --force --all origin"):  # 대상이 불분명한 강제 push
            self.assertIn("금지", self.bash(cmd)[1], cmd)
        self.assertEqual(self.bash("git push --force-with-lease origin feat/x")[0], "allow")  # 내 작업 브랜치는 허용
        git(self.app, "checkout", "-q", "main")
        self.assertIn("금지", self.bash("git push --force origin")[1])  # 지금 브랜치가 main

    def test_delete_checks_full_path_not_basename(self):
        self.assertEqual(self.bash(f"rm -rf {self.other / 'dist'}")[0], "deny")
        self.assertEqual(self.bash("rm -rf dist node_modules")[0], "allow")

    def test_strings_inside_arguments_are_not_commands(self):
        self.assertEqual(self.bash('git commit -m "fix: git push documentation"')[0], "allow")
        self.assertEqual(self.bash('echo "git push origin main"')[0], "allow")
        self.assertEqual(self.bash('git commit -m "a; git push"')[0], "allow")


class TrustLayerStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.p = S.new_project("app", "app", "x", self.root)
        S.add_task(self.p, "API", "codex")

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_and_done_need_verification_and_merge_approval(self):
        with self.assertRaises(S.StateError):
            S.update_task(self.p, "T1", status="review")
        with self.assertRaises(S.StateError):  # 같은 모델이 자기 작업을 검증할 수 없다
            S.verify_task(self.p, "T1", "codex", "pass", "pass", "pass")
        S.verify_task(self.p, "T1", "claude:reviewflow", "pass", "fail", "pass", "높음 1건")
        with self.assertRaises(S.StateError):
            S.update_task(self.p, "T1", status="review")
        S.verify_task(self.p, "T1", "claude:reviewflow", "pass", "pass", "pass", files=["api.py"])
        S.update_task(self.p, "T1", status="review", pr="#7")
        with self.assertRaises(S.StateError):
            S.update_task(self.p, "T1", status="done")
        S.approve(self.p, "merge_main", "PR#7")
        S.update_task(self.p, "T1", status="done")

    def test_done_needs_pr_and_verifier_must_be_real_other_agent(self):
        with self.assertRaises(S.StateError):
            S.verify_task(self.p, "T1", "banana", "pass", "pass", "pass")
        S.verify_task(self.p, "T1", "claude", "pass", "pass", "pass")
        S.approve(self.p, "merge_main")  # 전체 병합 승인이 있어도
        with self.assertRaises(S.StateError):  # PR 없이 done 불가
            S.update_task(self.p, "T1", status="done")

    def test_report_sections(self):
        S.verify_task(self.p, "T1", "claude", "fail", "pass", "pass", "테스트 2개 실패", ["api.py"])
        S.add_request(self.p, "push", "feat/x", "git push origin feat/x", "원격 공개", "되돌리기 어려움")
        out = report.render(self.p)
        for part in ("| T1 API | codex |", "api.py", "## 실패·미해결", "테스트 2개 실패", "## 자동 진행한 작업",
                     "## 사용자 승인이 필요한 작업", "`git push origin feat/x`", "영향 범위: 원격 공개", "위험: 되돌리기 어려움"):
            self.assertIn(part, out)

    def test_install_guard_idempotent(self):
        repo = Path(self.root) / "r"
        f, added = cli.install_guard(repo)
        self.assertTrue(added)
        self.assertFalse(cli.install_guard(repo)[1])
        cfg = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 1)
        self.assertIn("am/guard.py\" claude", cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
