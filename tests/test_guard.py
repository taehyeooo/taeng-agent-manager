"""Execution-time guard: real git repos in a temp dir, payloads shaped like Claude Code / Codex hooks."""
import contextlib
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from am import cli, guard as G, report, state as S  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def fetched_baseline(repo):
    """origin/main as `git fetch origin` leaves it (network origin + fetch reflog entry), without a network."""
    if subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True).returncode:
        git(repo, "remote", "add", "origin", "https://github.com/me/app.git")
    git(repo, "update-ref", "-m", "fetch origin: fast-forward", "refs/remotes/origin/main", "HEAD")


_FIXTURES = {}  # repos built once per test run, copied for each test — starting git is the slow part on Windows


class GuardTest(unittest.TestCase):
    @classmethod
    def build(cls, base):
        """app (feat/x with a fetched origin/main) and other, the way every test starts."""
        app, other = base / "app", base / "other"
        for repo in (app, other):
            repo.mkdir(parents=True)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        git(app, "checkout", "-q", "-b", "feat/x")
        (app / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
        git(app, "add", ".")
        git(app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "baseline")
        fetched_baseline(app)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = str(base / "mgr")
        self.app = base / "app"
        self.other = base / "other"
        key = type(self).build.__qualname__
        if key not in _FIXTURES:
            _FIXTURES[key] = tempfile.TemporaryDirectory()
            type(self).build(Path(_FIXTURES[key].name))
        for name in ("app", "other"):
            shutil.copytree(Path(_FIXTURES[key].name) / name, base / name, symlinks=True)
        # 기존 회귀 검사는 ask 스위치에서도 예전 보호 동작이 유지되는지 확인한다.
        config = Path(self.root) / "config"
        config.mkdir(parents=True)
        (config / "guard.json").write_text('{"opaque": "ask"}', encoding="utf-8")
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

    @unittest.skipUnless(sys.platform == "win32", "Windows 8.3 short names")
    def test_windows_short_path_names(self):
        """GitHub Actions Windows: temp is C:\\Users\\RUNNER~1\\… while git answers with the long name."""
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(self.app), buf, 1024) or buf.value == str(self.app):
            self.skipTest("8.3 short names are off on this volume")
        short = buf.value
        self.assertEqual(G.resolve_project(short, self.root), "app")
        self.assertEqual(self.bash("rm -rf node_modules dist", cwd=short)[0], "allow")

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
        d, msg = self.bash(f"git -C {(self.other).as_posix()} push origin main")
        self.assertEqual(d, "deny")  # 다른 저장소 변경 = 관련 없는 프로젝트
        self.assertIn("관련 없는 프로젝트", msg)
        self.assertEqual(self.bash(f"git -C {(self.other).as_posix()} log --oneline -1")[0], "allow")  # 읽기는 허용

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
        (Path(self.root) / "config/guard.json").unlink()  # 기본 허용 정책에서도 상태 위조는 차단
        for body in ("from pathlib import Path; Path('projects/app/' + 'project' + '.json').write_text('{}')",
                     "from am import state as S; p=S.load('app'); S.approve(p,'push'); S.save(p)"):
            d, msg = self.bash(f'python -c "{body}"')
            self.assertEqual(d, "deny", body)
        heredoc = "python - <<'EOF'\nfrom am import state as S\np = S.load('app'); S.approve(p, 'push'); S.save(p)\nEOF"
        self.assertEqual(self.bash(heredoc)[0], "deny")
        # 읽기만 하는 코드 문자열도 코드 실행 지점이므로 확인(위조가 아니라 차단은 아님)
        self.assertEqual(self.bash('python -c "from am import state; print(state.GATES)"')[0], "ask")
        self.assertEqual(self.bash('python -c "print(1+1)"')[0], "ask")

    def test_shell_write_targets_and_patches_checked(self):
        self.assertEqual(self.bash(f"Set-Content {(self.other / 'main.py').as_posix()} x")[0], "deny")
        self.assertEqual(self.bash(f"echo x > {(self.other / 'main.py').as_posix()}")[0], "deny")
        self.assertEqual(self.bash(f"cp a.py {(self.other / 'a.py').as_posix()}")[0], "deny")
        self.assertEqual(self.bash("echo x > out.txt")[0], "allow")  # 작업 폴더 안
        patch = f"*** Begin Patch\n*** Update File: {(self.other / 'main.py').as_posix()}\n@@\n-a\n+b\n*** End Patch"
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
        self.assertEqual(self.bash(f"rm -rf {(self.other / 'dist').as_posix()}")[0], "deny")
        self.assertEqual(self.bash("rm -rf dist node_modules")[0], "allow")

    def test_strings_inside_arguments_are_not_commands(self):
        self.assertEqual(self.bash('git commit -m "fix: git push documentation"')[0], "allow")
        self.assertEqual(self.bash('echo "git push origin main"')[0], "allow")
        self.assertEqual(self.bash('git commit -m "a; git push"')[0], "allow")


class VerifierRound2Test(GuardTest):
    """Regression tests for the codex re-verification report (ctx_d834afedd938)."""

    def test_substitutions_and_blocks_are_analysed(self):
        self.assertIn("금지", self.bash("echo $(gh auth token)")[1])
        self.assertEqual(self.bash('echo "$(git push origin main)"')[0], "deny")
        self.assertEqual(self.bash("echo `git push origin main`")[0], "deny")
        self.assertEqual(self.bash('powershell -Command "& { git push origin main }"')[0], "deny")
        self.assertEqual(self.bash("cmd /c git pu^sh origin main")[0], "deny")
        self.assertEqual(self.bash("powershell -Command git pu`sh origin main")[0], "ask")  # 짝 없는 ` → 사람 확인
        self.assertEqual(self.bash("powershell -Command git pu`sh origin main", agent="codex")[0], "deny")
        self.assertEqual(self.bash("$(echo git) push origin main")[0], "ask")
        self.assertEqual(self.bash("echo $(git rev-parse --show-toplevel)")[0], "allow")
        self.assertEqual(self.bash("echo $(oops")[0], "ask")  # 닫히지 않은 치환

    def test_python_options_dont_hide_self_approval(self):
        for cmd in ("python -u -m am approve app push", "python -X utf8 -m am approve app push", "python3 -B -m am approve app push"):
            self.assertEqual(self.bash(cmd)[0], "ask", cmd)

    def test_gh_repo_option_and_other_repos(self):
        p = S.load("app", self.root)
        p["repo"] = "me/app"
        S.save(p, self.root)
        self.approve("merge_main", "PR#3")
        self.assertEqual(self.bash("gh pr merge 3 --squash")[0], "allow")
        for cmd in ("gh --repo me/other pr merge 3 --squash", "gh pr merge 3 -R me/unrelated --squash",
                    "gh api repos/me/other/pulls/3/merge -X PUT"):
            d, msg = self.bash(cmd)
            self.assertEqual(d, "deny", cmd)
            self.assertIn("다른 저장소", msg, cmd)

    def test_gh_api_change_approved_by_exact_target(self):
        cmd = "gh api repos/me/app/branches/main/protection -X PUT --input p.json"
        d, msg = self.bash(cmd)
        self.assertEqual(d, "deny")
        self.assertIn("--ref PUT repos/me/app/branches/main/protection", msg.replace('"', ""))
        self.approve("push", "PUT repos/me/app/branches/main/protection")
        self.assertEqual(self.bash(cmd)[0], "allow")
        self.assertEqual(self.bash("gh api repos/me/app/branches/main/protection -X DELETE")[0], "deny")  # 다른 대상
        self.assertEqual(self.bash("git push origin main")[0], "deny")  # 넓은 push 승인이 아님

    def test_git_dash_C_into_a_managed_project_uses_its_approvals(self):
        """매니저가 관리 중인 프로젝트 레포를 git -C로 다룰 때: 금지가 아니라 그 프로젝트의 승인으로 판정."""
        S.new_project("other", "other", "x", self.root)
        cfg = S.load_local(self.root)
        cfg["paths"]["other"] = str(self.other)
        S.save_local(cfg, self.root)
        cmd = f"git -C {self.other.as_posix()} push -u origin main"
        d, msg = self.bash(cmd)
        self.assertEqual(d, "deny")
        self.assertNotIn("관련 없는 프로젝트", msg)  # 금지가 아니라 승인 요청
        self.assertIn("프로젝트 other", msg)
        self.approve("push", "main")  # app 프로젝트의 승인은 other에 쓰이지 않는다
        self.assertEqual(self.bash(cmd)[0], "deny")
        p = S.load("other", self.root)
        S.approve(p, "push", "main")
        S.save(p, self.root)
        self.assertEqual(self.bash(cmd)[0], "allow")

    def test_gh_api_on_another_managed_project_uses_its_approvals(self):
        p = S.load("app", self.root)
        p["repo"] = "me/app"
        S.save(p, self.root)
        svc = S.new_project("svc", "svc", "x", self.root)
        svc["repo"] = "me/svc"
        S.save(svc, self.root)
        cmd = "gh api repos/me/svc/branches/main/protection -X PUT --input p.json"
        d, msg = self.bash(cmd)
        self.assertEqual(d, "deny")
        self.assertIn("프로젝트 svc", msg)  # 금지가 아니라 svc의 승인 요청
        self.approve("push", "PUT repos/me/svc/branches/main/protection")  # app의 승인은 안 통함
        self.assertEqual(self.bash(cmd)[0], "deny")
        svc = S.load("svc", self.root)
        S.approve(svc, "push", "PUT repos/me/svc/branches/main/protection")
        S.save(svc, self.root)
        self.assertEqual(self.bash(cmd)[0], "allow")
        self.assertIn("다른 저장소", self.bash("gh api repos/me/unmanaged/pulls/1/merge -X PUT")[1])  # 관리 밖은 금지

    def test_package_install_approved_per_package(self):
        cmd = "winget install EclipseAdoptium.Temurin.21.JDK"
        d, msg = self.bash(cmd)
        self.assertEqual(d, "deny")
        self.assertIn("winget install eclipseadoptium.temurin.21.jdk", msg)
        self.approve("system_permission", "winget install eclipseadoptium.temurin.21.jdk")
        self.assertEqual(self.bash(cmd)[0], "allow")
        self.assertEqual(self.bash("winget install Some.Other.App")[0], "deny")  # 다른 패키지는 안 됨
        self.assertEqual(self.bash("winget install A.B C.D")[0], "deny")  # 여러 개면 대상 불분명 → 전체 승인 필요
        self.assertEqual(self.bash("Set-ExecutionPolicy RemoteSigned")[0], "deny")

    def test_repo_create_checks_the_project_that_planned_it(self):
        """새 저장소는 로컬에 없으니, 그 이름을 예정한 프로젝트(am set --repo)의 승인을 본다 — 이름 단위."""
        new = S.new_project("newsvc", "newsvc", "x", self.root)
        new["repo"] = "me/newsvc"
        S.save(new, self.root)
        cmd = "gh repo create me/newsvc --private --clone"
        self.assertEqual(self.bash(cmd)[0], "deny")  # 예정은 있지만 승인 전 → 승인 요청
        self.assertIn("newsvc", S.pending_requests(S.load("newsvc", self.root))[0]["ref"])
        p = S.load("newsvc", self.root)
        S.approve(p, "repo_create", "me/newsvc")
        S.save(p, self.root)
        self.assertEqual(self.bash(cmd)[0], "allow")  # 작업 폴더가 다른 프로젝트(app)여도 허용
        self.assertNotEqual(self.bash("gh repo create me/other --private")[0], "allow")  # 다른 이름은 안 됨
        self.assertEqual(self.bash("gh repo create me/other --private", agent="codex")[0], "deny")

    def test_work_branches_standing_approval(self):
        """사용자 승인 2026-11-10: 작업 브랜치 올리기는 묻지 않음 — main은 여전히 막힘."""
        self.approve("push", S.WORK_BRANCHES)
        self.assertEqual(self.bash("git push -u origin feat/x")[0], "allow")
        self.assertEqual(self.bash("git push origin fix/y:fix/y")[0], "allow")
        for cmd in ("git push origin main", "git push origin feat/x:main", "git push origin master",
                    "git push --all origin", "gh api repos/me/app/branches/main/protection -X DELETE",
                    "gh api repos/me/app/hooks -X POST -f url=x"):
            self.assertEqual(self.bash(cmd)[0], "deny", cmd)

    def test_gh_api_attached_option_forms(self):
        for cmd in ("gh api user/repos -XPOST", "gh api user/repos --input=data.json", "gh api user/repos --method=POST"):
            self.assertEqual(self.bash(cmd)[0], "deny", cmd)

    def test_push_repo_option(self):
        self.approve("push", "feat/x")
        self.assertEqual(self.bash("git push --repo=origin main")[0], "deny")
        self.assertEqual(self.bash("git push --repo origin main")[0], "deny")
        self.assertEqual(self.bash("git push --repo=origin feat/x")[0], "allow")

    def test_inline_script_writes_and_env_dumps(self):
        self.assertEqual(self.bash(f"python -c \"from pathlib import Path; Path(r'{(self.other / 'main.py').as_posix()}').write_text('x')\"")[0], "deny")
        self.assertIn("인증정보", self.bash('python -c "import os; print(os.environ)"')[1])
        self.assertIn("인증정보", self.bash("env")[1])
        self.assertEqual(self.bash("env A=1 python -m unittest")[0], "ask")

    def test_long_recursive_flag_and_wildcards(self):
        self.assertEqual(self.bash("rm --recursive src")[0], "deny")
        self.assertEqual(self.bash("Remove-Item src/*")[0], "deny")
        self.assertEqual(self.bash("rm src/old.py")[0], "allow")  # 파일 하나는 git으로 되살릴 수 있다

    def test_package_runners_unwrapped(self):
        for cmd in ("npx vercel --prod", "pnpm dlx vercel --prod", "npm exec -- vercel --prod", "bunx vercel"):
            self.assertIn("deploy", self.bash(cmd)[1], cmd)

    def test_git_aliases(self):
        self.assertEqual(self.bash("git -c alias.publish=push publish origin main")[0], "ask")
        git(self.app, "config", "alias.publish", "push")
        self.assertEqual(self.bash("git publish origin main")[0], "deny")
        git(self.app, "config", "alias.sh", "!sh -c 'git push'")
        self.assertEqual(self.bash("git sh")[0], "ask")

    def test_no_false_positives(self):
        for cmd in ('echo "documentation > C:/other/file.txt"',
                    "npm list -g", 'git commit -m "document --yolo flag"', "git log --oneline -5"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)


