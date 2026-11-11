"""Execution-time guard: real git repos in a temp dir, payloads shaped like Claude Code / Codex hooks."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
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
        for body in ("from pathlib import Path; Path('projects/app/' + 'project' + '.json').write_text('{}')",
                     "from am import state as S; p=S.load('app'); S.approve(p,'push'); S.save(p)"):
            d, msg = self.bash(f'python -c "{body}"')
            self.assertEqual(d, "deny", body)
        heredoc = "python - <<'EOF'\nfrom am import state as S\np = S.load('app'); S.approve(p, 'push'); S.save(p)\nEOF"
        self.assertEqual(self.bash(heredoc)[0], "deny")
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
        heredoc = "python - <<'EOF'\nimport re\nopen('out.txt', 'w').write(re.sub(r\"[/\\\\]\", \"-\", 'a/b') + '(G, \"x\")')\nEOF"
        self.assertEqual(self.bash(heredoc)[0], "allow")


class BuildAllowlistTest(GuardTest):
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
        self.assertEqual(self.bash("python - <<'EOF'\nprint(1)\nEOF\npython - <<'EOF'\nprint(2)\nEOF")[0], "allow")

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
            self.assertEqual(self.bash("python - <<'EOF'\n" + body + "\nEOF")[0], "allow", body)

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
