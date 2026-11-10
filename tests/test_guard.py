"""Execution-time guard: real git repos in a temp dir, payloads shaped like Claude Code / Codex hooks."""
import contextlib
import io
import json
import os
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
        self.assertEqual(self.bash('python -c "from am import state; print(state.GATES)"')[0], "allow")  # 읽기만
        self.assertEqual(self.bash('python -c "print(1+1)"')[0], "allow")

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
        self.assertEqual(self.bash("env A=1 python -m unittest")[0], "allow")

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
        for cmd in ('echo "documentation > C:/other/file.txt"', 'python -c "from am import state; print(state.GATES)"',
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
        self.assertEqual(self.bash("npm run push")[0], "allow")
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
        self.assertEqual(self.bash("python -c \"from pathlib import Path; Path('out/a.txt').write_text('x')\"")[0], "allow")

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
        git(self.app, "remote", "add", "origin", "https://github.com/me/app.git")
        self.assertEqual(self.bash("git push origin feat/x")[0], "allow")

    def test_normal_dev_commands_not_blocked(self):
        """The verifier's sample of 11 normal commands the second net used to block."""
        for cmd in ("git log --grep push", "git log --grep merge", "git commit -m push", "git grep push", "rg push src",
                    "pytest -k push", "python -m pytest -k merge", "npm test -- --grep merge", "echo push",
                    "node -e \"console.log('push')\"", "python -c \"print('push')\""):
            self.assertEqual(self.bash(cmd)[0], "allow", cmd)
        # 매니저가 실제로 막혔던 오탐: 다른 폴더의 설정을 읽기만 하는 스크립트 (json.dumps는 쓰기가 아님)
        reader = ("python -c \"import json; d=json.load(open(r'C:/x/orca-data.json', encoding='utf-8')); "
                  "print(json.dumps(d)[:10])\"")
        self.assertEqual(self.bash(reader)[0], "allow")
        self.assertEqual(self.bash("python -c \"print(open('w.txt').read())\"")[0], "allow")  # 이름이 w로 시작해도 읽기
        self.assertEqual(self.bash(f"python -c \"open('{(self.other / 'w.txt').as_posix()}', 'w').write('x')\"")[0], "deny")
        # 매니저가 실제로 막혔던 오탐: 정규식 문자열이 들어간 heredoc 스크립트
        heredoc = "python - <<'EOF'\nimport re\nopen('out.txt', 'w').write(re.sub(r\"[/\\\\]\", \"-\", 'a/b') + '(G, \"x\")')\nEOF"
        self.assertEqual(self.bash(heredoc)[0], "allow")


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