class VerifierRound3Test(GuardTest):
    """Regression tests for the codex 3rd verification report (ctx_2f1f23369f8d) + the tripwire."""

    def test_python_attached_options(self):
        d, msg = self.bash("python -mam approve app push")
        self.assertEqual(d, "ask")
        self.assertIn("승인 기록은 사용자", msg)  # 두 번째 그물이 아니라 해석기가 알아봐야 한다
        self.assertEqual(self.bash("python -um am approve app push")[0], "ask")
        self.assertIn("인증정보", self.bash('python -c"import os; print(os.environ)"')[1])

    def test_shell_keywords_and_backslash_escapes(self):
        self.assertEqual(self.bash("bash -c 'if true; then git push origin main; fi'")[0], "deny")
        self.assertEqual(self.bash("bash -c 'g\\it pu\\sh origin main'")[0], "deny")
        self.assertNotEqual(self.bash("g\\it pu\\sh origin main")[0], "allow")  # 최상위: 두 번째 그물

    def test_wrapper_options(self):
        self.assertIn("deploy", self.bash("npx --yes vercel --prod")[1])
        self.assertIn("deploy", self.bash("npx -p vercel vercel --prod")[1])
        self.assertNotEqual(self.bash(f"env --chdir={(self.other).as_posix()} git push origin main")[0], "allow")

    def test_gh_attached_and_url_targets(self):
        p = S.load("app", self.root)
        p["repo"] = "me/app"
        S.save(p, self.root)
        self.assertEqual(self.bash("gh api user/repos -fname=x")[0], "deny")
        self.approve("merge_main", "PR#3")
        self.assertIn("다른 저장소", self.bash("gh pr merge 3 -Rme/other --squash")[1])
        self.approve("merge_main")
        self.assertIn("다른 저장소", self.bash("gh pr merge https://github.com/me/other/pull/3 --squash")[1])
        self.assertEqual(self.bash("gh pr merge https://github.com/me/app/pull/3 --squash")[0], "allow")

    def test_git_option_forms(self):
        self.approve("push")
        self.assertIn("금지", self.bash("git push -fu origin main")[1])
        self.assertIn("관련 없는 프로젝트", self.bash(f"git -C{(self.other).as_posix()} push origin main")[1])
        self.assertIn("관련 없는 프로젝트", self.bash(f"git --git-dir={(self.other / '.git').as_posix()} push origin main")[1])
        self.assertIn("금지", self.bash("git -c core.hooksPath=NUL commit --no-verify -m x")[1])
        self.assertIn("금지", self.bash("git -c core.hooksPath=/dev/null commit -m x")[1])
        self.assertEqual(self.bash("git push -n origin feat/x")[0], "allow")  # push -n = dry-run, 우회 아님
        self.assertIn("금지", self.bash("git commit -nm x")[1])  # commit -n = --no-verify

    def test_fd_redirect_targets(self):
        self.assertEqual(self.bash(f"python -m unittest 2>{(self.other / 'main.py').as_posix()}")[0], "deny")
        self.assertEqual(self.bash("python -m unittest 2>&1")[0], "allow")
        self.assertEqual(self.bash("python -m unittest 2>err.log")[0], "allow")

    def test_state_file_delete_or_move(self):
        for cmd in ("rm projects/app/project.json", "mv projects/app/project.json old.json", "git rm projects/app/project.json",
                    "Remove-Item .am/local.json"):
            self.assertIn("금지", self.bash(cmd, cwd=self.root)[1], cmd)

    def test_no_false_positives_round3(self):
        self.assertEqual(self.bash("echo '$GITHUB_TOKEN'")[0], "allow")  # 작은따옴표 = 글자 그대로
        d = G.decide({"tool_name": "PowerShell", "tool_input": {"command": 'echo "uses `n for newline"'}, "cwd": str(self.app)}, "claude", self.root)
        self.assertEqual(d[0], "allow")
        for cmd in ('git commit -m "fix push bug"', "git log --grep=push", "git merge-base main HEAD", "git merge origin/main",
                    'git commit -m "merge approve flow"', "gh pr review 3 --approve", "python -m unittest discover -s tests"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_tripwire_catches_unparsed_risky_words(self):
        # 해석기가 실행기(perl)는 알지만 그 코드 인수(-E)를 해석하지 못한 경우 → 그물이 잡는다
        self.assertEqual(self.bash("perl -E 'say 1; system \"git push origin main\"'")[0], "ask")
        self.assertEqual(self.bash("perl -E 'say 1; system \"git push origin main\"'", agent="codex")[0], "deny")
        # 이미 알아본 명령의 인수는 데이터 (npm run의 스크립트 내용은 문서에 적은 구조적 한계)
        self.assertEqual(self.bash("npm run push")[0], "deny")
        self.assertEqual(self.bash("node -e \"require('child_process')\" && echo vercel")[0], "ask")


class VerifierRound4Test(GuardTest):
    """Regression tests for the codex 4th verification report (ctx_b142d32ffa40)."""

    def test_harmless_command_does_not_cover_a_later_one(self):
        self.assertEqual(self.bash("gh --version; g\\it pu\\sh origin main")[0], "deny")
        self.assertIn("금지", self.bash("git log --oneline; g\\it comm\\it --no-verify -m x")[1])

    def test_interpreter_option_clusters_with_attached_code(self):
        self.assertIn("인증정보", self.bash('python -uc"import os; print(os.environ)"')[1])
        self.assertIn("금지", self.bash(
            "python -uc\"from pathlib import Path; Path('projects/app/project.json').write_text('{}')\"", cwd=self.root)[1])
        self.assertEqual(self.bash("python -um am approve app push")[0], "ask")

    def test_single_secret_printed(self):
        for body in ("import os; print(os.environ['GITHUB_TOKEN'])", "import os; print(os.getenv('GITHUB_TOKEN'))"):
            self.assertIn("인증정보", self.bash(f'python -c "{body}"')[1], body)
        self.assertIn("인증정보", self.bash("node -e \"console.log(process.env.API_KEY)\"")[1])

    def test_relative_paths_in_inline_scripts(self):
        rel = os.path.relpath(self.other / "main.py", self.app).replace("\\", "/")
        self.assertEqual(self.bash(f"python -c \"from pathlib import Path; Path('{rel}').write_text('x')\"")[0], "deny")
        self.assertEqual(self.bash("python -c \"from pathlib import Path; Path('out/a.txt').write_text('x')\"")[0], "ask")

    def test_gh_api_option_values_skipped(self):
        p = S.load("app", self.root)
        p["repo"] = "me/app"
        S.save(p, self.root)
        self.approve("push")
        d, msg = self.bash("gh api -H Accept:application/vnd.github+json repos/me/other/pulls/3/merge -XPUT")
        self.assertEqual(d, "deny")
        self.assertIn("다른 저장소", msg)

    def test_git_restore_of_state_files(self):
        for cmd in ("git restore projects/app/project.json", "git checkout HEAD~1 -- projects/app/project.json",
                    "git stash push projects/app/project.json"):
            self.assertIn("금지", self.bash(cmd, cwd=self.root)[1], cmd)
        self.assertEqual(self.bash("git add projects/app/project.json", cwd=self.root)[0], "allow")  # am이 쓴 상태를 커밋하는 건 정상

    def test_push_target_repo_compared(self):
        p = S.load("app", self.root)
        p["repo"] = "me/app"
        S.save(p, self.root)
        self.approve("push")
        self.assertIn("다른 저장소", self.bash("git push https://example.invalid/other.git main")[1])
        git(self.app, "remote", "add", "evil", "https://github.com/me/other.git")
        self.assertIn("다른 저장소", self.bash("git push evil feat/x")[1])
        git(self.app, "remote", "set-url", "origin", "https://github.com/me/app.git")
        self.assertEqual(self.bash("git push origin feat/x")[0], "allow")

    def test_redirections_are_not_arguments(self):
        """매니저의 실제 push가 2>&1 때문에 대상 '2>'로 오인돼 막혔던 버그."""
        self.approve("push", "main")
        for cmd in ("git push -u origin feat/x:main 2>&1 | tail -4", "git push origin feat/x:main 2> err.log",
                    "git push origin feat/x:main &> out.log", "git push origin feat/x:main >/dev/null 2>&1"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)
        self.assertEqual(self.bash("git push origin feat/x:other 2>&1")[0], "deny")  # 대상은 여전히 정확히 본다
        self.assertEqual(self.bash("echo a & git push origin feat/x:other")[0], "deny")  # 진짜 & 구분자

    def test_normal_dev_commands_not_blocked(self):
        """The verifier's sample of 11 normal commands the second net used to block."""
        for cmd in ("git log --grep push", "git log --grep merge", "git commit -m push", "git grep push", "rg push src",
                    "npm test -- --grep merge", "echo push"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)
        # 4차(기본 ask): 허용 목록 밖 실행기는 위험 단어가 없어도 사람이 본다 — 테스트 코드도 쓰기 도구로 바뀔 수 있다
        for cmd in ("pytest -k push", "python -m pytest -k merge", "node -e \"console.log('push')\""):
            self.assertEqual(self.bash(cmd)[0], "ask", cmd)
        # 매니저가 실제로 막혔던 오탐: 다른 폴더의 설정을 읽기만 하는 스크립트 (json.dumps는 쓰기가 아님)
        reader = ("python -c \"import json; d=json.load(open(r'C:/x/orca-data.json', encoding='utf-8')); "
                  "print(json.dumps(d)[:10])\"")
        self.assertEqual(self.bash(reader)[0], "ask")
        self.assertEqual(self.bash("python -c \"print(open('w.txt').read())\"")[0], "ask")
        self.assertEqual(self.bash(f"python -c \"open('{(self.other / 'w.txt').as_posix()}', 'w').write('x')\"")[0], "deny")
        # 매니저가 실제로 막혔던 오탐: 정규식 문자열이 들어간 heredoc 스크립트
        # (차단되지 않고 확인까지만 — 파일 인수 없는 인터프리터 실행은 내용과 상관없이 확인)
        heredoc = "python - <<'EOF'\nimport re\nopen('out.txt', 'w').write(re.sub(r\"[/\\\\]\", \"-\", 'a/b') + '(G, \"x\")')\nEOF"
        self.assertEqual(self.bash(heredoc)[0], "ask")


class BuildAllowlistTest(GuardTest):
    def test_t5_readonly_combinations(self):
        folder = self.app / 'backend/src/main/resources/db/migration'
        folder.mkdir(parents=True)
        results = self.app / 'backend/build/test-results/test'
        results.mkdir(parents=True)
        absolute = results.as_posix()
        if sys.platform == 'win32':
            absolute = '/' + absolute[0].lower() + absolute[2:]
        for cmd in (
            f'cd {absolute} && grep -l "<skipped" *.xml',
            'ls backend/src/main/resources/db/migration/ 2>/dev/null || find backend/src/main/resources -name "*.sql"; grep -rhoiE "create table .*" backend/src/main/resources | sort | uniq',
            'cd backend/src/main/resources/db/migration; cat V1__init.sql V3__x.sql; grep -n "" V2__y.sql | head -60',
            'git status; git diff | head -60',
        ):
            for agent in ('claude', 'codex'):
                with self.subTest(cmd=cmd, agent=agent):
                    self.assertEqual(self.bash(cmd, agent=agent)[0], 'allow')

    def test_t5_unsafe_combinations(self):
        for cmd in ('cat README.md > x; head x', 'cat > x',
                    'ls; sort -o x README.md', 'ls; uniq README.md x',
                    'ls; find . -exec cat {} +', 'ls; find . -fprint x',
                    'cd ..; cat README.md', 'ls; find . -delete',
                    'cat README.md; git push origin main'):
            with self.subTest(cmd=cmd):
                self.assertNotEqual(self.bash(cmd)[0], 'allow')

    def test_t5_build_output_options(self):
        backend = self.app / 'backend'
        backend.mkdir()
        for name in ('gradlew', 'gradlew.bat', 'package.json'):
            shutil.copyfile(self.app / name, backend / name)
        git(self.app, 'add', '.')
        git(self.app, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'backend fixture')
        fetched_baseline(self.app)
        for option in ('--console=plain', '--console=rich', '--console=auto', '--info', '--quiet', '-q',
                       '--stacktrace', '--warning-mode=all', '--tests "example.*"', '--rerun', '--no-daemon', '--offline'):
            cmd = 'cd backend; ./gradlew.bat test ' + option
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd)[0], 'allow')
        if sys.platform == 'win32':
            self.assertEqual(G.decide({'tool_name': 'powershell', 'cwd': str(self.app),
                                      'tool_input': {'command': r'cd backend; .\gradlew.bat test --console=plain'}},
                                     'codex', self.root)[0], 'allow')
        self.assertEqual(self.bash('cd backend; npm test -- src/unit.test.ts -t "some test"')[0], 'allow')
        for option in ('--console=evil', '--init-script x', '--tests', '--tests --init-script x'):
            self.assertNotEqual(self.bash('cd backend; ./gradlew test ' + option)[0], 'allow')
        (backend / 'gradlew').write_text('# changed\n', encoding='utf-8')
        self.assertNotEqual(self.bash('cd backend; ./gradlew test --quiet')[0], 'allow')

    @classmethod
    def build(cls, base):
        GuardTest.build(base)
        app = base / "app"
        (app / "scripts").mkdir()
        for name in ("check.py", "check.sh"):
            (app / "scripts" / name).write_text("# test\n", encoding="utf-8")
        (app / "gradlew").write_text("# baseline wrapper\n", encoding="utf-8")
        (app / "gradlew.bat").write_text("@rem baseline wrapper\n", encoding="utf-8")
        (app / "package.json").write_text(json.dumps({"scripts": {
            "test": "vitest run", "build": "vite build", "lint": "eslint ."}}), encoding="utf-8")
        git(app, "add", ".")
        git(app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fixtures")
        fetched_baseline(app)

    def test_t4_git_execution_options(self):
        for cmd in (
            'git ls-remote --upload-pack="sh -c true" .',
            'git ls-remote --upload-pack "sh -c true" .',
            'git ls-remote --exec=evil .', 'git ls-remote --upload=evil .',
            'git diff --ext-diff', 'git show --textconv HEAD',
            'git -c diff.external=evil diff', 'git --config-env=core.pager=X log',
            'git --unknown status', 'git log --unknown',
            'git grep -Oevil pattern', 'git archive --exec=evil HEAD',
        ):
            with self.subTest(cmd=cmd):
                self.assertNotEqual(self.bash(cmd)[0], "allow")

    def test_t4_output_paths(self):
        target = 'projects/app/project.json'
        for cmd in (
            f'uniq payload.json {target}', f'uniq -- payload.json "{target}"',
            f'sort -no{target} payload.json', f'sort -n -o "{target}" payload.json',
            f'sort --output={target} payload.json', f'sort --output {target} payload.json',
            f'git diff --no-index --output={target} README.md AGENTS.md',
            f'git log --output={target}', f'git show --output "{target}"',
            f'git format-patch --stdout --output={target} HEAD',
            f'git archive HEAD --output={target}', f'git archive -o{target} HEAD',
            f'git config --file={target} user.name forged',
            f'git config -f{target} user.name forged', f'tree -o {target}',
            'sort -noPROJECTS/app/PROJECT.JSON payload.json',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd)[0], "deny")

    def test_t4_hooks_and_unknown_outputs(self):
        for cmd in ('uniq hook.txt .git/hooks/pre-commit && git commit -m test',
                    'git show HEAD:package.json --output=.git/hooks/pre-commit && git commit -m test',
                    'sort -no.git/hooks/pre-commit hook.txt',
                    'git format-patch -o.git/hooks HEAD', 'tree -o.git/hooks/pre-commit',
                    'sort --compress-program=evil input', 'sort --output',
                    'git log --output', 'echo x | xargs sort', 'find . -exec uniq {} \\;'):
            with self.subTest(cmd=cmd):
                self.assertNotEqual(self.bash(cmd)[0], "allow")
        self.assertEqual(self.tool('Write', self.app / '.git/hooks/pre-commit')[0], 'ask')

    def test_t4_secret_expansions(self):
        for cmd in ('cmd /c echo %API_KEY%', 'CMD /C echo "%api_key%"',
                    "cmd /c echo '%API_KEY%'", 'cmd /v:on /c echo !API_KEY!',
                    'cmd /c echo %API_KEY:~0,4%', 'cmd /c set API',
                    'echo $env:API_KEY', 'Write-Output "$env:api_key"',
                    'Get-ChildItem env:', 'gci -Path Env:API_KEY',
                    'Get-Content Env:api_key', 'Get-ChildItem -Path Env:*'):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd)[0], 'deny')
        for cmd in ('cmd /c echo %1', 'cmd /v:on /c echo !123!', 'cmd /c %PROGRAM%'):
            self.assertNotEqual(self.bash(cmd)[0], 'allow', cmd)

    def test_t4_sibling_message(self):
        (self.app / 'scripts/json.py').write_text('# changed\n', encoding='utf-8')
        decision, message = self.bash('python scripts/check.py')
        self.assertEqual(decision, 'ask')
        self.assertIn('scripts/json.py', message)
        self.assertIn('같은 폴더', message)

    def test_t4_normal_commands(self):
        for cmd in ('git status --short --branch', 'git log -3 --oneline',
                    'git diff --stat plan/backend-cs-tutor...HEAD',
                    'git ls-remote --heads origin', 'git show --no-ext-diff --no-textconv HEAD',
                    'sort -n input', 'uniq -c input', 'tree -L 2',
                    'cmd /c echo hello', "echo '$API_KEY'", 'Get-ChildItem env:PATH',
                    'python -m unittest discover -s tests', 'python scripts/check.py',
                    'npm test', 'gradlew test'):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd)[0], 'allow')

    def test_review_heredoc_interpreter_variants(self):
        for interpreter in ("node", "nodejs", "python3.12 -", "python3 -", "py -3", "pypy3 -"):
            body = "require('child_process').execSync('git push --force origin main')" if interpreter.startswith("node") else "import subprocess\nsubprocess.run(['git', 'push', '--force', 'origin', 'main'])"
            self.assertNotEqual(self.bash(interpreter + " <<'EOF'\n" + body + "\nEOF")[0], "allow", interpreter)
        self.assertEqual(self.bash("unknown-runtime <<'EOF'\nanything\nEOF")[0], "ask")

    def test_review_python_writes_and_flow(self):
        bodies = [
            "import shutil\nshutil.%s('source', 'projects/app/project.json')" % method
            for method in ("copyfile", "copy", "move")]
        bodies += ["import os\nos.%s('source', 'projects/app/project.json')" % method for method in ("replace", "rename")]
        bodies += ["open('projects/app/project.json', '%s')" % mode for mode in ("a", "w", "x", "r+")]
        bodies += ["p='projects/app/'\np+='project.json'\nopen(p,'w')",
                   "from pathlib import Path\np='ok.txt'\nfor i in range(2):\n Path(p).write_text('x')\n p='projects/app/project.json'",
                   "from pathlib import Path\nPath('projects/app/project.json').touch()",
                   "import builtins\nbuiltins.open('projects/app/project.json','a')",
                   "o=open\no('projects/app/project.json','w')"]
        for body in bodies:
            self.assertNotEqual(self.bash("python - <<'EOF'\n" + body + "\nEOF", cwd=self.root)[0], "allow", body)

    def test_review_dynamic_imports(self):
        for body in ("import importlib\nS=importlib.import_module('am.state')\nS.save(S.load('app'))",
                     "S=__import__('am.state')", "import runpy\nrunpy.run_module('am.cli')",
                     "exec('from am import state')", "eval('__import__(\"am.state\")')"):
            self.assertNotEqual(self.bash("python - <<'EOF'\n" + body + "\nEOF")[0], "allow", body)

    def test_review_heredoc_exact_terminator(self):
        for ending in ("EOF ", "EOF\t"):
            command = "python - <<'EOF'\nprint('ok')\n" + ending + "\nopen('projects/app/project.json','w')\nEOF"
            self.assertNotEqual(self.bash(command, cwd=self.root)[0], "allow")
            # 뒤 줄이 모두 아는 명령이어도: 진짜 본문은 Python으로 해석되지 않는 줄까지 포함한다
            command = "python - <<'EOF'\nprint('ok')\n" + ending + "\necho x\ncat <<'EOF'\nEOF"
            self.assertNotEqual(self.bash(command)[0], "allow")

    def test_review_committed_script_and_index_flags(self):
        for launcher, name in (("python", "scripts/check.py"), ("sh", "scripts/check.sh")):
            for flag in ("--assume-unchanged", "--skip-worktree"):
                git(self.app, "update-index", flag, name)
                self.assertEqual(self.bash(f"{launcher} {name}")[0], "ask", flag)
                git(self.app, "update-index", "--no-" + flag[2:], name)
        for launcher, name in (("python", "scripts/check.py"), ("sh", "scripts/check.sh")):
            (self.app / name).write_text("# changed and committed\n", encoding="utf-8")
        git(self.app, "add", ".")
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "changed")
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask")
        self.assertEqual(self.bash("sh scripts/check.sh")[0], "ask")

    def test_review_changed_build_inputs(self):
        for name, command in (("gradlew", "gradlew test"), ("gradle/wrapper/gradle-wrapper.jar", "gradlew test"),
                              ("build.gradle", "gradlew test"), ("build.gradle.kts", "gradlew test"),
                              ("settings.gradle.kts", "gradlew test"), ("init.d/setup.gradle", "gradlew test"),
                              ("package.json", "npm test"), ("package-lock.json", "npm ci"), (".npmrc", "npm test")):
            path = self.app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_bytes() if path.exists() else None
            path.write_text("{}", encoding="utf-8")
            self.assertNotEqual(self.bash(command)[0], "allow", name)
            if old is None:
                path.unlink()
            else:
                path.write_bytes(old)

    def test_review_environment_prefixes(self):
        for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "GRADLE_OPTS", "GRADLE_USER_HOME", "NODE_OPTIONS",
                     "PYTHONPATH", "PYTHONSTARTUP", "npm_config_script_shell", "LD_PRELOAD"):
            for prefix in ("", "env "):
                self.assertEqual(self.bash(prefix + name + "=x npm test")[0], "ask", name)

    def test_review_build_config_commits_flags_and_dependencies(self):
        installed = self.app / "node_modules/example/package.json"
        installed.parent.mkdir(parents=True)
        installed.write_text("{}", encoding="utf-8")
        self.assertEqual(self.bash("npm test")[0], "allow")
        for flag in ("--assume-unchanged", "--skip-worktree"):
            git(self.app, "update-index", flag, "package.json")
            self.assertEqual(self.bash("npm test")[0], "ask")
            git(self.app, "update-index", "--no-" + flag[2:], "package.json")
        for name, command in (("package.json", "npm test"), ("gradlew", "gradlew test")):
            path = self.app / name
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            git(self.app, "add", name)
            git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "changed config")
            self.assertEqual(self.bash(command)[0], "ask", name)

    def test_review_npm_deploy_scripts(self):
        for launcher in ("run", "run-script"):
            for name in ("deploy", "release", "publish", "push"):
                findings = G.classify_command("npm " + launcher + " " + name, self.app, self.root)
                self.assertTrue(any(f.get("gate") == "deploy" for f in findings), name)
        (self.app / "package.json").write_text(json.dumps({"scripts": {"test": "git push origin main"}}), encoding="utf-8")
        findings = G.classify_command("npm test", self.app, self.root)
        self.assertTrue(any(f.get("gate") == "push" for f in findings))

    # --- 2차 재검증(t3-reverify) 우회 입력: 지금 allow였던 것 → ask/deny
    def _not_allowed(self, cmd, cwd=None, expect=None):
        d = self.bash(cmd, cwd=cwd)[0]
        self.assertNotEqual(d, "allow", cmd)
        if expect:
            self.assertEqual(d, expect, cmd)
        self.assertEqual(self.bash(cmd, cwd=cwd, agent="codex")[0], "deny", cmd)

    def test_reverify_a_heredoc_head_is_split_like_any_line(self):
        for sep in (";", "&&", "||", "|", "&"):
            self._not_allowed(f"python - {sep} git push --force origin main <<'EOF'\nprint(1)\nEOF", expect="deny")
        self._not_allowed("node - ; git push --force origin main <<'EOF'\nconsole.log(1)\nEOF", expect="deny")
        (self.app / "evil.py").write_text("print(1)\n", encoding="utf-8")
        for cmd in ("python evil.py <<'EOF'\nx\nEOF", "python scripts/check.py <<'EOF'\nx\nEOF",
                    "python -c 'print(1)' <<'EOF'\nx\nEOF", "node evil.js <<'EOF'\nx\nEOF",
                    "python - <<'EOF' | sh\nprint('git status')\nEOF", "X=1 python - <<'EOF'\nprint(1)\nEOF"):
            self._not_allowed(cmd)
        # 인터프리터는 추적·미수정 스크립트 파일 실행만 기존 판정 — 파일 인수 없는 heredoc 프로그램은 확인
        self.assertEqual(self.bash("python - <<'EOF'\nprint(1)\nEOF\npython - <<'EOF'\nprint(2)\nEOF")[0], "ask")

    def test_reverify_b_baseline_ref_commands_need_a_person(self):
        for cmd in ("git update-ref refs/remotes/origin/main HEAD",
                    "git update-ref -m 'fetch origin: fast-forward' refs/remotes/origin/main HEAD",
                    "git fetch . HEAD:refs/remotes/origin/main", "git fetch origin +HEAD:refs/remotes/origin/main",
                    "git fetch ../other main", "git pull https://example.invalid/x.git main",
                    "git replace origin/main HEAD", "git symbolic-ref refs/remotes/origin/main refs/heads/feat/x",
                    "git remote set-url origin ../other", "git remote add origin2 ../other",
                    "git config remote.origin.url ../other", "git config --add remote.origin.fetch +refs/heads/*:refs/remotes/origin/*",
                    "git config url.../other.insteadOf https://github.com/me/app.git",
                    "git -c url.../other.insteadOf=https://github.com/me/app.git fetch origin",
                    "git push . HEAD:refs/remotes/origin/main", "echo 0 > .git/refs/remotes/origin/main"):
            self._not_allowed(cmd)
        self.assertNotEqual(self.tool("Write", self.app / ".git/refs/remotes/origin/main")[0], "allow")
        for cmd in ("git fetch origin", "git fetch origin main", "git pull origin main", "git remote -v",
                    "git config --get remote.origin.url", "git replace -l", "git config user.name t"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_reverify_b_forged_baseline_is_not_trusted(self):
        script = self.app / "scripts/check.py"
        script.write_text("import subprocess  # changed\n", encoding="utf-8")
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "changed")
        base = subprocess.run(["git", "-C", str(self.app), "rev-parse", "refs/remotes/origin/main"],
                              capture_output=True, text=True, check=True).stdout.strip()
        git(self.app, "replace", base, "HEAD")  # git show/diff follow replace objects unless told not to
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask", "replace")
        git(self.app, "replace", "-d", base)
        git(self.app, "update-ref", "refs/remotes/origin/main", "HEAD")  # no fetch reflog entry
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask", "update-ref")
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "merged")
        fetched_baseline(self.app)  # the remote main really moved on and was fetched
        self.assertEqual(self.bash("python scripts/check.py")[0], "allow")
        git(self.app, "remote", "set-url", "origin", str(self.other))
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask", "local origin")
        git(self.app, "remote", "set-url", "origin", "https://github.com/me/app.git")
        git(self.app, "config", "url." + str(self.other) + ".insteadOf", "https://github.com/me/app.git")
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask", "insteadOf")

    def test_reverify_b_hand_written_ref_file(self):
        git(self.app, "pack-refs", "--all")
        loose = self.app / ".git/refs/remotes/origin/main"
        loose.parent.mkdir(parents=True, exist_ok=True)
        (self.app / "scripts/check.py").write_text("# changed\n", encoding="utf-8")
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "changed")
        head = subprocess.run(["git", "-C", str(self.app), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        loose.write_text(head + "\n", encoding="utf-8")  # reflog still says the old fetched commit
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask")

    def test_reverify_c_environment_changed_on_the_same_line(self):
        for cmd in ('export NODE_OPTIONS="--require ./x.js"; npm test', "export JAVA_TOOL_OPTIONS=-javaagent:x.jar; ./gradlew test",
                    "declare -x NODE_OPTIONS=x; npm test", "typeset -x NODE_OPTIONS=x; npm test", "set -a; NODE_OPTIONS=x; npm test",
                    "set -o allexport; npm test", "readonly X=1; npm test", "unset HOME; npm test", ". ./env.sh && npm test",
                    "source ./env.sh && npm test", "set NODE_OPTIONS=x && npm test", "export PYTHONPATH=x && python -m unittest",
                    "export GIT_DIR=../other/.git; git status", "export X=1\nnpm test", "if export X=1; then npm test; fi"):
            self._not_allowed(cmd)
        for cmd in ("export", "export -p", "declare -x"):
            self.assertEqual(self.bash(cmd)[0], "deny", cmd)  # prints every exported secret
        for cmd in ("set -e; npm test", "set -euo pipefail; npm test", "set -x && npm test"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_reverify_d_cd_variants(self):
        for cd in ("if cd ../other; then echo x > a.txt; fi", "command cd ../other && echo x > a.txt",
                   "builtin cd ../other; echo x > a.txt", "time cd ../other; echo x > a.txt", "! cd ../other; echo x > a.txt",
                   "(cd ../other && echo x > a.txt)", "{ cd ../other; echo x > a.txt; }", "pushd ../other; echo x > a.txt",
                   "chdir ../other && echo x > a.txt", "cd ../other\npython - <<'EOF'\nopen('a.txt','w')\nEOF"):
            self._not_allowed(cd)
        self.assertEqual(self.bash("cd scripts && python check.py")[0], "allow")

    def test_reverify_e_package_runners_and_npm_options(self):
        for cmd in ("pnpx evil", "bunx evil", "pnpm dlx evil", "yarn dlx evil", "bun x evil", "npm x evil",
                    "npx cat package.json", "pnpm dlx echo hi", "npm exec -- ls",  # 패키지 이름이 아는 명령과 같아도
                    "pnpm test", "yarn test", "bun run test", "npm --prefix scripts test", "npm -w x test"):
            self._not_allowed(cmd)
        for cmd in ("npm --prefix x run deploy", "npm -w x run deploy", "npm rum deploy", "npm urn release",
                    "npm run --silent deploy", "npm publish"):
            findings = G.classify_command(cmd, self.app, self.root)
            self.assertTrue(any(f.get("gate") == "deploy" for f in findings), cmd)

    def test_reverify_f_document_heredocs_and_read_only_npm(self):
        for cmd in ("cat > notes.md <<'EOF'\n# notes\ngit push --force origin main is how not to do it\nEOF",
                    "cat <<'EOF' > notes.md\n- a\n- b\nEOF", "tee notes.md <<'EOF'\nx\nEOF",
                    "git commit -F - <<'EOF'\nfix: x\n\nbody (with parens)\nEOF",
                    "git commit -m \"$(cat <<'EOF'\nfix: x\n\nbody\nEOF\n)\"",
                    "npm ls", "npm list -g", "npm view vitest", "npm audit", "npm -v", "npm --version", "npm install",
                    "npm ci && npm test"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)
        for cmd in ("cat > notes.md <<EOF\n$(git status)\nEOF", "bash <<'EOF'\necho hi\nEOF", "sh <<'EOF'\ngit push --force origin main\nEOF",
                    "npm i lodash", "npm install lodash", "npm audit fix", "npm version patch", "cat <<-EOF\nx\nEOF"):
            self.assertNotEqual(self.bash(cmd)[0], "allow", cmd)
        self.assertEqual(self.bash("sh <<'EOF'\ngit push --force origin main\nEOF")[0], "deny")  # shell body is still read

    def test_reverify_f_trusted_npm_script_env_and_ignored_inputs(self):
        (self.app / "package.json").write_text(json.dumps({"scripts": {"test": "NODE_ENV=test vitest run"}}), encoding="utf-8")
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "env in script")
        fetched_baseline(self.app)
        self.assertEqual(self.bash("npm test")[0], "allow")  # the prefix is part of the baseline package.json
        (self.app / ".gitignore").write_text("node_modules/\n.npmrc\n", encoding="utf-8")
        (self.app / "node_modules/x").mkdir(parents=True)
        (self.app / "node_modules/x/package.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.bash("npm test")[0], "allow")
        (self.app / ".npmrc").write_text("script-shell=./evil.sh\n", encoding="utf-8")  # ignored but read by npm
        self.assertEqual(self.bash("npm test")[0], "ask")

    def test_reverify_g_h_unittest_paths_interpreter_names_and_shadowing(self):
        for cmd in (f'python -m unittest discover -s "{self.other.as_posix()}" -t /', "python -m unittest discover -s ../other",
                    "python -m unittest discover -t ..", "python -m unittest ../other/test_x.py",
                    "pythonw -c \"import subprocess; subprocess.run(['git', 'push', '--force', 'origin', 'main'])\"",
                    "python3-dbg -c \"import subprocess\"", "pythonw evil.py"):
            self._not_allowed(cmd)
        for cmd in ("python -m unittest discover -s tests", "python -m unittest discover -s tests -p 'test_*.py' -v",
                    "python -m unittest tests.test_guard", "python -m unittest"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)
        (self.app / "scripts/json.py").write_text("print('shadow')\n", encoding="utf-8")  # imported before the stdlib
        self.assertEqual(self.bash("python scripts/check.py")[0], "ask")

    def test_build_invocations(self):
        for cmd in ("gradlew test", "gradlew.bat build --no-daemon", "./gradlew check --rerun",
                    "./gradlew compileJava -p scripts", "npm ci", "npm test", "npm run build",
                    "npm run test", "npm run lint", "npx vitest run", "python scripts/check.py",
                    "sh scripts/check.sh", "python -m unittest discover -s tests",
                    "sh scripts/check.sh 2>&1 | tail -4", "gradlew test 2>&1 | grep PASS",
                    "gradlew.bat test --rerun --no-daemon", "npm ci && npm test", "python -m unittest discover -s tests"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_script_trust_boundaries(self):
        for suffix, launcher in (("py", "python"), ("sh", "sh")):
            script = self.app / "scripts" / ("check." + suffix)
            script.write_text("# modified\n", encoding="utf-8")
            self.assertEqual(self.bash(f"{launcher} scripts/check.{suffix}")[0], "ask")
            git(self.app, "add", str(script))
            self.assertEqual(self.bash(f"{launcher} scripts/check.{suffix}")[0], "ask")
            fresh = self.app / ("fresh." + suffix)
            fresh.write_text("# new\n", encoding="utf-8")
            self.assertEqual(self.bash(f"{launcher} fresh.{suffix}")[0], "ask")
            outside = self.other / ("check." + suffix)
            outside.write_text("# outside\n", encoding="utf-8")
            git(self.other, "add", ".")
            git(self.other, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fixture")
            self.assertEqual(self.bash(f'{launcher} "{outside.as_posix()}"')[0], "ask")

    @unittest.skipUnless(sys.platform == "win32", "Windows 8.3 short names")
    def test_windows_short_script_paths(self):
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(self.app), buf, 1024):
            self.skipTest("8.3 short names unavailable")
        for cmd in ("python scripts/check.py", "sh scripts/check.sh"):
            self.assertEqual(self.bash(cmd, cwd=buf.value)[0], "allow", cmd)

    def test_unsafe_build_invocations(self):
        for cmd in ("gradlew publish", "gradlew run", "gradlew test --init-script x.gradle",
                    "gradlew test -I x.gradle", "gradlew test -Duser.home=elsewhere",
                    "gradlew test -p ../other", "../other/gradlew test", "npm run missing",
                    "npm exec -- vitest run", "npx --yes vitest run",
                    "python -c \"print('hello')\"", "python fresh.py", "sh -x fresh.sh",
                    "env npx --yes vitest run", "env npm exec -- vitest run", "cd ../other; python scripts/check.py"):
            self.assertEqual(self.bash(cmd)[0], "ask", cmd)
            self.assertEqual(self.bash(cmd, agent="codex")[0], "deny", cmd)
        (self.app / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
        self.assertEqual(self.bash("npm test")[0], "ask")

    def test_policy_precedes_build_allowlist(self):
        for cmd in ("sh scripts/check.sh --yolo", "npx --yolo vitest run", "gradlew test --dangerously-skip-permissions",
                    "python scripts/check.py --yolo", "npm test; gh auth token",
                    "gradlew test; git push origin main",
                    f'sh scripts/check.sh > "{(self.other / "out.txt").as_posix()}"'):
            self.assertEqual(self.bash(cmd)[0], "deny", cmd)

    def test_heredoc_false_positives(self):
        bodies = (
            "from pathlib import Path\nPath('brief.md').write_text('projects/ and write_text and from am import state are documentation')",
            "from pathlib import Path\nPath('scripts/../brief.md').write_text('ok')",
            "values = set(['a', 'b'])\nopen('brief.md', 'w').write(str(values))",
            "import io\np = 'brief.md'\ntext = 'project and .am/local.json'\nio.open(p, 'w').write(text.replace('project', 'task'))",
        )
        for body in bodies:
            # 문서 글자로 차단(deny)하지 않는다. 파일 인수 없는 인터프리터 실행이므로 확인(ask)까지는 간다.
            self.assertEqual(self.bash("python - <<'EOF'\n" + body + "\nEOF")[0], "ask", body)

    def test_heredoc_still_checks_actual_writes_and_commands(self):
        for body in ("open('projects/app/project.json', 'w').write('{}')",
                     "import io\nio.open('.am/local.json', 'w').write('{}')",
                     "from pathlib import Path\np = Path('projects/app/project.json')\np.replace('old.json')",
                     "from pathlib import Path\np = Path('projects/app/project.json')\np.write_text('{}')"):
            self.assertEqual(self.bash("python - <<'EOF'\n" + body + "\nEOF", cwd=self.root)[0], "deny", body)
        self.assertEqual(self.bash("python - <<'EOF'\nprint('ok')\nEOF\ngit push origin main")[0], "deny")
        self.assertEqual(self.bash("python - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'push'])\nEOF")[0], "ask")

    # --- 4차 교차 검증: 남은 allow 경로 → 기본 ask
    def test_r4_unlisted_programs_modules_and_scripts_ask(self):
        """1·6: Write로 만든 파일을 실행하는 2단계 경로, 허용 목록 밖 실행기."""
        (self.app / "x.sh").write_text("git push --force origin main\n", encoding="utf-8")
        (self.app / "x.js").write_text("require('child_process').execSync('git push --force origin main')\n", encoding="utf-8")
        (self.app / "pkg").mkdir()
        (self.app / "pkg/__main__.py").write_text("import subprocess\n", encoding="utf-8")
        for cmd in ("./x.sh", "x.sh", "bash x.sh", "python -m pkg", "python3 -m pkg", "node x.js", "deno run x.js",
                    "ruby x.rb", "pytest", "make test", "cargo test", "go test ./...", "uv run python -c \"import os; print(os.environ)\"",
                    "uv run pkg", "pipx run pkg", "poetry run pytest", "pdm run pkg", "hatch run pkg", "conda run python x.py",
                    "cat x.sh | sh", "cat x.sh | bash", "git lfs pull", "gh extension exec x", "gh alias set x y",
                    "python -m pip install x", "python -m am status"):
            self._not_allowed(cmd)
        # 추적 안 된 .py(pkg/__main__.py)가 있으면 원격 main과 같은 스크립트도 import로 바뀐 코드를 돌릴 수 있어 확인
        self._not_allowed("python scripts/check.py")
        self._not_allowed("sh scripts/check.sh")  # 추적 안 된 x.sh — 셸도 같은 언어 소스 규칙
        shutil.rmtree(self.app / "pkg")
        (self.app / "x.sh").unlink()
        for cmd in ("npm test", "npm run build", "gradlew test", "python scripts/check.py", "sh scripts/check.sh",
                    "python -m unittest discover -s tests", "ls -la", "cat package.json | grep test", "git status",
                    "gh pr view 3", "orca orchestration check", "mkdir -p out && echo x > out/a.txt"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_r4_wrappers_are_analysed_not_trusted(self):
        """2: 래퍼 안의 명령을 재귀 분석. 일반 cmd 태그만으로 두 번째 그물을 건너뛰지 않는다."""
        self.approve("push")
        for cmd in ("timeout 60 git push --force origin main", "timeout -s KILL 60 git push --force origin main",
                    "nice git push --force origin main", "nice -n 5 git push --force origin main",
                    "nohup git push --force origin main", "stdbuf -oL git push --force origin main",
                    "setsid git push --force origin main", "command git push --force origin main",
                    "exec git push --force origin main", "watch -n 1 git push --force origin main",
                    "find . -exec git push --force origin main ;", "timeout 60 gh auth token", "nice printenv",
                    "timeout 60 env"):
            self.assertEqual(self.bash(cmd)[0], "deny", cmd)
            self.assertIn("금지", self.bash(cmd)[1], cmd)
        self.assertIn("deploy", self.bash("timeout 60 vercel --prod")[1])
        self.assertIn("destructive_delete", self.bash("find . -name '*.log' -delete")[1])
        for cmd in ("echo main | xargs git push origin", "git ls-files | xargs rm", "find . -name x -exec rm {} +",
                    "find . -exec sh -c 'echo {}' ;", "timeout 60 ./x.sh", "nice node x.js", "chronic make",
                    "ionice -c 3 python -m pkg", "git status | xargs -I{} sh -c 'echo {}'"):
            self._not_allowed(cmd)
        for cmd in ("timeout 60 npm test", "find . -name '*.py'", "find . -name '*.py' -exec grep -l push {} +",
                    "git ls-files | xargs grep -n merge", "command -v git", "nice -n 5 git status"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_r4_escaped_quote_does_not_hide_a_command(self):
        """3: \\\" 를 미리 풀면 따옴표가 시작돼 뒤 명령이 숨는다 — 인용·escape 상태를 같이 본다(코드값 92·34·59)."""
        bs, dq, semi, bt, caret = chr(92), chr(34), chr(59), chr(96), chr(94)
        cmd = "echo " + bs + dq + semi + " git push --force origin main"
        self.assertEqual([ord(c) for c in cmd[5:8]], [92, 34, 59])
        self.assertIn("금지", self.bash(cmd)[1])
        self.assertIn("금지", self.bash("echo " + bs + "'" + semi + " git push --force origin main")[1])
        self.assertIn("금지", self.bash("bash -c " + dq + "echo " + bs + bs + bs + dq + semi + " git push --force origin main" + dq)[1])
        ps = G.decide({"tool_name": "PowerShell", "tool_input": {"command": "echo " + bt + dq + semi + " git push --force origin main"},
                       "cwd": str(self.app)}, "claude", self.root)
        self.assertEqual(ps[0], "deny")
        self.assertIn("금지", self.bash("cmd /c 'echo " + caret + dq + "& git push --force origin main'")[1])
        self.assertIn("금지", self.bash("cmd /c \"echo 'a & git push --force origin main & echo b'\"")[1])  # cmd에는 작은따옴표가 없다
        codex = G.decide({"tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "echo " + bs + dq + semi + " git push --force origin main"]},
                          "cwd": str(self.app)}, "codex", self.root)
        self.assertEqual(codex[0], "deny")
        # 데이터로 남는 escape는 그대로 허용
        self.assertEqual(self.bash("echo " + bs + semi + " git push --force origin main")[0], "allow")
        self.assertEqual(self.bash('git commit -m "say ' + bs + dq + 'hi' + bs + dq + '"')[0], "allow")

    def test_r4_unittest_shadowing(self):
        """4: cwd의 unittest.py / unittest/ 가 표준 모듈을 가릴 수 있다 → -I 격리 실행만 허용."""
        (self.app / "tests").mkdir()
        self.assertEqual(self.bash("python -m unittest discover -s tests")[0], "allow")
        for shadow in ("unittest.py", "unittest"):
            path = self.app / shadow
            path.mkdir() if shadow == "unittest" else path.write_text("import os\n", encoding="utf-8")
            self._not_allowed("python -m unittest discover -s tests")
            self._not_allowed("python -m unittest")
            self.assertEqual(self.bash("python -I -m unittest discover -s tests")[0], "allow")
            shutil.rmtree(path) if shadow == "unittest" else path.unlink()

    def test_r4_read_only_npm_runs_no_git_and_npm_test_few(self):
        """5: 읽기 명령은 build input 검사 전에 분리, 한 판정 안 git 조회는 캐시·묶음."""
        calls = []
        real = subprocess.run

        def counting(*args, **kwargs):
            if args and args[0] and args[0][0] == "git":
                calls.append(args[0])
            return real(*args, **kwargs)
        G.subprocess.run = counting
        try:
            for cmd, most in (("npm -v", 0), ("npm --version", 0), ("npm ls", 0), ("npm test", 3), ("npm ci && npm test", 3)):
                calls.clear()
                self.assertEqual(self.bash(cmd)[0], "allow", cmd)
                self.assertLessEqual(len(calls), most, (cmd, calls))
        finally:
            G.subprocess.run = real

    def test_r4_git_default_ask(self):
        self.approve("push")
        for cmd in ("git -c core.askPass=./x.sh fetch origin", "git -c protocol.ext.allow=always fetch origin",
                    "git --exec-path=. fetch origin", "git submodule foreach git push --force origin main",
                    "git rebase -x ./x.sh main", "git bisect run ./x.sh", "git clone --template=t https://github.com/me/x.git",
                    "git config core.hooksPath hooks", "git config core.sshCommand ./x.sh", "git difftool",
                    "git push $'--force' origin main", "git push origin $BRANCH", "rm $TARGET", "echo x > $HOME/a.txt"):
            self._not_allowed(cmd)
        self.assertIn("금지", self.bash("echo x > ~/a.txt")[1])  # ~ 는 셸이 홈 폴더로 바꾼다
        for cmd in ("git config user.name t", "git config --get core.hooksPath", "git -c user.name=t -c user.email=t@t commit -m x",
                    "git -c color.ui=never log --oneline"):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)

    def test_r4_in_tree_hooks_must_match_baseline(self):
        hooks = self.app / ".githooks"
        hooks.mkdir()
        (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        git(self.app, "add", ".")
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "hooks")
        fetched_baseline(self.app)
        git(self.app, "config", "core.hooksPath", ".githooks")
        self.assertEqual(self.bash("git commit -m x")[0], "allow")
        (hooks / "pre-commit").write_text("#!/bin/sh\ngit push --force origin main\n", encoding="utf-8")
        self._not_allowed("git commit -m x")
        self._not_allowed("git push origin feat/x")
        (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (hooks / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")  # 새 훅
        self._not_allowed("git commit -m x")


class T7FalsePositiveTest(GuardTest):
    build = classmethod(BuildAllowlistTest.build.__func__)

    def test_t7_git_am_encoded_mail_requires_confirmation(self):
        safe = '--- /dev/null\n+++ b/safe.md\n@@ -0,0 +1 @@\n+ok\n'
        secret = '--- /dev/null\n+++ b/projects/x/project.json\n@@ -0,0 +1 @@\n+{}\n'
        encoded = base64.b64encode(secret.encode()).decode()
        cases = (
            'Content-Transfer-Encoding: quoted-printable\n\n' + secret.replace('project.json', 'project=2Ejson'),
            safe + '\nFrom second@example.com\nContent-Transfer-Encoding: base64\n\n' + encoded + '\n',
            'Content-Type: multipart/mixed; boundary=X\n\n--X\n' + safe,
            'cOnTeNt-TyPe:\n multipart/alternative; boundary=X\n\n' + safe,
            'content-transfer-encoding:\n Base64\n\n' + safe,
            'Content-Transfer-Encoding: binary\n\n' + safe,
            safe + '\n' + encoded + '\n',
            'Content-Transfer-Encoding: 7bit\n\n' + safe + '\n' + encoded + '\n',
            safe + '\n' + '\n'.join(encoded[i:i + 8] for i in range(0, len(encoded), 8)) + '\n',
        )
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.bash("git am <<'X'\n" + body + 'X')[0], 'ask')
                self.assertEqual(self.bash("git am <<'X'\n" + body + 'X', agent='codex')[0], 'deny')
                (self.app / 'encoded.mbox').write_text(body, encoding='utf-8')
                for command in ('git am encoded.mbox', 'git am -3 encoded.mbox'):
                    self.assertEqual(self.bash(command)[0], 'ask')
        for encoding in ('7bit', '8bit'):
            self.assertEqual(self.bash("git am <<'X'\nContent-Transfer-Encoding: " + encoding + '\n\n' + safe + 'X')[0], 'allow')
        self.assertEqual(self.bash('git am missing.mbox')[0], 'ask')

    def test_t7_git_am_content_type_allowlist(self):
        safe = '--- /dev/null\n+++ b/safe.md\n@@ -0,0 +1 @@\n+ok\n'
        # format=flowed; delsp=yes: git mailinfo joins "proj " + "ect.json" into project.json
        flowed = '--- /dev/null\n+++ b/projects/x/proj \nect.json\n@@ -0,0 +1 @@\n+{}\n'
        opaque = (
            'Content-Type: text/plain; charset=UTF-8; format=flowed; delsp=yes\n\n' + flowed,
            'Content-Type: text/plain; format=flowed\n\n' + safe,
            'Content-Type: text/plain; charset=utf-8; x-unknown=1\n\n' + safe,
            'Content-Type: text/plain; charset=iso-8859-1\n\n' + safe,
            'Content-Type: text/plain; charset=utf-8; charset=utf-8\n\n' + safe,
            'Content-Type: text/plain (comment)\n\n' + safe,
            'Content-Type: text/html\n\n' + safe,
            'Content-Type: application/octet-stream\n\n' + safe,
            'Content-Type:\n text/plain;\n format=flowed\n\n' + safe,
            'Content-Type: text/plain\n\n' + safe + '\nFrom second@example.com\n'
            'Content-Type: text/plain; format=flowed; delsp=yes\n\n' + flowed,
        )
        for body in opaque:
            with self.subTest(body=body):
                self.assertEqual(self.bash("git am <<'X'\n" + body + 'X')[0], 'ask')
                self.assertEqual(self.bash("git am <<'X'\n" + body + 'X', agent='codex')[0], 'deny')
                (self.app / 'flowed.mbox').write_text(body, encoding='utf-8')
                for command in ('git am flowed.mbox', 'git am -3 flowed.mbox'):
                    self.assertEqual(self.bash(command)[0], 'ask')
        plain = (
            'Content-Type: text/plain\n\n',
            'Content-Type: text/plain; charset=UTF-8\n\n',
            'content-type: TEXT/PLAIN ; Charset = "us-ascii"\n\n',
            'Content-Type: text/plain;\n charset="utf-8"\nContent-Transfer-Encoding: 8bit\n\n',
        )
        for head in plain:
            with self.subTest(head=head):
                self.assertEqual(self.bash("git am <<'X'\n" + head + safe + 'X')[0], 'allow')
                (self.app / 'plain.mbox').write_text(head + safe, encoding='utf-8')
                self.assertEqual(self.bash('git am plain.mbox')[0], 'allow')
                secret = '--- /dev/null\n+++ b/projects/x/project.json\n@@ -0,0 +1 @@\n+{}\n'
                self.assertEqual(self.bash("git am <<'X'\n" + head + secret + 'X')[0], 'deny')

    def test_t7_git_patch_inputs_protect_paths(self):
        for command in ('git apply', 'git apply --cached', 'git am', 'git am -3',
                        'git -c user.name=test apply', 'git apply -p0',
                        'git apply --directory=.am', 'git apply --directory .am'):
            for name in ('projects/x/project.json', '.am/local.json', '../other/main.py'):
                path = name if '-p0' in command else 'b/' + name
                if '--directory' in command:
                    path = 'b/local.json'
                body = f'--- /dev/null\n+++ {path}\n@@ -0,0 +1 @@\n+{{}}\n'
                with self.subTest(command=command, name=name):
                    self.assertEqual(self.bash(command + " <<'X'\n" + body + 'X')[0], 'deny')

    def test_t7_git_patch_files_and_safe_messages(self):
        patchfile = self.app / 'change.patch'
        for command in ('git apply change.patch', 'git apply --cached change.patch', 'git am -3 change.patch'):
            patchfile.write_text('--- /dev/null\n+++ b/.am/local.json\n@@ -0,0 +1 @@\n+{}\n', encoding='utf-8')
            self.assertEqual(self.bash(command)[0], 'deny', command)
        for command in ('git apply', 'git apply --cached', 'git am -3'):
            self.assertEqual(self.bash(command + " <<'X'\n--- /dev/null\n+++ b/safe.md\n@@ -0,0 +1 @@\n+ok\nX")[0], 'allow')
        body = '+++ b/.am/local.json\nssh production\n'
        for command in ('git commit -F -', 'git notes add --file=-', 'git tag -F - v1'):
            self.assertEqual(self.bash(command + " <<'X'\n" + body + 'X')[0], 'allow')
        self.assertNotEqual(self.bash('git apply missing.patch')[0], 'allow')

    def test_t7_git_patch_path_variants(self):
        bodies = ('--- a/.am/local.json\n+++ /dev/null\n',
                  'diff --git a/.am/local.json b/safe.md\nrename from .am/local.json\nrename to safe.md\n',
                  '--- /dev/null\n+++ "b/.am/local.json"\n',
                  '--- /dev/null\n+++ b/../other/main.py\n')
        for body in bodies:
            self.assertEqual(self.bash("git apply <<'X'\n" + body + 'X')[0], 'deny', body)
        self.assertEqual(self.bash(f'git -C "{self.other.as_posix()}" apply' + " <<'X'\n+++ b/main.py\nX")[0], 'deny')
        self.assertEqual(self.bash("git apply -p0 <<'X'\n+++ .am/local.json\nX")[0], 'deny')
        for command in ('git apply --unknown', 'git apply --directory="$DIR"', 'git status'):
            self.assertNotEqual(self.bash(command + " <<'X'\n+++ b/safe.md\nX")[0], 'allow')

    def test_t7_reviewflow_collect_pin(self):
        home = self.app.parent / 'home'
        script = home / '.claude/plugins/cache/taeng-marketplace/reviewflow/0.1.0/scripts/review.sh'
        script.parent.mkdir(parents=True)
        content = b'#!/bin/bash\n# pinned collect fixture\n'
        script.write_bytes(content)
        command_path = script.as_posix()
        with patch.object(Path, 'home', return_value=home), patch.object(
                G, 'REVIEWFLOW_COLLECT_SHA256', hashlib.sha256(content).hexdigest(), create=True):
            for prefix in ('bash ', ''):
                self.assertEqual(self.bash(f'{prefix}"{command_path}" collect 123')[0], 'allow')
            for args in ('record', 'comment', 'collect --upload', 'collect main extra'):
                self.assertEqual(self.bash(f'bash "{command_path}" {args}')[0], 'ask')
            script.write_bytes(content + b'# modified\n')
            self.assertEqual(self.bash(f'bash "{command_path}" collect')[0], 'ask')

    def test_t7_external_cd_readonly(self):
        other, app = self.other.as_posix(), self.app.as_posix()
        for command in (f'cd "{other}" && git status',
                        f'cd "{other}"; grep hello README.md | head -3',
                        'cd ~; orca orchestration check',
                        f'cd "{other}"; cd "{app}"; pwd'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], 'allow')
        for command in (f'cd "{other}"; touch x', f'cd "{other}"; git commit -m x',
                        f'cd "{other}"; git push origin main', f'cd "{other}"; bash scripts/deploy.sh',
                        f'cd "{other}"; rm -rf src', f'cd "{other}"; echo x > out',
                        f'cd "{other}"; git status --output=out', f'cd "{other}"; rg --pre bash x',
                        f'cd "{other}"; sqlite3 db "DROP TABLE users"',
                        'cd "$HOME"; pwd', 'cd "$(pwd)"; pwd', 'cd /missing-t7-path; pwd', 'cd ..; pwd'):
            self.assertNotEqual(self.bash(command)[0], 'allow', command)

    def test_t7_git_output_options_are_writes(self):
        other, app = self.other.as_posix(), self.app.as_posix()
        keep = (self.other / 'keep.txt')
        keep.write_text('data\n', encoding='utf-8')
        target = keep.as_posix()
        for command in (f'cd "{other}" && git diff --output="{target}"',
                        f'cd "{other}" && git diff --output "{target}"',
                        f'cd "{other}"; git log -1 --output={target}',
                        f'cd "{other}"; git show HEAD --output={target}',
                        f'git -C "{other}" diff --output={target}'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], 'deny')
                self.assertEqual(self.bash(command, agent='codex')[0], 'deny')
        for command in (f'cd "{other}" && git diff --output=keep.txt',
                        f'cd "{other}" && git log --output keep.txt',
                        f'cd "{other}" && git format-patch -o out HEAD~1'):
            with self.subTest(command=command):
                self.assertNotEqual(self.bash(command)[0], 'allow')
                self.assertEqual(self.bash(command, agent='codex')[0], 'deny')
        for command in (f'cd "{other}" && git diff --stat', f'cd "{other}"; git log --oneline -3',
                        f'cd "{other}"; git show --stat HEAD', f'cd "{app}"; git diff --output=out.diff'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], 'allow')

    def test_t7_same_line_write_breaks_content_trust(self):
        safe = '--- /dev/null\n+++ b/safe.md\n@@ -0,0 +1 @@\n+ok\n'
        (self.app / 'safe.patch').write_text(safe, encoding='utf-8')
        (self.app / 'evil.patch').write_text(safe.replace('safe.md', 'projects/x/project.json'), encoding='utf-8')
        for command in ('git apply safe.patch', 'git am safe.patch', 'git apply --check safe.patch && git status',
                        'python scripts/check.py', 'sh scripts/check.sh', './scripts/check.sh',
                        'python scripts/check.py && git status', 'git status; sh scripts/check.sh',
                        'sh scripts/check.sh > out.txt; cat out.txt',
                        'git add -A && git commit -m x'):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], 'allow')
        # 추적 안 된 셸 소스가 생기면 원격 main과 같은 셸 스크립트도 source로 바뀐 코드를 읽을 수 있어 확인(모든 언어 공통)
        (self.app / 'evil.sh').write_text('git push --force origin main\n', encoding='utf-8')
        self.assertEqual(self.bash('sh scripts/check.sh')[0], 'ask')
        for command in ('cp evil.patch safe.patch && git apply safe.patch',
                        'cp evil.patch safe.patch; git am safe.patch',
                        'cp evil.patch safe.patch\ngit apply safe.patch',
                        'mv evil.patch safe.patch || git apply safe.patch',
                        'echo x > safe.patch && git apply safe.patch',
                        'cat evil.patch | tee safe.patch | git apply safe.patch',
                        'sed -i s/safe/x/ safe.patch && git apply safe.patch',
                        'ln -sf evil.patch safe.patch && git apply safe.patch',
                        'git checkout HEAD~1 -- safe.patch && git apply safe.patch',
                        'cp evil.sh scripts/check.sh && sh scripts/check.sh',
                        'cp evil.sh scripts/check.sh; ./scripts/check.sh',
                        'cp evil.sh scripts/check.py && python scripts/check.py',
                        'ln -sf ../evil.sh scripts/check.sh && bash scripts/check.sh',
                        'echo x >> gradlew && ./gradlew test',
                        'git restore --source=HEAD~1 scripts/check.sh && sh scripts/check.sh',
                        'cp evil.sh scripts/check.sh && bash -c "sh scripts/check.sh"',
                        'bash -c "cp evil.sh scripts/check.sh" && sh scripts/check.sh',
                        'cp evil.sh scripts/check.sh && echo $(sh scripts/check.sh)',
                        "cat > scripts/check.sh <<'X'\ngit push origin main\nX\nsh scripts/check.sh",
                        "python - <<'X'\nfrom pathlib import Path\nPath('scripts/check.sh').write_text('x')\nX\n"
                        "sh scripts/check.sh",
                        "python3 - <<'X' && git apply safe.patch\nfrom pathlib import Path\nPath('safe.patch').write_text('x')\nX",
                        "node - <<'X'\nrequire('fs').writeFileSync('scripts/check.sh', 'x')\nX\nsh scripts/check.sh",
                        "python -c \"print(1)\" && sh scripts/check.sh",
                        "node -e \"1\"; python scripts/check.py",
                        "echo x | python && git apply safe.patch",
                        "cat x.py | python - ; ./scripts/check.sh"):
            with self.subTest(command=command):
                self.assertNotEqual(self.bash(command)[0], 'allow')
                self.assertEqual(self.bash(command, agent='codex')[0], 'deny')

    def test_t7_document_stdin(self):
        body = 'ssh production\nvercel deploy\ngit push --force origin main\n'
        for head in ('git commit -F -', 'git -c user.name=test commit --file=-',
                     'orca orchestration check', 'orca orchestration send --body document'):
            self.assertEqual(self.bash(head + " <<'DOC'\n" + body + 'DOC')[0], 'allow', head)
        for head in ('bash', 'python scripts/check.py', 'ssh production', 'env bash'):
            self.assertNotEqual(self.bash(head + " <<'DOC'\n" + body + 'DOC')[0], 'allow', head)
        self.assertNotEqual(self.bash("orca orchestration check <<DOC\n$(git push origin main)\nDOC")[0], 'allow')
        self.assertEqual(self.bash("orca orchestration check <<'DOC'\nx\nDOC\ngit push --force origin main")[0], 'deny')

    def test_t7_script_launchers(self):
        for command in ('bash scripts/check.sh', 'zsh scripts/check.sh', './scripts/check.sh', './scripts/check.py'):
            self.assertEqual(self.bash(command)[0], 'allow', command)
        (self.app / 'monitoring').mkdir()
        script = self.app / 'monitoring/validate.py'
        script.write_text('print("ok")\n', encoding='utf-8')
        self.assertEqual(self.bash('python3 monitoring/validate.py')[0], 'ask')
        git(self.app, 'add', 'monitoring/validate.py')
        git(self.app, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'validation')
        self.assertEqual(self.bash('python3 monitoring/validate.py')[0], 'ask')
        fetched_baseline(self.app)
        self.assertEqual(self.bash('python3 monitoring/validate.py')[0], 'allow')
        script.write_text('# changed\n', encoding='utf-8')
        self.assertEqual(self.bash('./monitoring/validate.py')[0], 'ask')
        (self.app / 'scripts/check.sh').write_text('# changed\n', encoding='utf-8')
        for command in ('bash scripts/check.sh', './scripts/check.sh'):
            self.assertEqual(self.bash(command)[0], 'ask', command)
        git(self.app, 'add', 'scripts/check.sh')
        self.assertEqual(self.bash('bash scripts/check.sh')[0], 'ask')
        self.assertEqual(self.bash('bash scripts/deploy.sh')[0], 'deny')

    def test_t7_specs_and_scratch_boundaries(self):
        for path in ('.am/specs/T7.md', '.am/specs/repro/example.py'):
            self.assertEqual(self.tool('Write', self.app / path)[0], 'allow', path)
            self.assertEqual(self.bash(f"cat > {path} <<'DOC'\nssh production\nDOC")[0], 'allow', path)
        for path in ('.am/local.json', '.am/approvals.json', '.am/specs/../local.json',
                     '.am/specs', 'projects/app/project.json', '.am/specs/project.json'):
            self.assertEqual(self.tool('Write', self.app / path)[0], 'deny', path)
        specs = self.app / '.am/specs'
        specs.mkdir(parents=True)
        try:
            (specs / 'escape').symlink_to(Path(self.root), target_is_directory=True)
        except OSError:
            pass
        else:
            self.assertEqual(self.tool('Write', specs / 'escape/.am/local.json')[0], 'deny')
            (specs / 'document.md').write_text('data', encoding='utf-8')
            (self.app / '.am/local.json').symlink_to(specs / 'document.md')
            self.assertEqual(self.tool('Write', self.app / '.am/local.json')[0], 'deny')
        if os.name != 'nt':
            self.assertEqual(self.tool('Write', '/private/tmp/claude-t7/session/scratchpad/spec.md')[0], 'allow')
            self.assertEqual(self.tool('Write', '/private/tmp/claude-t7/session/other/spec.md')[0], 'deny')


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
        self.assertEqual(added, "added")
        self.assertEqual(cli.install_guard(repo)[1], "current")
        cfg = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 1)
        self.assertIn("am/guard.py\" claude", cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"])


class RemoteDeployTest(unittest.TestCase):
    def findings(self, cmd):
        return G.classify_command(cmd, tempfile.gettempdir())

    def test_remote_mutations(self):
        commands = [
            'scp build/libs/app.jar user@host:/opt/app/',
            'scp -i key -o BatchMode=yes app.jar host:/tmp/x.jar',
            'ssh -i key -o BatchMode=yes host "sudo cp -p a b; sudo install -o u -g u -m 644 t a; sudo systemctl restart svc"',
            "ssh host 'sudo cp -p bak a && sudo systemctl restart svc'",
            'scp -i key -P 22 -r build host:/opt/app',
            'scp host:/old/app.jar other:/opt/app',
            'rsync -av -e "ssh -i key" build/ user@host:/opt/app',
            'rsync build/ rsync://host/app',
            'sftp -P 22 user@host:/opt/app',
            'ssh host', 'ssh -t host "sudo systemctl restart x"',
            'ssh -o StrictHostKeyChecking=yes -i key host -- systemctl restart app',
            "ssh host 'systemctl status app && systemctl restart app'",
            'ssh host "uptime; service app stop"',
            'ssh host "echo ok > /opt/file"', 'ssh host "echo ok | tee /opt/file"',
            'ssh host "$COMMAND"', 'ssh host bash -s < script',
            "ssh host <<'EOF'\nuptime\nEOF",
            "ssh host cat <<'EOF'\nuptime\nEOF",
        ]
        for remote in ('sudo cat /etc/hosts', 'mv a b', 'cp a b', 'rm a', 'ln -s a b',
                       'chmod 755 a', 'chown app a', 'java -jar app.jar', 'kill 123',
                       'pkill java', 'docker compose up -d', 'docker down', 'docker restart app',
                       'docker run app', 'docker rm app', 'docker pull app', './deploy.sh',
                       'bash deploy.sh', 'systemctl enable app', 'systemctl disable app',
                       'systemctl reload app', 'systemctl start app'):
            commands.append(f'ssh host "{remote}"')
        for cmd in commands:
            with self.subTest(cmd=cmd):
                gated = [f for f in self.findings(cmd) if f.get('gate') == 'deploy']
                self.assertTrue(gated, cmd)
                self.assertEqual(gated[0]['ref'], 'other' if cmd.startswith('scp host:/') else 'host')

    def test_readonly_and_download_still_ask(self):
        commands = ['scp -P 22 host:/opt/app ./app',
                    'rsync -e ssh user@host:/logs/ ./logs/',
                    'scp C:/build/app.jar ./app.jar', 'scp app ./build/foo:bar', 'systemctl restart app']
        for remote in ('systemctl status app', 'journalctl -u app', 'free -h', 'df -h',
                       'uptime', 'cat /var/log/app', 'tail /var/log/app',
                       'grep error /var/log/app', 'curl http://localhost/health'):
            commands.append(f'ssh host "{remote}"')
        for cmd in commands:
            with self.subTest(cmd=cmd):
                fs = self.findings(cmd)
                self.assertFalse(any(f.get('gate') == 'deploy' for f in fs))
                self.assertTrue(any(f['kind'] == 'opaque' for f in fs))


class DeployScriptTest(unittest.TestCase):
    def test_shell_deploy_script(self):
        for cmd in ('bash scripts/deploy.sh', 'sh -eu /app/scripts/deploy.sh --skip-build',
                    'zsh scripts/deploy.sh --skip-build', './deploy.sh',
                    'bash -c "bash scripts/deploy.sh"', 'bash -o pipefail scripts/deploy.sh',
                    'bash -- scripts/deploy.sh --skip-build'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any(f.get('gate') == 'deploy' for f in
                                    G.classify_command(cmd, tempfile.gettempdir())))


class T6SecurityReviewTest(unittest.TestCase):
    def findings(self, cmd):
        return G.classify_command(cmd, tempfile.gettempdir())

    def test_more_remote_mutation_and_read_boundaries(self):
        for remote in ('curl -X POST http://localhost', 'curl --json data http://localhost',
                       'curl -d data http://localhost', 'journalctl --vacuum-time=1d',
                       'journalctl --setup-keys'):
            fs = self.findings(f'ssh host "{remote}"')
            self.assertEqual([f['ref'] for f in fs if f.get('gate') == 'deploy'], ['host'])
        self.assertEqual([f['ref'] for f in self.findings('sftp user@host') if f.get('gate') == 'deploy'], ['host'])
        for cmd in ("ssh host \"grep '<error>' log\"", "ssh host 'grep \"<error>\" log'"):
            fs = self.findings(cmd)
            self.assertFalse(any(f.get('gate') == 'deploy' for f in fs), fs)
            self.assertTrue(any(f['kind'] == 'opaque' for f in fs), fs)
        escaped = r'ssh host "cat \"; reboot; echo \""'
        fs = self.findings(escaped)
        self.assertTrue(any(f.get('gate') == 'deploy' for f in fs), fs)

    def test_script_approval_is_scoped_and_dry_run_asks(self):
        # 레포 밖(임시 폴더)이라 스크립트별 승인 대상이 없다 — 전체 배포 승인이 필요(레포 안: T6Round3Test)
        for cmd in ('./deploy.sh', 'bash scripts/deploy.sh --skip-build'):
            self.assertEqual([f['ref'] for f in self.findings(cmd) if f.get('gate') == 'deploy'], [None])
        for cmd in ('./deploy.sh --dry-run', 'bash scripts/deploy.sh --checks-only',
                    'zsh -eu scripts/deploy.sh --dry-run'):
            with self.subTest(cmd=cmd):
                fs = self.findings(cmd)
                self.assertFalse(any(f.get('gate') == 'deploy' for f in fs), fs)
                self.assertTrue(any(f['kind'] == 'opaque' for f in fs), fs)

    def test_hook_rejects_empty_or_malformed_payload(self):
        from unittest.mock import patch
        for raw in ('', ' ', '{broken', '{}', '[]', 'null'):
            with self.subTest(raw=raw), patch.object(sys, 'stdin', io.StringIO(raw)), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(G.main(['claude']), 2)

    def test_exact_server_refs_and_transport_values(self):
        cases = [
            ('ssh -P production host sudo true', 'host'),
            ('ssh -o HostName=actual.example alias sudo true', 'actual.example'),
            ('scp app scp://user@host:22/app', 'host'),
            ('sftp sftp://user@host:22/app', 'host'),
            ('rsync app host:/app --link-dest /previous', 'host'),
            ('rsync --compare-dest /previous app host:/app', 'host'),
            ('rsync app host:/app --copy-dest=/previous', 'host'),
            ('sftp -s /usr/lib/sftp-server user@host', 'host'),
        ]
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                fs = self.findings(cmd)
                self.assertEqual([f['ref'] for f in fs if f.get('gate') == 'deploy'], [expected])
                if 'HostName=' in cmd:
                    self.assertTrue(any(f['kind'] == 'opaque' for f in fs))

    def test_transport_local_execution_stays_opaque(self):
        commands = [
            'ssh -o ProxyCommand="sh proxy.sh" host sudo true',
            'ssh -oLocalCommand="sh local.sh" -oPermitLocalCommand=yes host sudo true',
            'ssh -F config host sudo true', 'scp -F config app host:/app',
            'ssh -oKnownHostsCommand=helper host sudo true', 'ssh -o "Match exec anything" host sudo true',
            'scp -S local-program app.jar host:/app', 'scp -D local-server app host:/app',
            'rsync -e "sh transport.sh" app host:/app',
            'rsync app host:/app --rsh="sh transport.sh"',
            'rsync -e "ssh -oProxyCommand=helper" app host:/app',
            'rsync -D app host:/app', 'rsync -e /tmp/ssh app host:/app',
        ]
        for cmd in commands:
            with self.subTest(cmd=cmd):
                fs = self.findings(cmd)
                self.assertTrue(any(f.get('gate') == 'deploy' for f in fs), fs)
                self.assertTrue(any(f['kind'] == 'opaque' for f in fs), fs)

    def test_shell_command_keeps_all_findings(self):
        for shell in ('bash -c', 'sh -ec', 'zsh -lc'):
            for inner, kind in (('git push --force origin main', 'forbidden'),
                                ('python -m am approve x merge_main', 'approve')):
                with self.subTest(shell=shell, inner=inner):
                    fs = self.findings(f'{shell} "{inner}; ./deploy.sh"')
                    self.assertTrue(any(f['kind'] == kind for f in fs), fs)
                    self.assertTrue(any(f.get('gate') == 'deploy' for f in fs), fs)


class T6Round3Test(unittest.TestCase):
    """3차 수정: 셸 -c 위치, 배포 스크립트 승인 대상, 한 글자 서버, curl 묶음 옵션."""
    def findings(self, cmd, cwd=None):
        return G.classify_command(cmd, cwd or tempfile.gettempdir())

    def deploy_refs(self, cmd, cwd=None):
        return [f['ref'] for f in self.findings(cmd, cwd) if f.get('gate') == 'deploy']

    def test_c_after_script_is_a_script_argument(self):
        # -c 뒤 문자열만 보고 자동 허용하면 안 된다: 스크립트가 실행된다
        for cmd in ('bash scripts/deploy.sh -c true', 'bash scripts/deploy.sh -c "git status"',
                    'sh ./deploy.sh -xc ls', 'bash -e scripts/deploy.sh -c true',
                    'bash -euo pipefail scripts/deploy.sh', 'dash ./deploy.sh -c true'):
            with self.subTest(cmd=cmd):
                self.assertTrue(self.deploy_refs(cmd), self.findings(cmd))
        for cmd in ('bash evil.sh -c true', 'sh evil.sh -xc ls', 'zsh evil.sh -ec true',
                    'bash -- -c true', 'bash -- evil.sh -c true', 'dash evil.sh -c true',
                    'pwsh evil.ps1 -c true', 'pwsh evil.ps1 -command true', 'cmd evil.bat /c echo'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any(f['kind'] == 'opaque' for f in self.findings(cmd)), self.findings(cmd))

    def test_c_before_script_is_still_parsed(self):
        for prefix in ('bash -c', 'bash -xc', 'sh -ec', 'bash --norc -c', 'bash --login -c',
                       'bash -o pipefail -c', 'zsh -lc', 'dash -c', 'pwsh -NoProfile -c',
                       'pwsh -ExecutionPolicy Bypass -Command'):
            with self.subTest(prefix=prefix):
                fs = self.findings(f'{prefix} "git push --force origin main"')
                self.assertTrue(any(f['kind'] == 'forbidden' for f in fs), fs)

    def test_deploy_script_ref_is_namespaced_repo_path(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "app"
            (repo / "scripts").mkdir(parents=True)
            git(repo, "init", "-q")
            (repo / "deploy.sh").write_text("echo deploy\n", encoding="utf-8")
            cases = [('./deploy.sh', 'repo-script:deploy.sh'), ('bash deploy.sh', 'repo-script:deploy.sh'),
                     ('bash scripts/deploy.sh --skip-build', 'repo-script:scripts/deploy.sh'),
                     ('sh -e ./scripts/deploy.sh', 'repo-script:scripts/deploy.sh')]
            for cmd, ref in cases:
                with self.subTest(cmd=cmd):
                    self.assertEqual(self.deploy_refs(cmd, repo), [ref])
            # 레포 밖·PATH의 deploy.sh는 이름만 같다 — 전체 승인 필요 + 사람 확인
            outside = Path(d) / "evil" / "deploy.sh"
            for cmd in (f'bash {outside}', f'{outside}', 'deploy.sh', 'bash ../evil/deploy.sh'):
                with self.subTest(cmd=cmd):
                    fs = self.findings(cmd, repo)
                    self.assertEqual([f['ref'] for f in fs if f.get('gate') == 'deploy'], [None], fs)
                    self.assertTrue(any(f['kind'] == 'opaque' for f in fs), fs)
            # 서버 별칭 deploy.sh와 겹치지 않는다
            self.assertEqual(self.deploy_refs('ssh deploy.sh sudo true', repo), ['deploy.sh'])
            p = {"approvals": []}
            S.approve(p, "deploy", "deploy.sh")
            self.assertFalse(S.has_approval(p, "deploy", "repo-script:deploy.sh"))

    def test_single_letter_host_depends_on_platform(self):
        from unittest.mock import patch
        with patch.object(G, 'WINDOWS', False):
            self.assertEqual(self.deploy_refs('scp app.jar h:/opt/'), ['h'])
        with patch.object(G, 'WINDOWS', True):
            self.assertEqual(self.deploy_refs('scp app.jar h:/opt/'), [])

    def test_curl_bundled_and_writing_options(self):
        for remote in ('curl -sSLX POST http://localhost', 'curl -sd data http://localhost',
                       'curl -sO http://localhost/x', 'curl -c jar http://localhost',
                       'curl -D head http://localhost', 'curl --cookie-jar=jar http://localhost'):
            with self.subTest(remote=remote):
                self.assertEqual(self.deploy_refs(f'ssh host "{remote}"'), ['host'])
        for remote in ('curl -sSL http://localhost/health', 'curl -sH "Accept: x" http://localhost',
                       'curl -m 5 http://localhost'):
            with self.subTest(remote=remote):
                fs = self.findings(f'ssh host "{remote}"')
                self.assertFalse(any(f.get('gate') == 'deploy' for f in fs), fs)


class T6Round4Test(GuardTest):
    """4차 수정: 접속 프로그램·셸 옵션은 알려진 안전 옵션만 통과(허용 목록), 로컬 출력 파일은 쓰기 검사."""
    def kinds(self, cmd):
        return G.classify_command(cmd, self.app, self.root)

    def test_local_execution_probes_need_a_person(self):
        (self.app / 'deploy.sh').write_text('echo deploy\n', encoding='utf-8')
        self.approve('deploy', 'host')
        self.approve('deploy', 'repo-script:deploy.sh')
        for cmd in ('ssh -I ./provider.dylib host sudo true',
                    'rsync -e "ssh -I ./provider.dylib" app host:/app',
                    'bash --rcfile ./evil.rc -i ./deploy.sh',
                    'bash --init-file ./evil.rc -ic "./deploy.sh"',
                    'zsh -i ./deploy.sh', 'bash -li ./deploy.sh',
                    'bash -O extglob ./deploy.sh', 'sh -s ./deploy.sh',
                    'ENV=./evil.rc sh -i ./deploy.sh', 'BASH_ENV=./evil.rc bash ./deploy.sh',
                    'ZDOTDIR=. zsh ./deploy.sh', 'env BASH_ENV=./evil.rc bash ./deploy.sh',
                    f'ssh -E {self.root}/projects/app/project.json host sudo true',
                    f'rsync --log-file={self.root}/projects/app/project.json app host:/app',
                    'ssh -A host sudo true', 'ssh -S ./ctl host sudo true', 'ssh -P tag host sudo true',
                    'ssh -o UserKnownHostsFile=./known host sudo true', 'ssh -o ControlPath=./c host sudo true',
                    'scp -A app host:/app', 'sftp -b ./batch host', 'rsync --rsync-path="sudo rsync" app host:/app',
                    'rsync --write-batch=./b app host:/app', 'rsync -D app host:/app'):
            with self.subTest(cmd=cmd):
                for agent in ('codex', 'claude'):
                    self.assertNotEqual(self.bash(cmd, agent=agent)[0], 'allow', self.kinds(cmd))

    def test_local_output_files_go_through_the_write_check(self):
        state = f'{self.root}/projects/app/project.json'
        for cmd in (f'ssh -E {state} host sudo true', f'rsync --log-file={state} app host:/app',
                    f'rsync --log-file {state} app host:/app', f'rsync -e "ssh -E {state}" app host:/app',
                    f'rsync --only-write-batch={state} app host:/app'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any(f['kind'] == 'forbidden' for f in self.kinds(cmd)), self.kinds(cmd))

    def test_known_safe_options_only_need_the_deploy_approval(self):
        plugin = Path(self.tmp.name) / 'home' / '.claude/plugins/cache/taeng-marketplace/devflow/0.5.3/scripts'
        plugin.mkdir(parents=True)
        (plugin / 'deploy.sh').write_text('echo deploy\n', encoding='utf-8')
        trova = ('scp -i k -o BatchMode=yes app.jar host:/tmp/x.jar ; '
                 'ssh -i k -o BatchMode=yes host "sudo cp -p a b; sudo install -o u -g u -m 644 t a; '
                 'sudo systemctl restart svc" ; '
                 f'bash {plugin}/deploy.sh --skip-build')
        cases = [(trova, ['host', 'host', 'plugin-script:taeng-marketplace/devflow/scripts/deploy.sh']),
                 ('ssh -q -t -T -C -4 -p 22 -l me -o StrictHostKeyChecking=yes -o ConnectTimeout=5 '
                  '-o UserKnownHostsFile=/dev/null host sudo true', ['host']),
                 ('scp -q -r -p -C -P 22 -i k app host:/app', ['host']),
                 ('rsync -avz --delete --progress --exclude=.git -e "ssh -i k -p 22" app host:/app', ['host']),
                 ('bash -euo pipefail ./deploy.sh --skip-build', ['repo-script:deploy.sh']),
                 ('bash --norc --noprofile -xc "./deploy.sh"', ['repo-script:deploy.sh'])]
        (self.app / 'deploy.sh').write_text('echo deploy\n', encoding='utf-8')
        home = {'HOME': str(Path(self.tmp.name) / 'home'), 'USERPROFILE': str(Path(self.tmp.name) / 'home')}
        from unittest.mock import patch
        with patch.dict(os.environ, home):
            for cmd, refs in cases:
                with self.subTest(cmd=cmd):
                    fs = [f for f in self.kinds(cmd) if f['kind'] != 'seen']
                    self.assertEqual([f.get('ref') for f in fs if f.get('gate') == 'deploy'], refs, fs)
                    self.assertEqual([f for f in fs if f.get('gate') != 'deploy'], [], fs)


class T6Round5Test(GuardTest):
    """5차 수정: rsync -e 안 ssh 피연산자, 스크립트 승인 대상 이름공간, POSIX의 nul."""
    def kinds(self, cmd):
        return G.classify_command(cmd, self.app, self.root)

    def test_rsync_shell_with_operands_needs_a_person(self):
        self.approve('deploy', 'approved.example')
        for cmd in ('rsync -e "ssh other.example ./deploy.sh" app approved.example:/app',
                    'rsync --rsh="ssh -i k other.example" app approved.example:/app',
                    'rsync -e "ssh -p 22 -- other" app approved.example:/app'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any(f['kind'] == 'opaque' for f in self.kinds(cmd)), self.kinds(cmd))
                self.assertNotEqual(self.bash(cmd, agent='codex')[0], 'allow')
        fs = [f for f in self.kinds('rsync -e "ssh -i k -p 22" app approved.example:/app') if f['kind'] != 'seen']
        self.assertEqual([f.get('ref') for f in fs], ['approved.example'], fs)

    def test_repo_and_plugin_script_refs_cannot_collide(self):
        home = Path(self.tmp.name) / 'home'
        plugin = home / '.claude/plugins/cache/market/devflow/1/scripts/deploy.sh'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('echo approved\n', encoding='utf-8')
        if os.name != 'nt':  # a folder name with ':' only exists outside Windows
            impostor = self.app / 'plugin-script:market/devflow/scripts/deploy.sh'
            impostor.parent.mkdir(parents=True)
            impostor.write_text('echo unrelated\n', encoding='utf-8')
            colon = self.app / 'plugin:market/devflow/scripts/deploy.sh'
            colon.parent.mkdir(parents=True)
            colon.write_text('echo unrelated\n', encoding='utf-8')
        from unittest.mock import patch
        with patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home)}):
            refs = [f.get('ref') for f in self.kinds(f'bash {plugin}') if f.get('gate') == 'deploy']
            self.assertEqual(refs, ['plugin-script:market/devflow/scripts/deploy.sh'])
            if os.name != 'nt':
                for script in ('./plugin-script:market/devflow/scripts/deploy.sh',
                               './plugin:market/devflow/scripts/deploy.sh'):
                    with self.subTest(script=script):
                        fs = self.kinds(f'bash {script}')
                        self.assertEqual([f.get('ref') for f in fs if f.get('gate') == 'deploy'], [None], fs)
                        self.assertTrue(any(f['kind'] == 'opaque' for f in fs), fs)

    def test_nul_known_hosts_only_on_windows(self):
        from unittest.mock import patch
        for cmd in ('ssh -oUserKnownHostsFile=nul host sudo true',
                    'scp -oUserKnownHostsFile=NUL app host:/app',
                    'rsync -e "ssh -oUserKnownHostsFile=nul" app host:/app'):
            with self.subTest(cmd=cmd):
                with patch.object(G, 'WINDOWS', False):
                    self.assertTrue(any(f['kind'] == 'opaque' for f in self.kinds(cmd)), self.kinds(cmd))
                with patch.object(G, 'WINDOWS', True):
                    self.assertFalse(any(f['kind'] == 'opaque' for f in self.kinds(cmd)), self.kinds(cmd))
        with patch.object(G, 'WINDOWS', False):
            self.assertFalse(any(f['kind'] == 'opaque' for f in
                                 self.kinds('ssh -oUserKnownHostsFile=/dev/null host sudo true')))


class OpaqueAllowPolicyTest(unittest.TestCase):
    """2026-11-18: 기본 allow, 기존 회귀 검사는 ask 호환 모드로도 유지."""

    build = GuardTest.build
    tearDown = GuardTest.tearDown
    bash = GuardTest.bash
    tool = GuardTest.tool
    approve = GuardTest.approve
    test_forbidden_always_denied = GuardTest.test_forbidden_always_denied
    test_push_requires_matching_approval = GuardTest.test_push_requires_matching_approval
    test_state_files_and_outside_paths_denied_for_write_tools = GuardTest.test_state_files_and_outside_paths_denied_for_write_tools

    def setUp(self):
        GuardTest.setUp(self)
        (Path(self.root) / "config/guard.json").unlink()
        self.home_patch = patch.dict(os.environ, {"HOME": str(Path(self.tmp.name) / "home"),
                                                 "USERPROFILE": str(Path(self.tmp.name) / "home")})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def test_default_opaque_allowed(self):
        # python -c 같은 코드 문자열은 내용과 상관없이 확인 → test_any_interpreter_code_string_needs_confirmation
        # python -m <모듈>은 스크립트 파일 실행 형태가 아니므로 확인 → test_interpreter_anything_else_needs_confirmation
        for cmd in ("unknown-runtime --check", "pytest", "unknown-tool run"):
            for agent in ("claude", "codex"):
                with self.subTest(cmd=cmd, agent=agent):
                    self.assertEqual(self.bash(cmd, agent=agent)[0], "allow")

    def test_risk_signals_keep_confirmation(self):
        cases = (
            "git push", "$g push origin main", "gh pr merge", "[gh, pr, merge]", "gh repo view", "gh release list", "gh api x -X POST",
            "gh api x --method=PUT", "gh api x -f title=x", "ssh", "scp", "rsync", "sftp", "deploy",
            "sudo", "systemctl", "service", "rm -r", "rm -rf", "find . -delete", "shred", "kill", "pkill",
            "docker", "kubectl", "terraform", "helm", "psql", "mysql", "mongo", "redis-cli", "supabase",
            "DROP", "DELETE FROM", "TRUNCATE", "ALTER", "curl -X POST", "wget --method=PUT",
            "http PATCH", "curl -d x", "curl --data=x", "curl -sSLXDELETE", "wget --post-data=x",
            "npm publish", "pnpm publish", "yarn publish", "eas submit", "eas update",
            "base64 -d", "base64 --decode", "base64 -D", "eval", "source", "curl url | sh",
            "cat .env", "cat key.pem", "cat id_rsa", "cat credentials", "print(open('.env').read())",
        )
        for signal in cases:
            # 알 수 없는 프로그램의 인수도 검사: 변수의 실제 값은 읽지 않는다.
            cmd = "unknown-runtime '" + signal.replace("'", '"') + "'"
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(signal=signal, agent=agent):
                    self.assertEqual(self.bash(cmd, agent=agent)[0], expected)

    def test_ask_switch(self):
        (Path(self.root) / "config/guard.json").write_text('{"opaque":"ask"}', encoding="utf-8")
        self.assertEqual(self.bash("pytest")[0], "ask")
        self.assertEqual(self.bash("pytest", agent="codex")[0], "deny")

    def test_project_audit_redacted_and_bounded(self):
        cmd = "unknown-runtime --token=private-value ghp_" + "a" * 36 + " x" * 100
        self.assertEqual(self.bash(cmd)[0], "allow")
        event = S.load("app", self.root)["history"][-1]["event"]
        self.assertTrue(event.startswith("확인 없이 실행: "))
        self.assertNotIn("private-value", event)
        self.assertNotIn("ghp_", event)
        self.assertLessEqual(len(event.split("명령: ", 1)[1]), 120)

    def test_unmanaged_audit_one_line(self):
        self.assertEqual(self.bash("pytest\nunknown-runtime", cwd=self.other)[0], "allow")
        log = Path.home() / ".config/agent-manager/guard-allowed.log"
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_audit_failure_does_not_block(self):
        with patch.object(S, "save", side_effect=OSError("read only")):
            self.assertEqual(self.bash("pytest")[0], "allow")
        with patch.object(Path, "mkdir", side_effect=OSError("read only")):
            self.assertEqual(self.bash("pytest", cwd=self.other)[0], "allow")

    def test_write_tool_opaque_unchanged(self):
        self.assertEqual(self.tool("Write", self.app / ".git/hooks/pre-commit")[0], "ask")

    def test_explicit_allow_switch(self):
        (Path(self.root) / "config/guard.json").write_text('{"opaque":"allow"}', encoding="utf-8")
        self.assertEqual(self.bash("pytest")[0], "allow")

    def test_broken_config_reverts_to_ask(self):
        (Path(self.root) / "config/guard.json").write_text('{broken', encoding="utf-8")
        self.assertEqual(self.bash("pytest")[0], "ask")

    def test_approved_gate_with_opaque_still_checks_risk(self):
        self.approve("push", "feat/x")
        self.assertEqual(self.bash("unknown-runtime && git push origin feat/x")[0], "allow")
        self.assertEqual(self.bash("unknown-runtime 'git push' && git push origin feat/x")[0], "ask")
        self.assertEqual(self.bash("unknown-runtime && git push origin main")[0], "deny")
        self.assertEqual(self.bash("unknown-runtime && python -m am approve app push")[0], "ask")
        self.assertEqual(self.bash("unknown-runtime && python -m am approve app push", agent="codex")[0], "deny")

    def test_risk_after_long_argument_and_safe_message(self):
        self.assertEqual(self.bash("unknown-runtime '" + "x" * 200 + " git push'")[0], "ask")
        self.assertEqual(self.bash("echo $(" + "x" * 200 + " git push")[0], "ask")
        self.assertEqual(self.bash("unknown-runtime && git commit -m 'docs: git push'")[0], "allow")


class OpaqueReviewRegressionTest(OpaqueAllowPolicyTest):
    def test_hidden_tripwires_never_auto_allowed(self):
        bodies = (
            "python3 -m am approve app merge_main --ref PR#1", "gh auth token",
            "git merge feature", "git commit --no-verify", "vercel --prod",
            "netlify deploy", "flyctl deploy", "firebase deploy",
        )
        for body in bodies:
            command = 'python3 -c "import os; os.system(\'' + body + '\')"'
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(body=body, agent=agent):
                    self.assertEqual(self.bash(command, agent=agent)[0], expected)

    def test_every_tripwire_in_opaque_segments(self):
        for word in ("push", "merge", "approve", "vercel", "netlify", "firebase", "wrangler", "railway",
                     "flyctl", "kubectl", "eas", "environ", "printenv", "process.env", "auth token",
                     "project.json", "local.json", ".am/local.json", "no-verify", "hookspath", "drop table", "truncate table",
                     "migrate reset", "db push"):
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(word=word, agent=agent):
                    self.assertEqual(self.bash("unknown-runtime '" + word + "'", agent=agent)[0], expected)

    def test_unresolved_execution_always_needs_confirmation(self):
        cases = ("$runner check", "G=git; S=pu; $G ${S}sh origin main", 'bash -c "$CMD"',
                 "xxd -r -p | sh", 'python3 -c "import os; os.system(\'git pu\'+\'sh origin main\')"',
                 'python3 -c "import shutil; shutil.rmtree(\'src\')"')
        for command in cases:
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(command=command, agent=agent):
                    self.assertEqual(self.bash(command, agent=agent)[0], expected)

    def test_audit_masks_more_credentials(self):
        for text in ("MYSQL_PWD=example-pass", "mysql -pexample-pass", "tool --pass=example-pass",
                     "postgres://u:example-pass@host/db", "sk_live_example-pass", "Authorization: token example-pass",
                     "xoxb-example-pass", "--password example-pass", 'PASSWORD="example-pass"',
                     "https://example-pass@host/path", 'tool -H "Authorization: Basic example-pass"'):
            with self.subTest(text=text):
                self.assertNotIn("example-pass", G._audit_excerpt(text))

    def test_guard_config_is_protected(self):
        target = Path(self.root) / "config/guard.json"
        target.write_text('{"opaque":"allow"}', encoding="utf-8")
        for tool in ("Write", "Edit"):
            self.assertEqual(self.tool(tool, target)[0], "deny")
        for command in ("rm config/guard.json", "rm -rf config", "echo x > config/guard.json",
                        "sed -i s/allow/ask/ config/guard.json"):
            self.assertEqual(self.bash(command, cwd=self.root)[0], "deny", command)
        self.assertEqual(self.tool("Read", target)[0], "allow")

    def test_guard_config_protected_case_insensitively(self):
        # macOS·Windows 파일 시스템은 대소문자를 구분하지 않아 Guard.JSON도 같은 파일이다.
        target = Path(self.root) / "config/guard.json"
        target.write_text('{"opaque":"allow"}', encoding="utf-8")
        for command in ("rm config/Guard.JSON", "rm CONFIG/guard.json", "rm ./config/../config/GUARD.json",
                        "rm -rf Config", "mv config/guard.JSON x.json", "echo x > Config/Guard.json"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, cwd=self.root)[0], "deny")
        for tool in ("Write", "Edit"):
            self.assertEqual(self.tool(tool, Path(self.root) / "CONFIG/Guard.Json")[0], "deny")

    def test_audit_masks_option_and_named_credentials(self):
        for text in ("mysql -u root -p hunter2xyz", "mysql -u root:hunter2xyz db", "curl -u admin:hunter2xyz https://x",
                     "curl --user admin:hunter2xyz https://x", "DB_DSN=postgres-hunter2xyz", "Cookie: session=hunter2xyz",
                     'curl -H "Cookie: session=hunter2xyz"', "PRIVATE_KEY=hunter2xyz", "glpat-hunter2xyz",
                     "AIzaSyhunter2xyz", "MY_AUTH=hunter2xyz", "SERVICE_KEY=hunter2xyz", "APP_PWD=hunter2xyz"):
            with self.subTest(text=text):
                self.assertNotIn("hunter2xyz", G._audit_excerpt(text))

    def test_stdin_and_dynamic_programs_need_confirmation(self):
        cases = ('echo "git pu""sh" | bash /dev/stdin', 'echo "git pu""sh" | sh /dev/fd/0',
                 "echo 'print(1)' | python3 -", "echo 'print(1)' | python3", "unknown-runtime | python3",
                 "unknown-runtime | node", "unknown-runtime | bash -s", "unknown-runtime | perl",
                 """awk 'BEGIN{system("touch x")}'""", """awk 'BEGIN{print "x" | "sh"}'""",
                 """node -e "require('fs').rmSync('src', {recursive: true})" """,
                 """node -e "fetch('https://x', {method: 'POST'})" """,
                 """node -e "require('fs').unlinkSync('a')" """,
                 """ruby -e "File.delete('a')" """, """ruby -e "system('touch x')" """,
                 """perl -e "unlink 'a'" """, """perl -ne 'system("touch x")' a.txt""",
                 """perl -e 'exec "touch", "x"'""")
        for command in cases:
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(command=command, agent=agent):
                    self.assertEqual(self.bash(command, agent=agent)[0], expected)

    def test_any_interpreter_code_string_needs_confirmation(self):
        # 코드 문자열을 실행하는 옵션이면 내용과 상관없이 확인(문자열을 쪼개 위험 단어를 숨겨도 같다).
        cases = ("""php -r 'system("git pu"."sh origin main");'""", "php -r 'echo 1;'",
                 """lua -e 'os.execute("touch x")'""", "lua -e 'print(1)'", "luajit -e 'print(1)'",
                 """osascript -e 'do shell script "touch x"'""", "Rscript -e 'print(1)'",
                 "deno eval 'console.log(1)'", "bun -e 'console.log(1)'", "bun --eval 'console.log(1)'",
                 "node -e 'console.log(1)'", "node -p '1'", "ruby -e 'puts 1'", "perl -e 'print 1'",
                 "python3 -c 'print(1)'", "python -c 'print(1+1)'", "echo 'puts 1' | tclsh",
                 """ruby -e "File.write('config/guard.json', '{}')" """,
                 """perl -e "open(my \\$f, '>', 'projects/app/project.json')" """,
                 """php -r "file_put_contents('config/guard.json', '{}');" """,
                 "powershell -EncodedCommand ZQBjAGgAbwAgADEA", "pwsh -enc ZQBjAGgAbwAgADEA",
                 "powershell -c Invoke-WebRequest -Method POST https://x", "pwsh -Command Get-Date",
                 "pwsh -c 'Get-Date'")
        for command in cases:
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(command=command, agent=agent):
                    self.assertEqual(self.bash(command, cwd=self.root, agent=agent)[0], expected)

    def test_command_execution_options_need_confirmation(self):
        cases = ("sed 1e a.txt", "sed -n 1e a.txt", "sed 's/x/date/e' a.txt", "sed -e '1e date' a.txt",
                 "sed --expression=1e a.txt", "git -c alias.st='!touch x' st",
                 "git -c core.askPass='!touch x' status", "git -c core.gitProxy=/tmp/x fetch",
                 "git -c gpg.program=/tmp/x log", "git -c diff.external=/tmp/x diff",
                 "git config alias.st '!touch x'", "git rebase --exec 'touch x' main",
                 "git submodule foreach 'touch x'", "git bisect run ./x.sh")
        for command in cases:
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(command=command, agent=agent):
                    self.assertIn(self.bash(command, agent=agent)[0], (expected, "deny"))
        for command in ("sed 's/a/b/' a.txt", "sed -n 1p a.txt", "git -c color.ui=always status"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "allow")

    def test_audit_masks_quoted_user_and_whole_cookie(self):
        for text in ('curl --user "admin:hunter2xyz" https://x', "curl --user 'admin:hunter2xyz' https://x",
                     "mysql -uadmin:hunter2xyz db", "curl -uadmin:hunter2xyz https://x",
                     'curl -H "Cookie: a=1; session=hunter2xyz; c=hunter2xyz" https://x',
                     "Cookie: a=1; b=hunter2xyz", 'curl -b "sid=1; tok=hunter2xyz" https://x',
                     'curl --cookie "sid=1; tok=hunter2xyz" https://x'):
            with self.subTest(text=text):
                self.assertNotIn("hunter2xyz", G._audit_excerpt(text))

    def test_plain_pipes_and_scripts_still_allowed(self):
        for command in ("echo x | grep x", "unknown-runtime | grep x"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "allow")

    def commit_on_main(self, rel, text):
        path = self.app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(self.app, "add", rel)
        git(self.app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add " + rel)
        fetched_baseline(self.app)
        return path

    def test_interpreter_runs_only_a_clean_tracked_script_without_confirmation(self):
        # 인터프리터는 '추적·미수정 스크립트 파일 하나 실행'으로 확실히 판명될 때만 기존 판정
        script = self.commit_on_main("tools/ok.py", "print(1)\n")
        self.commit_on_main("tools/ok.js", "console.log(1)\n")
        for command in ("python3 tools/ok.py", "python3 tools/ok.py --flag x", "cat a.txt | python3 tools/ok.py",
                        "node tools/ok.js", "python3 --version", "node -v"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "allow")
        script.write_text("print(2)\n", encoding="utf-8")  # 수정된 스크립트는 확인
        self.assertEqual(self.bash("python3 tools/ok.py")[0], "ask")

    def test_interpreter_anything_else_needs_confirmation(self):
        (self.app / "new.py").write_text("print(1)\n", encoding="utf-8")
        cases = ("php -r'echo 1;'", "php --run='echo 1;'", "php -d k=v", "php -d k=v x.php", "lua -l math",
                 "osascript -l JavaScript", "python3", "node", "tclsh", "echo x | ruby", "python3 new.py",
                 "python3 -u tools/ok.py", "python -m tools.check", "Rscript x.R", "pwsh -File x.ps1",
                 "awk '{print $1}' a.txt", "gawk 'BEGIN{print 1}'", "mawk -f x.awk a.txt", "deno run x.ts",
                 "bun x.ts", "perl x.pl", "lua x.lua", "python3 - <<'EOF'\nprint(1)\nEOF")
        for command in cases:
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(command=command, agent=agent):
                    self.assertEqual(self.bash(command, agent=agent)[0], expected)

    def test_more_command_execution_options_need_confirmation(self):
        cases = ("rg --pre ./x foo", "rg --pre=./x foo", "rg --pre-glob '*.gz' foo", "git grep -O foo",
                 "git grep -Ovim foo", "git grep --open-files-in-pager foo", "git grep --open-files-in-pager=vim foo",
                 "man -P 'touch x' ls", "man --pager='touch x' ls", "less '+!touch x' a.txt", "less '+|touch x' a.txt",
                 "find . -exec touch x ';'", "find . -ok touch x ';'", "echo a | xargs touch",
                 "tar --to-command='touch x' -xf a.tar", "tar --use-compress-program=./x -cf a.tar .",
                 "tar -I ./x -cf a.tar .", "tar --checkpoint-action=exec=touch -cf a.tar .",
                 "rsync -e 'touch x' a b", "rsync --rsh='touch x' a b", "rsync -avze 'touch x' a b",
                 "rsync --rsync-path='touch x' a host:b")
        for command in cases:
            for agent, expected in (("claude", "ask"), ("codex", "deny")):
                with self.subTest(command=command, agent=agent):
                    self.assertIn(self.bash(command, agent=agent)[0], (expected, "deny"))

    def test_audit_masks_whole_argument_after_marker(self):
        for text in ('curl --user "admin:word leakprobe" https://x', 'curl --user "admin:word;leakprobe" https://x',
                     "curl --user 'admin:word leakprobe' https://x", "curl -u'admin:word leakprobe' https://x",
                     'curl -H "Authorization: Digest username=alice, response=leakprobe" https://x',
                     'curl -H "Proxy-Authorization: Basic leakprobe" https://x',
                     "curl -H 'Proxy-Authorization: Negotiate a leakprobe' https://x",
                     'curl -H "Cookie: a=1; b=c leakprobe" https://x', 'tool --password "word leakprobe"',
                     'DB_PASSWORD="word leakprobe" tool', "tool --token='a leakprobe'"):
            with self.subTest(text=text):
                masked = G._audit_excerpt(text)
                self.assertNotIn("leakprobe", masked)
                self.assertNotIn("word", masked.replace("password", "").replace("PASSWORD", ""))


class DefaultModeAllowlistTest(OpaqueReviewRegressionTest):
    """기본(allow) 모드에서 자동 허용은 '위험이 낮다고 명시한 사유 코드'의 opaque에만 적용된다."""

    def both(self, command, cwd=None):
        return tuple(self.bash(command, cwd=cwd, agent=agent)[0] for agent in ("claude", "codex"))

    def test_same_line_replacement_still_needs_confirmation(self):
        # T7: 같은 명령줄에서 신뢰한 파일을 바꿔치기하면 기본 모드에서도 확인
        safe = '--- /dev/null\n+++ b/safe.md\n@@ -0,0 +1 @@\n+ok\n'
        evil = '--- /dev/null\n+++ b/projects/x/project.json\n@@ -0,0 +1 @@\n+{}\n'
        self.commit_on_main("safe.patch", safe)
        (self.app / "evil.patch").write_text(evil, encoding="utf-8")
        self.commit_on_main("check.py", "print(1)\n")
        self.assertEqual(self.bash("python3 check.py")[0], "allow")
        for command in ("cp evil.patch safe.patch && git apply safe.patch", "echo x > check.py && python3 check.py",
                        "cp evil.patch safe.patch; git apply safe.patch"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))

    def test_attached_sed_expressions_need_confirmation(self):
        for command in ("sed -e'1e date' a.txt", "sed -e's/x/date/e' a.txt", "sed -e1e a.txt",
                        "sed --expression='1e date' a.txt", "sed --expression=s/x/date/e a.txt", "sed -ne'1e date' a.txt",
                        "sed -n -e '1e date' a.txt"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))
        for command in ("sed -e's/x/y/' a.txt", "sed -n -e 1p a.txt", "sed -n 's/date/x/p' a.txt"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "allow")

    def test_git_external_program_options_need_confirmation(self):
        for command in ("git grep -nOcat pattern", "git grep -O pattern", "git grep -iO pattern",
                        "git grep --open-files-in-pager=cat pattern", "git diff --ext-diff", "git diff --textconv",
                        "git log --textconv -p", "git show --ext-diff HEAD", "git difftool --extcmd=./formatter HEAD",
                        "git difftool --extcmd ./formatter HEAD", "git difftool -x ./formatter HEAD"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))
        for command in ("git grep -n pattern", "git diff --no-ext-diff", "git log --no-textconv -p"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "allow")


    def test_same_line_write_cancels_clean_script_for_every_language(self):
        self.commit_on_main("tools/check.js", "require('./helper')\n")
        self.commit_on_main("tools/helper.js", "module.exports = 1\n")
        self.commit_on_main("tools/check.py", "print(1)\n")
        (self.app / "replacement.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(self.bash("node tools/check.js")[0], "allow")
        for command in ("cp replacement.txt tools/check.js && node tools/check.js",
                        "cp replacement.txt tools/helper.js && node tools/check.js",
                        "cp replacement.txt tools/helper.js; node tools/check.js",
                        "echo x > tools/check.py && python3 tools/check.py",
                        "node tools/check.js && cp replacement.txt tools/helper.js"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))

    def test_wrapped_or_versioned_stdin_programs_need_confirmation(self):
        for name in ("code.py", "code.js", "code.sh"):
            (self.app / name).write_text("x\n", encoding="utf-8")
        for command in ("cat code.py | uv run python -", "cat code.js | node22", "node22 < code.js",
                        "cat code.sh | busybox sh", "cat code.py | pnpm exec python3 -", "uv run python < code.py",
                        "cat code.py | python3.12", "cat code.py | env python3 -", "busybox sh < code.sh",
                        "cat code.sh | npx bash /dev/stdin"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))

    def test_wrapped_interpreter_only_runs_a_script_right_after_its_name(self):
        # 형태 고정: 래퍼·버전 이름 뒤 인터프리터·셸은 '이름 바로 다음이 추적·미수정 스크립트 경로'일 때만 기존 판정
        self.commit_on_main("tools/ok.py", "print(1)\n")
        self.commit_on_main("tools/ok.js", "console.log(1)\n")
        self.commit_on_main("tools/ok.sh", "echo 1\n")
        for name in ("code.py", "code.sh", "helper.js", "init.sh"):
            (self.app / name).write_text("x\n", encoding="utf-8")
        for command in ("cat code.py | uv run python3 -X dev", "busybox sh -o errexit < code.sh",
                        "node22 --require ./helper.js -e 'console.log(1)'", "uv run bash --rcfile ./init.sh -c 'echo 1'",
                        "uv run python3 -X dev tools/ok.py", "node22 --require ./helper.js tools/ok.js",
                        "busybox sh -e tools/ok.sh", "uv run python -m tools.ok", "pnpm exec node --import ./helper.js tools/ok.js",
                        "uv run python tools/ok.py < code.py", "cat code.py | uv run python tools/ok.py",
                        "uv run python code.py", "node22 helper.js"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))
        (self.app / "code.py").unlink()
        (self.app / "helper.js").unlink()
        for name in ("code.sh", "init.sh"):
            (self.app / name).unlink()
        for command in ("uv run python tools/ok.py", "uv run python tools/ok.py --flag -x", "node22 tools/ok.js",
                        "busybox sh tools/ok.sh", "timeout 60 python -m unittest discover -s tests"):
            with self.subTest(command=command):
                self.assertNotEqual(self.bash(command, agent="codex")[0], "deny")

    def test_low_risk_codes_do_not_cover_these(self):
        # 허용 목록 사유가 붙을 만한 입력이라도 기본 모드에서 확인(또는 차단)
        self.commit_on_main("tools/check.py", "import helper\n")
        self.commit_on_main("tools/helper.py", "x = 1\n")
        self.assertEqual(self.bash("python3 tools/check.py")[0], "allow")
        (self.app / "tools/helper.py").write_text("open('x', 'w')\n", encoding="utf-8")  # 수정된 형제 모듈
        self.assertEqual(self.both("python3 tools/check.py"), ("ask", "deny"))
        (self.app / "tools/helper.py").write_text("x = 1\n", encoding="utf-8")
        (self.app / "other_mod.py").write_text("x = 2\n", encoding="utf-8")  # 추적 안 된 같은 언어 파일
        self.assertEqual(self.both("python3 tools/check.py"), ("ask", "deny"))
        for command in ("uv run python -c 'print(1)'", "php8.3 -r 'echo 1;'", "busybox sh -c 'touch marker'",
                        "pnpm exec python3 -c 'print(1)'", "npx node -e 'console.log(1)'", "env bash -c 'echo 1'",
                        "nice python3.12 -c 'print(1)'", "timeout 5 node22 -e 'console.log(1)'",
                        "uv run python -c \"open('config/guard.json','w').write('{}')\"", "bunx ruby3.2 -e 'puts 1'",
                        "yarn exec perl -e 'print 1'", "uv run bash -lc 'echo 1'"):
            with self.subTest(command=command):
                self.assertEqual(self.both(command), ("ask", "deny"))
        for command in ("cd ../other && touch marker", "cd ../other; echo x > marker", "cd ../other && unknown-runtime"):
            with self.subTest(command=command):
                self.assertNotEqual(self.bash(command)[0], "allow")
                self.assertEqual(self.bash(command, agent="codex")[0], "deny")
        self.assertFalse((self.other / "marker").exists())
        for command in ("cd ../other && git status", "cd ../other && ls"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "allow")


class DefaultModeShadowTest(unittest.TestCase):
    """기존 검사를 원래 환경(HOME 그대로) 그대로 돌리며 기본 모드 판정을 함께 본다."""

    def test_auto_allow_only_for_low_risk_reason_codes(self):
        # 이전 검사들(T6·T7·빌드 허용 목록)을 그대로 돌리면서, 같은 입력을 기본(allow) 모드로도 판정한다.
        # 기본 모드에서만 allow가 된 판정은 모든 opaque 사유가 허용 목록 코드여야 한다.
        flipped, original = [], G.decide

        def shadow(payload, agent="claude", root=None):
            result = original(payload, agent, root)
            if result[0] == "allow":
                return result
            seen = []
            with patch.object(G, "_opaque_policy", return_value="allow"), \
                    patch.object(G, "_record_opaque_allow", side_effect=lambda c, o, *a: seen.append(o)), \
                    patch.object(S, "save"), patch.object(S, "event"), patch.object(S, "add_request"):
                default = original(payload, agent, root)
            if default[0] == "allow":
                flipped.append((payload["tool_input"], agent, [f.get("code") for opaque in seen for f in opaque]))
            return result

        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (
            T7FalsePositiveTest, T6SecurityReviewTest, T6Round3Test, T6Round4Test, T6Round5Test,
            BuildAllowlistTest, VerifierRound4Test, LowRiskCodeMisuseCases))
        result = unittest.TestResult()
        with patch.object(G, "decide", shadow):
            suite.run(result)
        self.assertEqual(result.failures + result.errors, [])
        self.assertTrue(flipped)  # 자동 허용 자체는 동작한다(위험 낮은 흔한 경우)
        for tool_input, agent, codes in flipped:
            with self.subTest(command=tool_input, agent=agent):
                self.assertTrue(codes)
                self.assertTrue(set(codes) <= G.LOW_RISK_CODES, codes)
        # 허용 목록 사유(clean_script·unknown_program·unlisted_build·outside_cd)가 붙을 만한 입력이라도 기본 모드에서 확인
        misuse = set(LowRiskCodeMisuseCases.COMMANDS) | clean_script_misuse_commands()
        self.assertEqual(len(clean_script_misuse_commands()), len(G.SCRIPT_INTERPRETERS) * 4 * len(SAME_LINE))
        self.assertEqual([t for t, _, _ in flipped if t.get("command") in misuse], [])


# clean_script 사례 × 실행 형태(래퍼 없음·버전 이름·uv run·timeout 60): 한 경로만 판정 함수를 빠뜨리는 일이 없게 곱해서 본다
# 언어별 import·require 예시(대표 확장자). 여기 없는 인터프리터는 그 언어의 첫 확장자로 자동 생성된다 —
# 가드는 스크립트 내용을 실행하지 않으므로 형제 파일과 같은 언어 소스가 '있다·바뀌었다'는 상태만 중요하다.
IMPORT_EXAMPLES = {
    "python": ("import helper\n", "x = 1\n"), "node": ("require('./helper')\n", "module.exports = 1\n"),
    "php": ("<?php require __DIR__ . '/helper.php';\n", "<?php $x = 1;\n"), "lua": ("require('helper')\n", "return 1\n"),
    "ruby": ("require_relative 'helper'\n", "X = 1\n"), "perl": ("require './helper.pl';\n", "1;\n"),
    "sh": (". ./helper.sh\n", "x=1\n"), "bash": ("source ./helper.sh\n", "x=1\n"), "tclsh": ("source helper.tcl\n", "set x 1\n"),
    "rscript": ("source('helper.R')\n", "x <- 1\n"), "pwsh": (". ./helper.ps1\n", "$x = 1\n"),
}
FORM_PREFIX = {"deno": "run ", **{a: "-f " for a in G.AWKS}}  # 스크립트 앞에 꼭 필요한 하위 명령·옵션


def clean_script_langs():
    """가드의 단일 상수 SCRIPT_INTERPRETERS의 모든 이름 → (스크립트, 형제 소스, 내용, 형제 내용, 같은 언어 미추적 파일,
    실행 형태: 래퍼 없음·버전 이름·uv run·timeout 60)."""
    langs = {}
    for name in sorted(G.SCRIPT_INTERPRETERS):
        ext = G.LANG_SOURCES[name][0]
        sbody, hbody = IMPORT_EXAMPLES.get(name, ("# check\n", "# helper\n"))
        pre = FORM_PREFIX.get(name, "")
        langs[name] = (f"tools/{name}/check{ext}", f"tools/{name}/helper{ext}", sbody, hbody, f"extra{ext}",
                       tuple(f.format(n=name, p=pre) + "{s}" for f in
                             ("{n} {p}", "{n}9 {p}", "uv run {n} {p}", "timeout 60 {n} {p}")))
    return langs


CLEAN_SCRIPT_LANGS = clean_script_langs()
SAME_LINE = ("cp replacement.txt {s} && {cmd}", "cp replacement.txt {h} && {cmd}", "cp replacement.txt {h}; {cmd}",
             "{cmd} && cp replacement.txt {h}")


def clean_script_misuse_commands():
    """같은 줄 쓰기가 붙은 명령 문자열(상태와 상관없이 항상 확인) — 그림자 검사가 기본 모드 허용이 아닌지 본다."""
    return {t.format(s=s, h=h, cmd=v.format(s=s)) for s, h, _, _, _, forms in CLEAN_SCRIPT_LANGS.values()
            for v in forms for t in SAME_LINE}


def clean_script_matrix(test, decide, names=None):
    """→ [(사례, 명령, 판정)]. 사례: clean(허용돼야 함) / same-line·modified-sibling·untracked-source(확인돼야 함)."""
    results = []
    (test.app / "replacement.txt").write_text("x\n", encoding="utf-8")
    for name, (script, helper, sbody, hbody, extra, forms) in CLEAN_SCRIPT_LANGS.items():
        if names and name not in names:
            continue
        for rel, text in ((script, sbody), (helper, hbody)):
            if not (test.app / rel).exists():  # 같은 테스트에서 다시 돌 때는 이미 원격 main에 있다
                DefaultModeAllowlistTest.commit_on_main(test, rel, text)
        for form in forms:
            cmd = form.format(s=script)
            results.append(("clean", cmd, decide(cmd)))
            results += [("same-line", line, decide(line))
                        for line in (t.format(s=script, h=helper, cmd=cmd) for t in SAME_LINE)]
        (test.app / helper).write_text(hbody + "\n", encoding="utf-8")  # 수정된 형제 소스
        results += [("modified-sibling", f.format(s=script), decide(f.format(s=script))) for f in forms]
        (test.app / helper).write_text(hbody, encoding="utf-8")
        (test.app / extra).write_text(hbody, encoding="utf-8")
        results += [("untracked-source", f.format(s=script), decide(f.format(s=script))) for f in forms]
        (test.app / extra).unlink()
    return results


class CleanScriptMatrixTest(OpaqueReviewRegressionTest):
    """기본(allow) 모드: 모든 실행 형태가 clean일 때만 허용, 나머지 사례는 확인(Codex 차단)."""

    def test_every_language_and_form_goes_through_the_same_clean_script_check(self):
        # 가드 상수의 모든 인터프리터 × 실행 형태 4종 × 사례 7종. Codex는 같은 판정(확인→차단)이라 대표 언어만 함께 본다.
        self.assertEqual(set(CLEAN_SCRIPT_LANGS), set(G.SCRIPT_INTERPRETERS))
        self.assertTrue({"php", "lua", "sh", "awk", "pwsh", "rscript", "tclsh"} <= set(CLEAN_SCRIPT_LANGS))
        results = clean_script_matrix(self, lambda cmd: self.bash(cmd)[0])
        self.assertEqual(len(results), len(CLEAN_SCRIPT_LANGS) * 4 * (1 + len(SAME_LINE) + 2))
        for case, cmd, decision in results:
            with self.subTest(case=case, command=cmd):
                self.assertEqual(decision, "allow" if case == "clean" else "ask")
        codex = clean_script_matrix(self, lambda cmd: self.bash(cmd, agent="codex")[0], names={"php", "lua"})
        for case, cmd, decision in codex:
            with self.subTest(agent="codex", case=case, command=cmd):
                self.assertEqual(decision, "allow" if case == "clean" else "deny")


class LowRiskCodeMisuseCases(GuardTest):
    """확인(ask) 설정에서 확인·차단인 입력 — DefaultModeShadowTest가 기본 모드에서도 허용되지 않는지 본다."""
    COMMANDS = ("python3 tools/check.py", "uv run python -c 'print(1)'", "php8.3 -r 'echo 1;'",
                "busybox sh -c 'touch marker'", "pnpm exec python3 -c 'print(1)'", "cd ../other && touch marker",
                "cp tools/helper.py tools/check.py && python3 tools/check.py", "cat code.py | uv run python -",
                "node22 < code.js")

    def test_misuse_cases_need_a_person(self):
        DefaultModeAllowlistTest.commit_on_main(self, "tools/check.py", "import helper\n")
        DefaultModeAllowlistTest.commit_on_main(self, "tools/helper.py", "x = 1\n")
        (self.app / "tools/helper.py").write_text("open('x', 'w')\n", encoding="utf-8")
        for command in self.COMMANDS:
            with self.subTest(command=command):
                self.assertNotEqual(self.bash(command)[0], "allow")

    def test_clean_script_matrix_misuse(self):
        # clean_script 사례 × 래퍼 없음·버전 이름·uv run·timeout 60 — 확인 설정에서 모두 허용 아님, 그림자 검사가 기본 모드도 본다
        # 대표 언어만(전 언어는 CleanScriptMatrixTest가 기본 모드에서 직접 본다)
        for case, cmd, decision in clean_script_matrix(self, lambda c: self.bash(c)[0],
                                                       names={"node", "python3", "php", "lua", "sh"}):
            if case != "clean":
                with self.subTest(case=case, command=cmd):
                    self.assertNotEqual(decision, "allow")


if __name__ == "__main__":
    unittest.main()
