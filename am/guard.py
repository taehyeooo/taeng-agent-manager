"""Execution-time permission guard — called by Claude Code / Codex PreToolUse hooks BEFORE a tool runs.

    python <manager>/am/guard.py claude|codex      (hook payload JSON on stdin)

Decisions
  forbidden → always blocked (승인 우회, 인증정보 노출, 상태 파일 위조, 관련 없는 프로젝트 수정)
  gated     → blocked unless projects/<slug>/project.json has a matching approval; the block message
              is the approval request (하려는 일·영향 범위·위험·명령) and is recorded for `am report`
  approve   → `am approve` is never self-served: Claude asks the user in its permission UI, Codex is blocked
  opaque    → what will run can't be known in advance (변수로 부르기, eval, 인코딩된 명령, 스크립트 안 subprocess):
              Claude asks the user, Codex is blocked
  else      → allowed (reading, editing inside the worktree, tests, lint, build, local commits)

Commands are interpreted, not grepped:
  - split on ; && || | & { } and newlines outside quotes; quotes removed when tokenizing
  - $(...) and `...` substitutions are analysed as commands of their own, and a command word or git/gh
    subcommand that comes from a substitution is opaque
  - wrappers are unwrapped (bash -c, powershell -Command with its ` escapes, cmd /c with its ^ escapes,
    env X=1, &, sudo, npx/pnpx/bunx/pnpm dlx/npm exec), git and gh global options are parsed
    (git -C <dir> is judged in <dir>, gh -R <repo> is compared with the project's repo), git aliases expanded
  - only the command-word position is matched, so `git commit -m "git push docs"` is not a push
A guard is a seatbelt, not a sandbox: script files and PATH tricks are invisible before they run. The backstops
are append-only approvals in CI and server-side branch protection (docs/architecture.md 5장).
Blocking uses exit code 2 + stderr, which both Claude Code and Codex treat as "deny, tell the agent why".
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # run as a script from a hook
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from am import state as S
else:
    from . import state as S

SHELLS = {"bash", "sh", "zsh", "dash", "pwsh", "powershell", "cmd"}
SHELL_CMD_FLAGS = {"-c", "-lc", "-ic", "-command", "/c", "/k", "/s"}
SCRIPTERS = {"python", "python3", "py", "node", "deno", "bun", "ruby", "perl"}
PY_OPTS_WITH_ARG = {"-X", "-W", "--check-hash-based-pycs"}
PREFIXES = {"&", ".", "call", "time", "nohup", "exec", "command", "builtin", "start", "start-process", "npx", "pnpx", "bunx"}
SHELL_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "fi", "done", "esac", "time"}
OPAQUE_WORDS = {"eval", "iex", "invoke-expression", "invoke-command", "xargs", "source"}
GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env", "--super-prefix"}
GIT_BUILTINS = {"add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "checkout", "cherry-pick",
                "clean", "clone", "commit", "config", "describe", "diff", "fetch", "format-patch", "gc", "grep", "init", "log",
                "ls-files", "ls-remote", "ls-tree", "merge", "merge-base", "mv", "notes", "pull", "push", "range-diff", "rebase",
                "reflog", "remote", "reset", "restore", "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "sparse-checkout",
                "stash", "status", "submodule", "switch", "symbolic-ref", "tag", "update-ref", "worktree", "help", "version"}
GIT_MUTATING = {"commit", "add", "push", "merge", "reset", "checkout", "switch", "rm", "mv", "stash", "apply", "am",
                "rebase", "cherry-pick", "revert", "tag", "branch", "clean", "worktree", "restore", "pull", "fetch", "update-ref"}
GIT_REWRITES_FILES = {"rm", "mv", "restore", "checkout", "reset", "stash", "apply", "am", "cherry-pick", "revert", "switch"}
GH_API_WITH_VALUE = {"-H", "--header", "-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "-q", "--jq",
                     "-t", "--template", "--cache", "-p", "--preview", "--hostname"}
GH_GLOBAL_WITH_ARG = {"-R", "--repo", "--hostname"}
MAIN = {"main", "master"}
SAFE_DELETE = {"node_modules", "dist", "build", ".next", "out", "coverage", "__pycache__", ".pytest_cache", ".turbo", ".cache", "target"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
BYPASS_FLAGS = {"--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox", "--yolo"}
SECRET_LISTERS = {"printenv", "get-childitem env:", "gci env:", "dir env:", "ls env:", "set", "export", "export -p",
                  "declare", "declare -p", "declare -x", "typeset", "typeset -p", "typeset -x"}
# commands that change the environment of everything after them on the same command line
ENV_SETTERS = {"export", "declare", "typeset", "readonly", "local", "unset", "setx", "setenv", "shopt", "alias"}
CD_WORDS = {"cd", "chdir", "pushd", "popd", "set-location", "sl", "push-location", "pop-location"}

# 쉬운 말 — 사용자에게 그대로 보여 준다
IMPACT = {
    "repo_create": ("GitHub에 새 저장소가 생긴다", "공개로 만들면 코드를 누구나 볼 수 있다"),
    "repo_visibility": ("저장소가 공개↔비공개로 바뀐다", "공개로 바꾸면 지금까지의 코드와 기록이 모두 보인다"),
    "repo_delete": ("GitHub 저장소와 그 안의 기록이 통째로 지워진다", "되살릴 수 없다"),
    "merge_main": ("작업한 코드가 완성본(main)에 들어간다", "문제가 있으면 완성본이 깨진다 (되돌리기는 가능하지만 번거롭다)"),
    "merge_local": ("작업한 코드가 완성본(main)에 바로 들어간다", "자동 검사와 리뷰를 건너뛴다"),
    "push": ("내 PC의 코드가 GitHub에 올라간다", "공개 저장소면 바로 누구나 볼 수 있고, 올린 기록은 지우기 어렵다"),
    "remote_api": ("GitHub 저장소의 내용·설정이 바뀐다", "어떤 변경인지에 따라 되돌리기 어렵다"),
    "deploy": ("서비스가 실제로 바뀐다", "실제 사용자에게 바로 영향이 가고, 비용이 생길 수 있다"),
    "db_change": ("데이터베이스의 구조나 데이터가 바뀐다", "저장된 데이터가 지워지거나 깨질 수 있다"),
    "system_permission": ("이 PC 전체 설정이 바뀐다 (이 프로젝트만이 아님)", "다른 프로그램·보안 설정에 영향을 줄 수 있다"),
    "credentials": ("비밀번호·API 키 파일(.env)을 연다", "비밀값이 대화 기록에 남을 수 있다"),
    "destructive_delete": ("파일이나 작업 기록이 지워진다", "되살리지 못할 수 있다"),
    "architecture_policy": ("프로젝트의 큰 규칙 문서(ADR)가 바뀐다", "앞으로 모든 작업이 이 규칙을 따르게 된다"),
}
SUBST = "\x00SUBST\x00"  # placeholder for a $(...)/`...` result


# ---------- helpers ----------

def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _norm(path):
    # realpath: Windows 8.3 short names (C:\Users\RUNNER~1) and symlinks/junctions → the real long path, so
    # the same folder compares equal however git, tempfile or the agent spelled it
    return os.path.normcase(os.path.realpath(str(path))).rstrip("\\/")


def _under(path, root):
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r + os.sep)


def _abs(path, cwd):
    return Path(os.path.abspath(Path(path) if os.path.isabs(path) else Path(cwd) / path))


_CACHE = {}  # per judgement (cleared by classify_command at depth 0): repo top and trusted baseline lookups


def _cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _top(cwd):
    return _cached(("top", str(cwd)), lambda: _git(cwd, "rev-parse", "--show-toplevel") or str(cwd))


def resolve_project(cwd, root=None):
    """cwd (any worktree) → slug, via the main checkout path registered in .am/local.json."""
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    main_checkout = Path(common).parent
    for slug, path in S.load_local(root)["paths"].items():
        if _norm(path) == _norm(main_checkout):
            return slug
    return None


def _seen(tag, seg):
    return {"kind": "seen", "seg": seg, "tag": tag}


def _finding(kind, seg, **kw):
    return {"kind": kind, "seg": seg, **kw}


def _gated(gate, seg, ref=None, where=None, what=None, repo=None):
    impact, risk = IMPACT[what or gate]
    return _finding("gated", seg, gate=gate, ref=ref, where=where, impact=impact, risk=risk, repo=repo)


# ---------- lexing ----------

def extract_substitutions(cmd):
    """$(...) and `...` outside single quotes → (cmd with SUBST placeholders, [inner commands], unbalanced?)."""
    out, inner, i, q = [], [], 0, None
    while i < len(cmd):
        ch = cmd[i]
        if q == "'":
            out.append(ch)
            q = None if ch == "'" else q
        elif ch == "'" and q is None:
            q = "'"
            out.append(ch)
        elif ch == '"':
            q = None if q == '"' else '"'
            out.append(ch)
        elif cmd.startswith("$(", i):
            depth, j = 1, i + 2
            while j < len(cmd) and depth:
                depth += {"(": 1, ")": -1}.get(cmd[j], 0)
                j += 1
            if depth:
                return cmd, inner, True
            inner.append(cmd[i + 2:j - 1])
            out.append(SUBST)
            i = j
            continue
        elif ch == "`":
            j = cmd.find("`", i + 1)
            if j < 0:
                return cmd, inner, True
            inner.append(cmd[i + 1:j])
            out.append(SUBST)
            i = j + 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out), inner, False


def split_commands(cmd):
    """Split on ; | & { } ( ) and newlines that are outside quotes. `&` inside a redirection (2>&1, &>file, >&2) is not a separator."""
    out, cur, q = [], [], None
    for i, ch in enumerate(cmd):
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur.append(ch)
        elif ch == "&" and ((i > 0 and cmd[i - 1] in "<>") or (i + 1 < len(cmd) and cmd[i + 1] == ">")):
            cur.append(ch)
        elif ch in ";|&\n{}()":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def tokens(seg):
    """Whitespace split outside quotes, quotes removed (backslashes kept — Windows paths)."""
    toks, cur, q, has = [], [], None, False
    for ch in seg:
        if q:
            if ch == q:
                q = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            q, has = ch, True
        elif ch.isspace():
            if cur or has:
                toks.append("".join(cur))
            cur, has = [], False
        else:
            cur.append(ch)
    if cur or has:
        toks.append("".join(cur))
    return toks


def bash_unescape(s):
    """Backslash escapes outside single quotes, as bash removes them (g\\it → git)."""
    out, q, i = [], None, 0
    while i < len(s):
        ch = s[i]
        if q == "'":
            q = None if ch == "'" else q
            out.append(ch)
        elif ch == "'" and q is None:
            q = "'"
            out.append(ch)
        elif ch == '"':
            q = None if q == '"' else '"'
            out.append(ch)
        elif ch == "\\" and i + 1 < len(s) and (q is None or s[i + 1] in '$`"\\'):
            out.append(s[i + 1])
            i += 1
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def unquoted(seg):
    """seg with quoted text blanked — for operators like > that only count outside quotes."""
    out, q = [], None
    for ch in seg:
        if q:
            out.append(" ")
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _word(tok):
    name = re.split(r"[/\\]", tok)[-1].lower()
    name = re.sub(r"\.(exe|cmd|bat|ps1)$", "", name)
    if re.fullmatch(r"(?:python|pypy)[\w.-]*", name):
        return "python"
    return "node" if name == "nodejs" else name


# ---------- command analysis ----------

BASELINE_REF = "refs/remotes/origin/main"
NO_REPLACE = "--no-replace-objects"  # git show/diff would otherwise follow `git replace` objects
# reflog subjects git itself writes when the remote-tracking ref really came from the remote
TRUSTED_REFLOG = re.compile(r"^(?:(?:fetch|pull)(?: [^\s:/\\.~][^\s:]*)*: |clone: from (?:https|ssh|git)://|clone: from [\w.-]+@[\w.-]+:|update by push$)")


def _baseline(top):
    """The remote main commit to compare against — only when it can be trusted, else "" (= ask).

    Trusted = origin is a network remote, and the newest reflog entry of refs/remotes/origin/main was
    written by fetch/pull/clone/push and points at the current value (a hand-written ref file or
    `git update-ref` leaves no such entry). Replace objects are ignored for every baseline read."""
    return _cached(("baseline", str(top)), lambda: _trusted_baseline(top))


def _trusted_baseline(top):
    base = _git(top, NO_REPLACE, "rev-parse", "--verify", "-q", BASELINE_REF + "^{commit}")
    url = _git(top, "ls-remote", "--get-url", "origin")  # after url.*.insteadOf rewriting
    if not base or not re.match(r"^(?:https|ssh|git)://|^[\w.-]+@[\w.-]+:", url):
        return ""
    sha, _, subject = _git(top, NO_REPLACE, "log", "-g", "-n1", "--format=%H%x00%gs", BASELINE_REF).partition("\0")
    return base if sha == base and TRUSTED_REFLOG.match(subject) else ""


def _clean_script(path, cwd, suffix):
    """Compare index, HEAD and actual bytes with the remote main baseline."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    p = _abs(path, cwd)
    if not top or not _under(p, top) or suffix is not None and p.suffix != suffix or not p.is_file() or p.is_symlink():
        return False
    rel = os.path.relpath(_norm(p), _norm(top)).replace("\\", "/")
    try:
        base = _baseline(top)
        if not base or _git(top, "ls-files", "-v", "--", rel) != "H " + rel:
            return False
        for args in (("diff", "--quiet", base, "HEAD", "--", rel),
                     ("diff", "--cached", "--quiet", base, "--", rel)):
            if subprocess.run(["git", "-C", top, NO_REPLACE, *args], capture_output=True, timeout=5).returncode:
                return False
        blob = subprocess.run(["git", "-C", top, NO_REPLACE, "show", base + ":" + rel], capture_output=True, timeout=5)
        return blob.returncode == 0 and blob.stdout.replace(b"\r\n", b"\n") == p.read_bytes().replace(b"\r\n", b"\n")
    except (OSError, subprocess.TimeoutExpired):
        return False


def _clean_build_inputs(cwd, family):
    top = _git(cwd, "rev-parse", "--show-toplevel")
    base = _baseline(top) if top else ""
    if not top or not base:
        return False
    paths = set()
    try:
        # tracked + untracked, and ignored entries with ignored folders collapsed (no walk through node_modules)
        for args in (("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
                     ("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"),
                     ("ls-tree", "-r", "--name-only", "-z", base)):
            result = subprocess.run(["git", "-C", top, NO_REPLACE, *args], capture_output=True, timeout=5, check=True)
            paths.update(result.stdout.decode("utf-8").split("\0"))
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return False
    pattern = (r"(^|/)(package\.json|package-lock\.json|npm-shrinkwrap\.json|\.npmrc)$" if family == "npm" else
               r"(^|/)(gradlew(?:\.bat)?|build\.gradle(?:\.kts)?|settings\.gradle(?:\.kts)?|gradle\.properties)$|(^|/)(gradle/wrapper|init\.d|buildSrc|build-logic)(/|$)")
    generated = {"node_modules", ".git", ".gradle", ".venv", "venv", "build", "dist", "coverage"}
    inputs = [p for p in paths if p and not generated.intersection(p.rstrip("/").split("/")[:-1]) and re.search(pattern, p)]
    return bool(inputs) and all(_clean_script(p, top, None) for p in inputs)


def _build_allowed(toks, cwd):
    """Narrow invocation grammar; never overrides forbidden/gated findings."""
    if not toks or not _git(cwd, "rev-parse", "--show-toplevel"):
        return False
    excluded = {"node_modules", ".git", ".gradle", ".venv", "venv", "build", "dist", "coverage"}
    if excluded.intersection(Path(os.path.relpath(cwd, _top(cwd))).parts):
        return False
    word, args = _word(toks[0]), toks[1:]
    if word == "gradlew":
        if not _clean_build_inputs(cwd, "gradle") or not _clean_script(toks[0], cwd, None):
            return False
        if not _under(_abs(toks[0], cwd), _top(cwd)):
            return False
        tasks, i = [], 0
        while i < len(args):
            a = args[i]
            if a in ("--rerun", "--rerun-tasks", "--no-daemon", "--console=plain", "--stacktrace", "--info"):
                pass
            elif a == "-p" and i + 1 < len(args):
                i += 1
                if not _under(_abs(args[i], cwd), _top(cwd)) or excluded.intersection(Path(args[i]).parts):
                    return False
            elif a in ("test", "build", "check", "compileJava", "compileTestJava", "classes", "testClasses", "assemble"):
                tasks.append(a)
            else:
                return False
            i += 1
        return bool(tasks)
    if word == "npm":
        if not _clean_build_inputs(cwd, "npm"):
            return False
        if args in (["ci"], ["install"], ["i"]):  # lockfile install from unchanged package files
            return True
        name = "test" if args[:1] == ["test"] else args[1] if args[:1] in (["run"], ["run-script"]) and len(args) == 2 else None
        if name not in ("test", "build", "lint"):
            return False
        if args[:1] == ["test"] and len(args) > 1 and args[1] != "--":
            return False
        package = _abs("package.json", cwd)
        if not _under(package, _top(cwd)):
            return False
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
            return isinstance(scripts, dict) and isinstance(scripts.get(name), str)
        except (OSError, ValueError, AttributeError):
            return False
    if word == "npx":
        return args == ["vitest", "run"] and _clean_build_inputs(cwd, "npm")
    if word in ("python", "python3", "py"):
        if args[:2] == ["-m", "unittest"]:
            return _unittest_args(args[2:], cwd)
        return bool(args and _clean_script(args[0], cwd, ".py") and _clean_siblings(args[0], cwd))
    if word == "sh":
        return bool(args and _clean_script(args[0], cwd, ".sh"))
    return False


def _unittest_args(args, cwd):
    """python -m unittest [discover] with options and dotted names only; folders must be inside this work tree."""
    top, i = _top(cwd), 0
    while i < len(args):
        a = args[i]
        if a in ("-s", "-t", "--start-directory", "--top-level-directory"):
            if i + 1 >= len(args) or not _under(_abs(args[i + 1], cwd), top):
                return False
            i += 1
        elif a in ("-p", "--pattern", "-k"):
            if i + 1 >= len(args) or SUBST in args[i + 1]:
                return False
            i += 1
        elif a not in ("discover", "-v", "-q", "-b", "-c", "-f", "--verbose", "--quiet", "--buffer", "--catch",
                       "--failfast", "--locals") and not re.fullmatch(r"[A-Za-z_]\w*(?:\.\w+)*", a):
            return False
        i += 1
    return True


def _clean_siblings(path, cwd):
    """python puts the script's folder first on sys.path: no untracked, ignored or changed .py next to it."""
    top = _top(cwd)
    folder = os.path.relpath(_norm(_abs(path, cwd).parent), _norm(top)).replace("\\", "/")
    spec = (folder + "/*.py") if folder != "." else "*.py"
    base = _baseline(top)
    others = _git(top, "ls-files", "--others", "-z", "--", spec)  # ignored files count too
    flags = _git(top, "ls-files", "-v", "--", spec).splitlines()
    if not base or others or any(not f.startswith("H ") for f in flags):
        return False
    try:
        return subprocess.run(["git", "-C", top, NO_REPLACE, "diff", "--quiet", base, "--", spec],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _python_write_paths(body):
    """Resolve literal/variable Path and open targets, not file content strings."""
    tree = ast.parse(body)
    values, paths, path_names = {}, [], set()

    def name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return name(node.value) + "." + node.attr
        return ""

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.Call) and node.args and name(node.func) in ("Path", "pathlib.Path", "str"):
            return value(node.args[0])
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if left is not None and right is not None:
                if isinstance(node.op, ast.Div):
                    return str(Path(left) / right)
                if isinstance(node.op, ast.Add):
                    return left + right
        return None

    def is_path(node):
        return (isinstance(node, ast.Call) and name(node.func) in ("Path", "pathlib.Path") or
                isinstance(node, ast.Name) and node.id in path_names or
                isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and is_path(node.left))

    def walk(node):
        yield node
        for child in ast.iter_child_nodes(node):
            yield from walk(child)

    for node in walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value(node.value)
                    if is_path(node.value):
                        path_names.add(target.id)
                    else:
                        path_names.discard(target.id)
        if not isinstance(node, ast.Call):
            continue
        call = name(node.func)
        if call in ("open", "io.open", "builtins.open", "codecs.open") or isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            builtin = call in ("open", "io.open", "builtins.open", "codecs.open")
            mode_index = 1 if builtin else 0
            mode = node.args[mode_index] if len(node.args) > mode_index else ast.Constant("r")
            mode = next((k.value for k in node.keywords if k.arg == "mode"), mode)
            m = value(mode)
            if m is None or any(c in m for c in "wax+"):
                target = (node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "file"), None)) if builtin else node.func.value
                paths.append(value(target))
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ("write_text", "write_bytes", "unlink", "rename", "replace"):
            # str.replace edits content; Path.replace moves a file.
            if node.func.attr == "replace" and not is_path(node.func.value):
                continue
            paths.append(value(node.func.value))
            if node.func.attr in ("rename", "replace") and node.args:
                paths.append(value(node.args[0]))
    return paths


def _check_script(body, seg, cwd, root, python=False):
    """Inline python -c / node -e / heredoc bodies."""
    out = []
    touches_am = re.search(r"\bfrom\s+am\b|\bimport\s+am\b|\bam\.(state|guard|cli)\b", body)
    if python:
        try:
            touches_am = (touches_am and re.search(r"\b(importlib|__import__|runpy|exec|eval)\b", body)) or any(
                isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "am" or
                isinstance(n, ast.Import) and any(a.name.split(".")[0] == "am" for a in n.names)
                for n in ast.walk(ast.parse(body)))
        except (SyntaxError, ValueError):
            pass  # keep the textual safety net even when parsing fails
    # writes: open(..., 'w'/'a'/'x'/'+' mode) — the mode argument, not a file name starting with w; json.dump( not json.dumps(
    writes = re.search(r"write_text|write_bytes|open\s*\([^)]*,\s*(mode\s*=\s*)?['\"][rbt]*[wax+]|writeFile|appendFile|json\.dump\s*\("
                       r"|unlink|rename|\.replace\s*\(\s*[^)]*[/\\]|rmtree|copyfile|\.save\s*\(", body)
    if touches_am and (writes or re.search(r"approve\s*\(|\.save\s*\(|save_local|approvals|add_request", body)):
        out.append(_finding("forbidden", seg, reason="스크립트로 승인·상태를 직접 조작(위조 위험)"))
    if python:
        try:
            tree = ast.parse(body)
            # This is deliberately not a Python evaluator. Flow-dependent paths and
            # calls outside this small grammar cannot establish a safe write target.
            safe_calls = {"open", "print", "str", "set", "Path", "pathlib.Path", "io.open",
                          "builtins.open", "codecs.open", "json.dumps", "json.loads", "re.sub"}
            safe_methods = {"write_text", "write_bytes", "read_text", "read_bytes", "write", "read", "replace", "open", "unlink", "rename"}
            for node in ast.walk(tree):
                if isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.Delete, ast.Match, ast.ClassDef,
                                     ast.Global, ast.Nonlocal, ast.For, ast.AsyncFor, ast.While, ast.If, ast.Try,
                                     ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.With,
                                     ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.NamedExpr)):
                    out.append(_finding("opaque", seg, reason="Python 제어 흐름의 쓰기 경로를 확정할 수 없음"))
                    break
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                    if any(a.asname for a in node.names) or any(n not in {"pathlib", "io", "builtins", "codecs", "json", "re"} for n in names):
                        out.append(_finding("opaque", seg, reason="Python import의 부작용·별칭을 확정할 수 없음"))
                if isinstance(node, ast.Assign) and any(not isinstance(t, ast.Name) or t.id in safe_calls for t in node.targets):
                    out.append(_finding("opaque", seg, reason="Python 대입의 부작용을 확정할 수 없음"))
                if isinstance(node, ast.Call):
                    call = ast.unparse(node.func)
                    if call.startswith(("os.", "shutil.")) or call not in safe_calls and not (isinstance(node.func, ast.Attribute) and node.func.attr in safe_methods):
                        out.append(_finding("opaque", seg, reason="Python 호출의 부작용을 확정할 수 없음"))
            # Preserve the old safety net whenever the AST grammar is insufficient.
            if any(f["kind"] == "opaque" for f in out) and re.search(r"project\.json|local\.json|\.am[/\\]", body):
                if writes or re.search(r"\b(shutil|os|pathlib)\b|\bopen\b", body):
                    out.append(_finding("forbidden", seg, reason="불명확한 Python 쓰기에서 상태 파일 경로 발견"))
            for path in _python_write_paths(body):
                if path is None:
                    out.append(_finding("opaque", seg, reason="스크립트 쓰기 대상 경로를 확인할 수 없음"))
                else:
                    out += classify_file("Write", path, cwd, root, via=seg)
        except (SyntaxError, ValueError):
            out.append(_finding("opaque", seg, reason="Python 본문을 해석할 수 없음"))
    elif writes:  # other languages retain conservative literal-path checks
        # string literals (same quote on both ends) that look like a path — absolute or relative (../other)
        for _q, lit in re.findall(r"(['\"])((?:(?!\1)[^\s()\[\]{}*?^$|<>;,])*[/\\](?:(?!\1)[^\s()\[\]{}*?^$|<>;,])*)\1", body):
            if re.search(r"[\w.]", lit):
                out += classify_file("Write", lit, cwd, root, via=seg)
    if re.search(r"print\s*\(\s*(dict\s*\()?\s*os\.environ\s*\)|console\.log\s*\(\s*process\.env\s*\)|os\.environ\.items\s*\(\)|JSON\.stringify\s*\(\s*process\.env", body):
        out.append(_finding("forbidden", seg, reason="인증정보 노출(환경 변수 전체 출력)"))
    elif re.search(r"(print|pprint|console\.(log|error|info)|sys\.std(out|err)\.write|logging\.\w+|process\.std(out|err)\.write)\s*\([^;]*"
                   r"(os\.environ|getenv|process\.env)", body):
        out.append(_finding("forbidden", seg, reason="인증정보 노출(환경 변수 값을 출력)"))
    if re.search(r"\bimportlib\b|\b__import__\b|\brunpy\b|\bsubprocess\b|os\.system|os\.popen|os\.exec|child_process|\bexecSync\b|\bspawn\w*\s*\(|\bexec\s*\(|\beval\s*\("
                 r"|\bsystem\s*[\(\s'\"]|%x\{|`|\bIO\.popen|\bopen3\b|\bqx\s*[\(\{/]|\bDeno\.(run|Command)|\bBun\.spawn", body):
        out.append(_finding("opaque", seg, reason="스크립트 안에서 다른 명령·코드를 실행"))
    return out


def _write_targets(word, args, seg):
    """Paths a shell command writes to (redirections only outside quotes)."""
    redirect = r"(?<![&<>])\d*>>?(?!&)\s*"  # >, >>, 2>, 1>> … but not 2>&1
    uq = unquoted(seg)
    targets = [m.group(1) for m in re.finditer(redirect + r"(\S+)", uq)]
    # unquoted() blanks quoted targets; recover them from the original text at the same position
    for m in re.finditer(redirect + r"[\"']([^\"']+)[\"']", seg):
        if ">" in uq[m.start():m.start() + 4]:
            targets.append(m.group(1))
    plain = [a for a in args if not a.startswith("-")]
    if word in ("set-content", "add-content", "out-file", "new-item", "sc", "ac"):
        for i, a in enumerate(args):
            if a.lower() in ("-path", "-filepath", "-literalpath") and i + 1 < len(args):
                targets.append(args[i + 1])
                break
        else:
            if plain:
                targets.append(plain[0])
    elif word in ("cp", "mv", "copy-item", "move-item", "copy", "move", "install", "rsync", "robocopy", "xcopy") and plain:
        targets.append(plain[-1])
    elif word == "tee":
        targets += plain
    elif word == "sed" and any(a.startswith("-i") for a in args) and plain:
        targets.append(plain[-1])
    return [t for t in targets if t and t != "/dev/null" and t.lower() != "nul" and not t.startswith("&")]


def _short_flag(args, letter):
    """-f, -fu, -uf … (a single-dash cluster containing the letter)."""
    return any(re.fullmatch(r"-[a-zA-Z]+", a) and letter in a[1:] for a in args)


def _push(args, seg, where):
    out = []
    force = _short_flag(args, "f") or any(a == "--force" or a.startswith("--force-with-lease") or a == "--force-if-includes" for a in args)
    delete = "--delete" in args or _short_flag(args, "d")
    remote, pos, i = None, [], 0
    while i < len(args):
        a = args[i]
        if a in ("--repo", "-o", "--push-option", "--receive-pack", "--exec") and i + 1 < len(args):
            if a == "--repo":
                remote = args[i + 1]
            i += 2
            continue
        if a.startswith("--repo="):
            remote = a.split("=", 1)[1]
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    if remote is None and pos:
        remote, pos = pos[0], pos[1:]
    url = (remote if re.search(r"://|@|\.git$|^[./\\]", remote) else _git(where, "remote", "get-url", remote)) if remote \
        else _git(where, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url or "")
    target_repo = m.group(1) if m else (url or None)
    current = lambda: _git(where, "branch", "--show-current") or None  # noqa: E731
    refs = []  # (destination branch or None=unknown/all, force, deletes it)
    if any(a in ("--all", "--mirror", "--tags", "--branches") for a in args) or any(SUBST in a for a in pos):
        refs = [(None, force, False)]
    elif pos:
        for spec in pos:
            body = spec.lstrip("+")
            src, colon, dst = body.rpartition(":")
            if not colon:
                src, dst = body, body
            if dst.upper() == "HEAD":
                dst = current()
            refs.append((dst, force or spec.startswith("+"), colon == ":" and src == ""))
    else:
        refs = [(current(), force, False)]
    for dst, f, del_one in refs:
        short = (dst or "").removeprefix("refs/heads/")
        if f and (short in MAIN or dst is None):
            out.append(_finding("forbidden", seg, reason="main 강제 push(또는 대상이 불분명한 강제 push)"))
        elif delete or del_one:
            out.append(_gated("destructive_delete", seg, ref=short or None, where=where, repo=target_repo))
        else:
            out.append(_gated("push", seg, ref=short or None, where=where, repo=target_repo))
    return out


def _gh_api(args, seg, where, repo):
    method = "GET"
    for i, a in enumerate(args):
        if a in ("-X", "--method") and i + 1 < len(args):
            method = args[i + 1].upper()
        elif a.startswith("--method="):
            method = a.split("=", 1)[1].upper()
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:].lstrip("=").upper()
    if method == "GET" and any(a in ("-f", "-F", "--field", "--raw-field", "--input") or
                               a.startswith(("--field=", "--raw-field=", "--input=", "-f", "-F")) for a in args):
        method = "POST"  # gh api: any field (also attached, -fname=x) makes it a POST
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a in GH_API_WITH_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    endpoint = pos[0] if pos else ""
    m = re.match(r"/?repos/([^/]+/[^/]+)", endpoint)
    repo = repo or (m.group(1) if m else None)
    if SUBST in endpoint or SUBST in method:
        return [_finding("opaque", seg, reason="gh api 대상을 미리 알 수 없음")]
    if method == "GET":
        if endpoint == "graphql" and re.search(r"\bmutation\b", seg):
            return [_gated("push", seg, where=where, what="remote_api", repo=repo)]
        return []
    m = re.search(r"pulls/(\d+)/merge", endpoint)
    if m:
        return [_gated("merge_main", seg, ref=S.pr_ref(m.group(1)), where=where, repo=repo)]
    if method == "POST" and re.search(r"(^|/)(user|orgs/[^/]+)/repos/?$", endpoint):
        return [_gated("repo_create", seg, where=where)]
    if method == "DELETE":
        return [_gated("destructive_delete", seg, where=where, what="repo_delete", repo=repo)]
    # 승인 단위 = 정확한 API 대상 (예: "PUT repos/me/app/branches/main/protection") — 넓은 push 승인 없이 이것만 허락할 수 있게
    return [_gated("push", seg, ref=f"{method} {endpoint.lstrip('/')}", where=where, what="remote_api", repo=repo)]


def _delete_safe(paths, cwd):
    if not paths or any(SUBST in p for p in paths):
        return False
    top, tmp = _top(cwd), tempfile.gettempdir()

    def scratch(a):  # temp folder that is not part of any git repo
        anc = next((x for x in [a, *a.parents] if x.exists()), a)
        return _under(a, tmp) and not _git(anc, "rev-parse", "--show-toplevel")
    return all((Path(p).name in SAFE_DELETE and _under(_abs(p, cwd), top)) or scratch(_abs(p, cwd)) for p in paths)


def _drop_redirections(toks):
    """2>&1, >file, 2> file, &>file … are not arguments (their targets are checked by _write_targets)."""
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if re.fullmatch(r"\d*(>>?|<|&>>?)(&\d+|&-)?", t):  # bare operator: the next token is its target
            skip = not re.search(r"&\d+$|&-$", t)
            continue
        if re.match(r"^\d*(>>?|<|&>>?)", t):  # operator with attached target / fd
            continue
        out.append(t)
    return out


NPM_OPTS_WITH_ARG = {"--prefix", "-C", "-w", "--workspace", "--userconfig", "--globalconfig", "--registry", "--cache",
                     "--loglevel", "--script-shell", "--node-options", "--tag", "--otp", "--before"}
NPM_ALIASES = {"t": "test", "tst": "test", "run-script": "run", "rum": "run", "urn": "run", "x": "exec", "i": "install",
               "in": "install", "ins": "install", "inst": "install", "insta": "install", "instal": "install",
               "isnt": "install", "isnta": "install", "isntal": "install", "isntall": "install", "add": "install",
               "clean-install": "ci", "ic": "ci", "install-clean": "ci", "isntall-clean": "ci", "it": "install-test",
               "cit": "install-ci-test", "clean-install-test": "install-ci-test", "sit": "install-ci-test"}
NPM_READONLY = {"ls", "list", "ll", "la", "view", "info", "show", "v", "outdated", "audit", "search", "find", "s", "se",
                "help", "root", "prefix", "bin", "explain", "why", "ping", "fund", "-v", "--version", "-h", "--help", "version"}
NPM_INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepublish", "preprepare", "prepare", "postprepare")


def _npm_command(args):
    """npm [global options] <sub> … → (canonical sub, script name or sub). Options before the subcommand are skipped."""
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] not in ("-v", "--version", "-h", "--help"):
        i += 2 if args[i] in NPM_OPTS_WITH_ARG else 1
    sub = NPM_ALIASES.get(args[i].lower(), args[i].lower()) if i < len(args) else ""
    if sub in ("run", "install-test", "install-ci-test"):
        if sub != "run":
            return sub, "test"
        name = next((a for a in args[i + 1:] if not a.startswith("-")), "")
        return sub, name
    return sub, sub


def _npm_readonly(args):
    """Reading package information only — no package code runs. `version` with arguments bumps and runs scripts."""
    sub, _ = _npm_command(args)
    if sub == "version":
        return len(args) == 1
    if sub == "config":
        return args[1:2] in (["get"], ["list"], ["ls"])
    return bool(args) and sub in NPM_READONLY and "fix" not in [a.lower() for a in args]


def _package_runner(toks):
    """npx, pnpx, bunx, npm exec/x, pnpm/yarn dlx, bun x — they download and run any package."""
    w, sub = _word(toks[0]), (toks[1].lower() if len(toks) > 1 else "")
    return w in ("npx", "pnpx", "bunx") or w == "npm" and sub in ("exec", "x") or \
        w in ("pnpm", "yarn") and sub == "dlx" or w == "bun" and sub == "x"


def _strip_prefixes(toks, seg, out, cwd, trusted=False):
    """env/sudo/&/npx… → the real command. `env` alone prints every secret.
    trusted: an npm script body from package.json that equals the remote baseline (NODE_ENV=test jest)."""
    def drop_opts(ts, with_value=()):
        """Drop leading options of a wrapper (npx --yes -p pkg …) so the real command word comes next."""
        while ts and ts[0].startswith("-"):
            o = ts.pop(0)
            if o in with_value and ts:
                ts.pop(0)
        return ts

    while toks:
        w = _word(toks[0])
        if _package_runner(toks) and not _build_allowed(toks, cwd):
            out.append(_finding("opaque", seg, reason="허용 목록 밖 패키지 실행기"))
        if toks[0] in SHELL_KEYWORDS:
            toks = toks[1:]
        elif re.match(r"^[A-Za-z_]\w*=", toks[0]) or toks[0].lower() in PREFIXES:
            if re.match(r"^[A-Za-z_]\w*=", toks[0]) and not trusted:
                out.append(_finding("opaque", seg, reason="환경 변수 접두가 실행 동작을 바꿀 수 있음"))
            toks = drop_opts(toks[1:], ("-p", "--package", "-c", "--call")) if w in ("npx", "pnpx", "bunx") else toks[1:]
        elif w == "env":
            if any(t in ("-C", "--chdir", "-S", "--split-string") or t.startswith(("--chdir=", "-C", "-S")) for t in toks[1:]):
                out.append(_finding("opaque", seg, reason="env가 폴더·명령 문자열을 바꿔 실행"))
                return []
            rest = [t for t in toks[1:] if not t.startswith("-")]
            if not any(not re.match(r"^[A-Za-z_]\w*=", t) for t in rest):
                out.append(_finding("forbidden", seg, reason="인증정보 노출(환경 변수 전체 출력)"))
                return []
            toks = drop_opts(toks[1:], ("-u", "--unset"))
        elif w == "sudo":
            out.append(_gated("system_permission", seg))
            toks = drop_opts(toks[1:], ("-u", "-g", "-C", "-h", "-p"))
        elif _package_runner(toks) and w not in ("npx", "pnpx", "bunx"):
            toks = toks[2:]
            if toks and toks[0] == "--":
                toks = toks[1:]
        else:
            break
    return toks


def analyze(seg, cwd, root, depth=0, script_stdin=False, trusted=False):
    """One simple command → findings."""
    out = []
    toks = _drop_redirections(tokens(seg))
    if any(a in BYPASS_FLAGS for a in toks[1:]):
        out.append(_finding("forbidden", seg, reason="승인 우회 옵션"))
    toks = _strip_prefixes(toks, seg, out, cwd, trusted)
    if not toks:
        return out
    word, args = _word(toks[0]), toks[1:]
    out.append(_seen(f"cmd:{word}", seg))
    low = [a.lower() for a in args]
    joined = " ".join(toks)
    # Keep redirect and policy findings even when a launcher returns early.
    for target in _write_targets(word, args, seg):
        out += classify_file("Write", target, cwd, root, via=seg)

    if SUBST in toks[0] or word.startswith("$") or word in OPAQUE_WORDS:
        return out + [_finding("opaque", seg, reason="실행할 명령을 미리 알 수 없음(변수·치환·eval)")]

    # always-forbidden flags (exact tokens — not text inside a commit message)
    if any(a in BYPASS_FLAGS or a.startswith("--permission-mode=bypass") for a in args) or \
            any(a == "--permission-mode" and i + 1 < len(args) and args[i + 1].lower().startswith("bypass") for i, a in enumerate(args)):
        out.append(_finding("forbidden", seg, reason="승인 우회 옵션"))
    if (word == "gh" and [a for a in low if not a.startswith("-")][:2] == ["auth", "token"]) or joined.lower() in SECRET_LISTERS \
            or word in ("printenv",):
        out.append(_finding("forbidden", seg, reason="인증정보 노출"))
    no_single = re.sub(r"'[^']*'", "''", seg)  # '$TOKEN' in single quotes is literal text, not the secret
    if word in ("echo", "write-output", "write-host", "printf") and re.search(r"\$\{?(env:)?\w*(TOKEN|SECRET|PASSWORD|API_?KEY)\w*", no_single, re.I):
        out.append(_finding("forbidden", seg, reason="인증정보 노출"))
    if word in ("cat", "type", "get-content", "gc", "less", "more", "head", "tail", "bat") and any(
            re.search(r"(auth\.json|\.git-credentials|credentials\.json)$", a, re.I) for a in args):
        out.append(_finding("forbidden", seg, reason="인증정보 노출"))

    # wrappers → recurse into the inner command, decoding that shell's escapes
    if word in SHELLS:
        if any(a in ("-encodedcommand", "-enc", "-e", "-ec") for a in low) and word in ("pwsh", "powershell"):
            return out + [_finding("opaque", seg, reason="인코딩된 명령")]
        for i, a in enumerate(low):
            if a in SHELL_CMD_FLAGS and i + 1 < len(args):
                inner = " ".join(args[i + 1:])
                if word in ("pwsh", "powershell"):
                    inner = re.sub(r"`(.)", r"\1", inner)
                elif word == "cmd":
                    inner = inner.replace("^", "")
                else:  # bash/sh: g\it pu\sh → git push
                    inner = bash_unescape(inner)
                if depth >= 4:
                    return out + [_finding("opaque", seg, reason="너무 깊게 감싼 명령")]
                return out + [_seen("shell-parsed", seg)] + classify_command(inner, cwd, root, depth + 1)
        if args:
            return out if _build_allowed(toks, cwd) else out + [_finding("opaque", seg, reason="추적·변경 여부를 확인하지 못한 셸 스크립트")]
        return out

    if word in SCRIPTERS:
        i, module, body = 0, None, None
        while i < len(args):
            a = args[i]
            if a in PY_OPTS_WITH_ARG:
                i += 2
                continue
            if a in ("--eval", "-p", "--print") and i + 1 < len(args):
                body = " ".join(args[i + 1:])
                break
            if a.startswith(("--eval=", "--print=")):
                body = " ".join([a.split("=", 1)[1], *args[i + 1:]])
                break
            # option clusters: -m am, -mam, -um am, -c "…", -c"…", -Bc "…", -ucCODE
            m = None if a.startswith("--") else re.match(r"^-([bBdEhiIOPqsSuvx]*?)([cem])(.*)$", a)
            if m:
                attached = m.group(3)
                if m.group(2) == "m":
                    module, margs = (attached, args[i + 1:]) if attached else ((args[i + 1], args[i + 2:]) if i + 1 < len(args) else (None, []))
                    margs = [x.lower() for x in margs]
                else:
                    body = " ".join([attached, *args[i + 1:]]) if attached else (" ".join(args[i + 1:]) if i + 1 < len(args) else None)
                break
            if not a.startswith("-"):
                if re.search(r"am[/\\](__main__|cli)\.py$", a) and "approve" in low:
                    out.append(_finding("approve", seg, reason="승인 기록은 사용자만"))
                break
            i += 1
        if module:
            out.append(_seen(f"{_word(module)}:{(margs or [''])[0]}", seg))
        if module == "am" and "approve" in margs:
            out.append(_finding("approve", seg, reason="승인 기록은 사용자만"))
        if body is not None:
            out.append(_seen("script", seg))
            out += _check_script(body, seg, cwd, root, python=word in ("python", "python3", "py"))
            if word in ("python", "python3", "py"):
                out.append(_finding("opaque", seg, reason="인라인 Python 실행은 자동 허용 목록 밖"))
        elif not module and word in ("python", "python3", "py") and not script_stdin and not _build_allowed(toks, cwd):
            out.append(_finding("opaque", seg, reason="추적·변경 여부를 확인하지 못한 Python 스크립트"))
        elif module == "unittest" and not _build_allowed(toks, cwd):
            out.append(_finding("opaque", seg, reason="unittest 인수가 허용 목록 밖(작업 폴더 밖 경로 등)"))

    allowed = word in ("gradlew", "npm") and _build_allowed(toks, cwd)
    if (word == "gradlew" or word in ("npm", "pnpm", "yarn", "bun") and not _npm_readonly(args)) and not allowed:
        out.append(_finding("opaque", seg, reason="허용 목록 밖 테스트·빌드 실행"))
    if word == "npm":
        sub, name = _npm_command(args)
        if re.search(r"deploy|release|publish|push", name, re.I):
            out.append(_gated("deploy", seg))
        keys = {"ci": NPM_INSTALL_HOOKS, "install": NPM_INSTALL_HOOKS}.get(sub, ("pre" + name, name, "post" + name) if name else ())
        if depth < 4 and keys:
            try:
                scripts = json.loads(_abs("package.json", cwd).read_text(encoding="utf-8")).get("scripts", {})
                for key in keys:
                    body = scripts.get(key)
                    if isinstance(body, str):
                        out.extend(classify_command(body, cwd, root, depth + 1, trusted=bool(allowed)))
            except (OSError, ValueError, AttributeError):
                pass
        elif depth >= 4:
            out.append(_finding("opaque", seg, reason="npm 스크립트 재귀를 확인할 수 없음"))

    # removing or moving a state file is forging it too
    if word in ("rm", "remove-item", "ri", "del", "erase", "unlink", "mv", "move", "move-item", "mi", "ren", "rename",
                "rename-item", "rni", "truncate", "clear-content", "clc"):
        if any(_is_state_file(a, cwd, root) for a in args if not a.startswith("-")):
            out.append(_finding("forbidden", seg, reason="상태 파일을 am 없이 지우거나 옮김(승인 위조 위험)"))

    # deletes: recursive, or wildcard — single explicit files stay recoverable through git
    if word in ("rm", "remove-item", "ri", "del", "erase", "rd", "rmdir", "unlink"):
        recursive = any(re.match(r"^-[a-zA-Z]*[rR]", a) for a in args if not a.startswith("--")) or \
            any(a in ("--recursive", "-recurse", "/s") for a in low) or word in ("rd", "rmdir")
        targets = [a for a in args if not a.startswith("-") and not re.match(r"^/[sq]$", a, re.I)]
        wildcard = any(re.search(r"[*?]", t) for t in targets)
        if (recursive or wildcard) and not _delete_safe(targets, cwd):
            out.append(_gated("destructive_delete", seg))

    if word == "git":
        return out + _analyze_git(args, seg, cwd, root, depth)
    if word == "gh" and args:
        return out + _analyze_gh(args, seg, cwd)

    # deploy / db / system / credentials
    if re.match(r"^(vercel|netlify deploy|fly deploy|flyctl deploy|firebase deploy|wrangler (deploy|publish)|railway up|docker push|kubectl (apply|delete|rollout)|eas (submit|update)|npm run deploy|pnpm (run )?deploy|yarn deploy)\b", joined, re.I) \
            or re.search(r"(^|[/\\])deploy\.sh$", toks[0]):
        out.append(_gated("deploy", seg))
    if re.match(r"^(supabase db (push|reset)|prisma migrate (deploy|reset)|prisma db push)\b", joined, re.I) \
            or word in ("psql", "mysql", "sqlite3", "sqlcmd") and re.search(r"\b(drop\s+(table|database|schema)|truncate\s+table|delete\s+from|alter\s+table)\b", seg, re.I):
        out.append(_gated("db_change", seg))
    if (word in ("npm", "pnpm", "yarn", "bun") and any(a in ("-g", "--global", "global") for a in low)
            and any(a in ("i", "install", "add", "uninstall", "remove", "rm", "un", "update", "up", "link") for a in low)
            or word in ("set-executionpolicy", "setx", "icacls", "takeown")
            or word == "reg" and low[:1] in (["add"], ["delete"])
            or word in ("winget", "choco", "scoop") and low[:1] in (["install"], ["uninstall"])
            or word == "chmod" and "777" in args):
        # 패키지 설치는 승인 단위 = "<도구> <동작> <패키지>" (예: "winget install eclipseadoptium.temurin.21.jdk")
        pkgs = [a for a in low[1:] if not a.startswith("-")] if word in ("winget", "choco", "scoop", "npm", "pnpm", "yarn", "bun") else []
        verb = next((a for a in low if not a.startswith("-") and a != "global"), "")
        ref = f"{word} {verb} {' '.join(pkgs)}" if len(pkgs) == 1 and SUBST not in pkgs[0] else None
        out.append(_gated("system_permission", seg, ref=ref))
    if word in ("cat", "type", "less", "more", "head", "tail", "get-content", "gc", "source", "bat") and any(
            re.search(r"(^|[/\\])\.env(\.(?!example$)[\w.]+)?$", a) for a in args):
        out.append(_gated("credentials", seg))
    return out


def _is_state_file(path, cwd, root=None):
    root = Path(root or S.ROOT)
    p = _abs(path, cwd)
    return _under(p, root) and (p.name.lower() == "project.json" and _under(p, root / "projects") or _under(p, root / ".am")) \
        or re.search(r"(^|[/\\])projects[/\\][^/\\]+[/\\]project\.json$|(^|[/\\])\.am[/\\]", str(p), re.I) is not None


def _rewrites_refs(sub, sargs, gargs):
    """Commands that can move refs/remotes/origin/main or change what "origin" is — the build allowlist's baseline."""
    low = [a.lower() for a in sargs]
    pos = [a for a in sargs if not a.startswith("-")]
    if any(re.match(r"-c(?:remote|url)\.|(?:remote|url)\.", a, re.I) for a in gargs if not a.startswith("--")) or \
            any(a.lower().startswith(("--config-env=remote.", "--config-env=url.")) for a in gargs):
        return True  # git -c remote.origin.url=… / -c url.<x>.insteadOf=…
    if sub == "update-ref":
        return True
    if sub == "replace":
        return bool(sargs) and low[:1] not in (["-l"], ["--list"])
    if sub == "symbolic-ref":
        return len(pos) >= 2 or "-d" in low or "--delete" in low
    if sub == "remote":
        return bool(pos) and pos[0].lower() in ("add", "set-url", "rename", "remove", "rm", "set-head", "set-branches")
    if sub == "config":
        keys = [a.lower() for a in pos]
        if keys[:1] in (["set"], ["unset"], ["rename-section"], ["remove-section"], ["edit"]):
            return keys[0] == "edit" or any(k.startswith(("remote.", "url.")) for k in keys[1:])
        reading = any(a in ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l") for a in low)
        writing = any(a in ("--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section") for a in low) \
            or len(pos) >= 2 and not reading
        return "-e" in low or "--edit" in low or writing and any(k.startswith(("remote.", "url.")) for k in keys)
    if sub in ("fetch", "pull"):
        if any(a in ("--refmap", "--upload-pack", "-u", "--update-head-ok") or a.startswith(("--refmap=", "--upload-pack=")) for a in low):
            return True
        return any(":" in a for a in pos) or bool(pos) and re.search(r"^\.$|://|@|^[./\\~]|^[A-Za-z]:", pos[0]) is not None
    if sub == "push":
        return pos[:1] == ["."] or any(re.search(r"(^|:)\+?refs/remotes/", a) for a in pos)
    return False


def _analyze_git(args, seg, cwd, root, depth):
    out, where, rest = [], cwd, list(args)
    while rest and rest[0].startswith("-"):
        opt = rest.pop(0)
        name, _, val = opt.partition("=")
        if opt.startswith("-C") and len(opt) > 2:  # git -C<dir> (attached)
            name, val = "-C", opt[2:]
        elif opt.startswith("-c") and len(opt) > 2 and not opt.startswith("--"):
            name, val = "-c", opt[2:]
        elif name in GIT_GLOBAL_WITH_ARG and not val and rest:
            val = rest.pop(0)
        if name == "-C":
            where = str(_abs(val, where))
        elif name in ("--git-dir", "--work-tree"):
            d = _abs(val, where)
            where = str(d.parent if d.name == ".git" else d)
        if name in ("-c", "--config-env"):
            key = val.split("=", 1)[0].lower()
            if key.startswith("alias."):
                return out + [_finding("opaque", seg, reason="명령 안에서 git 별칭을 새로 정의")]
            if key in ("core.hookspath", "core.sshcommand", "core.fsmonitor", "core.editor", "core.pager", "credential.helper") \
                    or key.startswith(("filter.", "diff.", "merge.")) and key.endswith((".clean", ".smudge", ".textconv", ".driver", ".command")):
                return out + [_finding("forbidden", seg, reason=f"git 설정({key})으로 훅·검사를 바꾸거나 명령을 끼워 넣음")]
    if not rest:
        return out
    sub, sargs = rest[0].lower(), rest[1:]
    out.append(_seen(f"git:{sub}", seg))
    if sub == "update-index" or sub == "config" and any(re.match(r"(?:filter|diff)\.", a, re.I) for a in sargs):
        out.append(_finding("opaque", seg, reason="Git 파일 변경 검사를 바꿀 수 있는 명령"))
    if _rewrites_refs(sub, sargs, args[:len(args) - len(rest)]):
        out.append(_finding("opaque", seg, reason="기준 브랜치(원격 추적 ref)·원격 주소를 바꿀 수 있는 명령"))
    if sub in ("commit", "push", "merge", "rebase", "am", "cherry-pick", "revert") and (
            any(a in ("--no-verify", "--no-gpg-sign") for a in sargs) or sub == "commit" and _short_flag(sargs, "n")):
        out.append(_finding("forbidden", seg, reason="검증 단계 우회"))
    if sub in GIT_REWRITES_FILES and any(_is_state_file(a, where, root) for a in sargs if not a.startswith("-")):
        out.append(_finding("forbidden", seg, reason="상태 파일을 am 없이 지우거나 옮김(승인 위조 위험)"))
    if SUBST in sub or sub.startswith("$"):
        return out + [_finding("opaque", seg, reason="git 하위 명령을 미리 알 수 없음")]
    if sub not in GIT_BUILTINS:
        alias = _git(where, "config", "--get", f"alias.{sub}")
        if alias:
            if alias.startswith("!") or depth >= 4:
                return out + [_finding("opaque", seg, reason=f"git 별칭 '{sub}'이 셸 명령을 실행")]
            return out + classify_command("git " + alias + " " + " ".join(sargs), where, root, depth + 1)
    # 다른 저장소 변경: 관리 대상이 아니면 금지. 관리 중인 프로젝트면 아래 판정이 그 프로젝트(where)의 승인 기록으로 한다
    if _norm(where) != _norm(cwd) and sub in GIT_MUTATING and not _under(_top(where), _top(cwd)) \
            and not resolve_project(where, root):
        out.append(_finding("forbidden", seg, reason=f"다른 저장소({where})를 변경 — 관련 없는 프로젝트 수정 금지"))
    if sub == "push":
        out += _push(sargs, seg, where)
    elif sub == "merge" and _git(where, "branch", "--show-current") in MAIN:
        out.append(_gated("merge_main", seg, where=where, what="merge_local"))
    elif sub == "reset" and "--hard" in sargs or sub == "clean" and any(re.match(r"^-\w*f", a) for a in sargs) \
            or sub == "branch" and any(a in ("-D", "--delete", "-d") for a in sargs) \
            or sub == "worktree" and sargs[:1] == ["remove"] \
            or sub == "checkout" and "--" in sargs and "." in sargs \
            or sub == "update-ref" and "-d" in sargs:
        out.append(_gated("destructive_delete", seg, where=where))
    elif sub == "config" and any(a in ("--global", "--system") for a in sargs) and not any(
            a in ("--get", "--list", "-l", "--get-all", "--get-regexp") for a in sargs):
        out.append(_gated("system_permission", seg))
    return out


def _analyze_gh(args, seg, cwd):
    out, repo, rest, i = [], None, [], 0
    while i < len(args):  # -R/--repo can appear before or after the subcommand
        a = args[i]
        name, _, val = a.partition("=")
        if a.startswith("-R") and len(a) > 2:  # gh -Rme/other (attached)
            name, val = "-R", a[2:]
        if name in GH_GLOBAL_WITH_ARG:
            if not val and i + 1 < len(args):
                val = args[i + 1]
                i += 1
            if name in ("-R", "--repo"):
                repo = val
        else:
            rest.append(a)
        i += 1
    low = [a.lower() for a in rest]
    pos = [a for a in low if not a.startswith("-")]
    out.append(_seen("gh:" + ".".join(pos[:2]), seg))
    if any(SUBST in a for a in pos[:2]):
        return [_finding("opaque", seg, reason="gh 하위 명령을 미리 알 수 없음")]
    if pos[:2] == ["auth", "token"]:
        return [_finding("forbidden", seg, reason="인증정보 노출")]
    if pos[:2] == ["repo", "create"]:
        # 승인 단위 = 만들 저장소 이름. 아직 로컬에 없으니 decide()가 그 이름을 예정한 프로젝트(am set --repo)에서 승인을 찾는다
        name = next((a for a in rest[rest.index("create") + 1:] if not a.startswith("-")), None) if "create" in rest else None
        out.append(_gated("repo_create", seg, ref=name.lower() if name else None))
    elif pos[:2] == ["repo", "edit"] and any(a.startswith("--visibility") for a in low):
        out.append(_gated("repo_create", seg, what="repo_visibility", repo=repo or (pos[2] if len(pos) > 2 else None)))
    elif pos[:2] == ["repo", "delete"]:
        out.append(_gated("destructive_delete", seg, what="repo_delete", repo=repo or (pos[2] if len(pos) > 2 else None)))
    elif pos[:2] == ["pr", "merge"]:
        num = next((a.lstrip("#") for a in pos[2:] if re.fullmatch(r"#?\d+", a)), None)
        for a in pos[2:]:  # gh pr merge https://github.com/<owner>/<name>/pull/<n>
            m = re.match(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", a)
            if m:
                repo, num = repo or m.group(1), m.group(2)
        out.append(_gated("merge_main", seg, ref=S.pr_ref(num) if num else None, repo=repo))
    elif pos[:1] == ["api"]:
        idx = low.index("api")
        out += _gh_api(rest[idx + 1:], seg, cwd, repo)
    return out


HEREDOC = re.compile(r"(?m)^(?P<head>[^\n]*?)<<(?P<quote>['\"]?)(?P<tag>\w+)(?P=quote)(?P<tail>[^\n]*)\n"
                     r"(?P<body>(?:[^\n]*\n)*?)(?P=tag)(?:\n|$)")
HEREDOC_MARK = "\x00HEREDOC"


def _stdin_interpreter(toks):
    """`python -`, `python`, `py -3 -`, `node`, `node -` — the heredoc body is the whole program. Nothing else."""
    word, args = _word(toks[0]), toks[1:]
    if word == "py" and args and re.fullmatch(r"-[23](?:\.\d+)?(?:-(?:32|64))?", args[0]):
        args = args[1:]
    return word in ("python", "py", "node") and args in ([], ["-"])


def _stdin_data(toks):
    """Commands that only read the heredoc as text: cat, tee, git commit -F -, gh … --body-file -."""
    word, args = _word(toks[0]), toks[1:]
    dash_file = any(a in ("-F", "--file", "--body-file") and args[i + 1:i + 2] == ["-"] or a in ("--file=-", "--body-file=-")
                    for i, a in enumerate(args))
    if word == "cat":
        return all(a == "-" for a in args)
    if word == "tee":
        return all(a in ("-a", "--append") or not a.startswith("-") for a in args)
    if word == "git":
        return args[:1] == ["commit"] and dash_file
    if word == "gh":
        return [a for a in args if not a.startswith("-")][:2] in (["pr", "create"], ["pr", "edit"], ["pr", "comment"],
                                                                   ["issue", "create"], ["issue", "comment"]) and dash_file
    return False


def _lead(words):
    """Command word after shell keywords and transparent prefixes: if cd, ! cd, command cd, builtin cd, time cd."""
    i = 0
    while i < len(words) and (words[i] in SHELL_KEYWORDS or words[i].lower() in ("command", "builtin", "nohup", "exec", "&")):
        i += 1
    return words[i:]


def _changes_env(words):
    """Same command line, environment changed for what follows (export X=…; npm test)."""
    if not words:
        return False
    w, args = words[0].lower(), [a.lower() for a in words[1:]]
    if w in ENV_SETTERS:
        return True
    if w == "set":  # set -a / set -o allexport / cmd's set X=1; set -e, set -o pipefail are fine
        return any("=" in a or re.fullmatch(r"[-+][a-z]*a[a-z]*", a) or a == "allexport" for a in args)
    if w in (".", "source") and args:
        return True  # runs another file in this shell
    if w in ("set-item", "si", "new-item", "ni", "set-variable", "sv") and any(a.startswith("env:") or "env:" in a for a in args):
        return True
    return w.startswith(("$env:", "[environment]::", "[system.environment]::"))


def _heredocs(cmd, cwd, root, depth, out, stored):
    """Each heredoc → its head line goes back into the command (split and judged like any other line).
    Only `<interpreter> [-]` gets its body checked as a program; cat/tee/commit message bodies are data; anything else
    that reads a heredoc is opaque and its body stays in the command as shell lines."""
    def one(m):
        head, tail, body = m.group("head"), m.group("tail"), m.group("body")
        line = m.group(0).split("\n", 1)[0]
        if unquoted(line)[len(head):len(head) + 2] != "<<" or head.rstrip().endswith("<"):
            return m.group(0)  # inside quotes, or <<< — left for the later "unresolved <<" check
        if not m.group("quote"):  # shell expansions in an unquoted heredoc run before the reader gets the text
            _, substitutions, broken = extract_substitutions(body)
            if broken or substitutions:
                out.append(_finding("opaque", line, reason="인용하지 않은 heredoc의 셸 치환"))
        command = head + " " + tail
        segs = split_commands(command)
        toks = _drop_redirections(tokens(segs[0])) if len(segs) == 1 else []
        if toks and _stdin_interpreter(toks):
            stored.append((segs[0], body, _word(toks[0]) != "node"))
            return f"{HEREDOC_MARK}{len(stored) - 1}\n"
        if toks and _stdin_data(toks):
            return command + "\n"
        out.append(_finding("opaque", line, reason="heredoc을 받는 명령이 단일 인터프리터·문서 명령이 아님"))
        return command + "\n" + body
    return HEREDOC.sub(one, cmd)


def classify_command(cmd, cwd, root=None, depth=0, trusted=False):
    out, stored = [], []
    if depth == 0:
        _CACHE.clear()  # git state may have changed since the last judgement
    # Language bodies come out before shell lexing (set(...) is not shell `set`); the head line stays in the command.
    cmd = _heredocs(cmd, cwd, root, depth, out, stored)
    if "<<" in unquoted(cmd):
        out.append(_finding("opaque", cmd[:120], reason="해석하지 못한 heredoc 경계·인터프리터"))
    if "*** Begin Patch" in cmd:
        return out + _patch_paths(cmd, cwd, root)
    flat, inner, broken = extract_substitutions(cmd)
    if broken:
        return out + [_finding("opaque", cmd[:120], reason="닫히지 않은 명령 치환")]
    for sub in inner:
        out += classify_command(sub, cwd, root, depth + 1, trusted) if depth < 4 else [_finding("opaque", sub, reason="너무 깊은 치환")]
    initial_top = _top(cwd)
    for seg in split_commands(flat):
        m = re.fullmatch(re.escape(HEREDOC_MARK) + r"(\d+)", seg)
        if m:  # `python - <<EOF` judged where it runs (after any cd above it)
            head, body, python = stored[int(m.group(1))]
            out += _check_script(body, head, cwd, root, python=python)
            out += analyze(head, cwd, root, depth, script_stdin=True)
            continue
        words = _lead(tokens(seg))
        if words and _word(words[0]) in CD_WORDS:
            if len(words) == 2 and _word(words[0]) == "cd" and _under(_abs(words[1], cwd), initial_top) and _abs(words[1], cwd).is_dir():
                cwd = str(_abs(words[1], cwd))
            else:
                out.append(_finding("opaque", seg, reason="현재 작업 트리 밖 또는 불명확한 폴더 변경"))
        if not trusted and _changes_env(words):
            out.append(_finding("opaque", seg, reason="같은 명령줄에서 환경 변수·셸 설정을 바꿈"))
        out += analyze(seg, cwd, root, depth, trusted=trusted)
    if depth == 0:
        out += tripwire(cmd, out)
    for f in out:
        f["seg"] = f["seg"].replace(SUBST, "$(…)")
    return out


# ---------- second net: risky words the interpreter did not account for ----------
# (pattern on the decoded command text, tags that mean "a parser looked at this")
TRIPWIRES = [
    (r"push", ("git:push", "cmd:docker", "gh:")),
    (r"merge", ("git:merge", "gh:pr.merge", "gh:api")),
    (r"approve", ("am:approve",)),
    (r"(vercel|netlify|firebase|wrangler|railway|flyctl|kubectl|eas)",
     ("cmd:vercel", "cmd:netlify", "cmd:firebase", "cmd:wrangler", "cmd:railway", "cmd:flyctl", "cmd:kubectl", "cmd:eas")),
    (r"(environ|printenv|process\.env)", ("script", "cmd:printenv")),
    (r"auth\s+token", ("gh:auth.token",)),
    (r"(project\.json|local\.json)", ("cmd:", "git:")),
    (r"(no-verify|hookspath)", ("git:",)),
    (r"(drop\s+(table|database)|truncate\s+table|migrate\s+(reset|deploy)|db\s+(push|reset))",
     ("cmd:psql", "cmd:mysql", "cmd:sqlite3", "cmd:sqlcmd", "cmd:supabase", "cmd:prisma")),
]
EXEC_FLAG_BEFORE = re.compile(r"(?:^|\s)(?:-c|-lc|-ic|-command|/c|/k|-e|--eval=?|--print=?|eval|iex|invoke-expression)\s*$", re.I)


def tripwire_text(cmd):
    """Decoded command text where quoted strings count only if they are code (bash -c '…', python -c "…")."""
    s = bash_unescape(cmd).replace("`", "").replace("^", "")
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch in "\"'":
            j = s.find(ch, i + 1)
            j = len(s) if j < 0 else j
            body = s[i + 1:j]
            out.append(body if EXEC_FLAG_BEFORE.search("".join(out)) else " " * len(body))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out).lower()


def tripwire(cmd, found):
    """Per command segment: a risky word in a segment that no parser explained → opaque.
    A segment is explained when it got a tag other than "this is a shell/interpreter" — then its remaining
    words are data (git log --grep push, rg merge, pytest -k push, print('push'))."""
    if any(f["kind"] in ("forbidden", "opaque", "approve") for f in found):
        return []  # already stopped
    launchers = {f"cmd:{w}" for w in SHELLS | SCRIPTERS}
    tags = {}
    for f in found:
        if f["kind"] == "seen":
            tags.setdefault(f["seg"], set()).add(f["tag"])
    flat, _, _ = extract_substitutions(cmd)
    hits = []
    for seg in split_commands(flat):
        if tags.get(seg, set()) - launchers:
            continue
        text = tripwire_text(seg)
        for pat, _owners in TRIPWIRES:
            m = re.search(r"(?<![\w=/.:-])" + pat + r"(?![\w=/.-])", text)
            if m:
                hits.append(m.group(0))
    if hits:
        return [_finding("opaque", cmd[:160], reason=f"위험 단어({', '.join(dict.fromkeys(hits))})가 있는데 어떤 명령인지 해석하지 못함")]
    return []


def _patch_paths(text, cwd, root):
    out = []
    for m in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$|^\+\+\+ b/(.+)$", text, re.M):
        path = (m.group(1) or m.group(2) or m.group(3)).strip()
        out += classify_file("Write", path, cwd, root, via="patch")
    return out


def classify_file(tool, path, cwd, root=None, via=None):
    root = Path(root or S.ROOT)
    out = []
    p = _abs(path, cwd)
    label = via or f"{tool} {p}"
    if tool in WRITE_TOOLS:
        if _is_state_file(str(p), cwd, root):
            out.append(_finding("forbidden", label, reason="상태 파일을 am 없이 직접 수정(승인 위조 위험)"))
        elif re.search(r"[/\\]knowledge-base[/\\]decisions[/\\](?!readme)", str(p), re.I):
            out.append(_gated("architecture_policy", label, ref=p.name))
        else:
            top = _top(cwd)
            anc = next((a for a in [p, *p.parents] if a.exists()), p)
            other_repo = _git(anc, "rev-parse", "--show-toplevel")
            in_other_repo = bool(other_repo) and _norm(other_repo) != _norm(top)
            scratch = [tempfile.gettempdir(), Path.home() / ".claude", Path.home() / ".codex"]
            if not _under(p, top) and (in_other_repo or not any(_under(p, a) for a in scratch)):
                out.append(_finding("forbidden", label, reason=f"지금 작업 폴더({top}) 밖 파일 수정 — 관련 없는 프로젝트·매니저의 직접 코딩 금지"))
            elif ".git" in [part.lower() for part in p.parts]:
                out.append(_finding("opaque", label, reason="Git 내부 파일(ref·설정·훅) 직접 수정"))
    if tool in ("Read", "view") and re.search(r"(^|[/\\])\.env(\.(?!example$)[\w.]+)?$", str(p)):
        out.append(_gated("credentials", f"Read {p}"))
    return out


def extract(payload):
    """Claude Code and Codex payloads → (tool, command|None, file_path|None, patch|None, cwd)."""
    tool = payload.get("tool_name") or payload.get("tool") or ""
    ti = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(ti, str):  # Codex apply_patch may send the patch text directly
        ti = {"patch": ti}
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if isinstance(cmd, list):
        cmd = " ".join(f'"{c}"' if re.search(r"\s", str(c)) else str(c) for c in cmd)
    path = (ti.get("file_path") or ti.get("path") or ti.get("notebook_path")) if isinstance(ti, dict) else None
    patch = (ti.get("patch") or ti.get("input")) if isinstance(ti, dict) else None
    cwd = payload.get("cwd") or os.getcwd()
    return tool, cmd, path, patch if isinstance(patch, str) else None, cwd


# ---------- decision ----------

def _repo_of(p):
    return (p.get("repo") or "").lower() if p else ""


def decide(payload, agent="claude", root=None):
    """→ (decision, message). decision: allow | deny | ask"""
    tool, cmd, path, patch, cwd = extract(payload)
    findings = []
    _CACHE.clear()
    if cmd and tool.lower() == "powershell":  # in PowerShell ` is an escape character, not a substitution
        cmd = re.sub(r"`(.)", r"\1", cmd)
    elif cmd and tool == "Bash":  # Claude Code's Bash tool: g\it pu\sh is git push
        cmd = bash_unescape(cmd)
    if cmd:
        findings += classify_command(cmd, cwd, root)
    if path:
        findings += classify_file(tool, path, cwd, root)
    if patch:
        findings += _patch_paths(patch, cwd, root)
    findings = [f for f in findings if f["kind"] != "seen"]
    if not findings:
        return "allow", ""

    # a gh -R / repos/<owner>/<name> target that is not this project's repo = unrelated project
    projects = {}

    def proj(where):
        slug = resolve_project(where, root)
        if slug and slug not in projects:
            projects[slug] = S.load(slug, root)
        return slug, projects.get(slug)
    def planned_owner(repo_name):
        """The managed project whose repo is owner/name (also one recorded before the repo exists)."""
        for q in S.all_projects(root):
            if (q.get("repo") or "").lower() == repo_name:
                projects.setdefault(q["slug"], q)
                return q["slug"], projects[q["slug"]]
        return None, None

    for f in findings:
        if f["kind"] == "gated" and f.get("repo"):
            slug, p = proj(f.get("where") or cwd)
            if p and _repo_of(p) and f["repo"].lower() != _repo_of(p):
                owner, _ = planned_owner(f["repo"].lower())
                if owner:
                    f["owner_slug"] = owner  # 관리 중인 다른 프로젝트의 저장소 → 그 프로젝트의 승인으로 판정
                else:
                    findings.append(_finding("forbidden", f["seg"], reason=f"다른 저장소({f['repo']})를 변경 — 이 프로젝트 저장소는 {p['repo']}"))

    forb = [f for f in findings if f["kind"] == "forbidden"]
    if forb:
        return "deny", "[am guard] 금지된 작업 — 승인으로도 풀 수 없습니다\n" + "\n".join(f"- {f['reason']}: {f['seg']}" for f in forb)

    if any(f["kind"] == "approve" for f in findings):
        msg = "[am guard] 승인 기록은 사용자가 직접 확인해야 합니다. 사용자가 이 대화에서 명시적으로 승인한 게 맞을 때만 허용하세요."
        if agent == "codex":
            return "deny", msg + "\n사용자에게 이 명령을 직접 실행해 달라고 요청하세요: " + (cmd or "")
        return "ask", msg

    opaque = [f for f in findings if f["kind"] == "opaque"]
    missing, granted = [], []
    for f in findings:
        if f["kind"] != "gated":
            continue
        slug, p = proj(f.get("where") or cwd)
        if f.get("owner_slug"):
            slug, p = f["owner_slug"], projects[f["owner_slug"]]
        if f["gate"] == "repo_create" and f.get("ref") and "/" in f["ref"]:
            owner = planned_owner(f["ref"])  # 새 저장소는 그 이름을 예정한 프로젝트의 승인만 본다
            if owner[0]:
                slug, p = owner  # 예정이 없으면 지금 작업 폴더 프로젝트 기준 그대로 → 차단 + 승인 요청
        f["slug"] = slug
        (granted if p and S.has_approval(p, f["gate"], f.get("ref")) else missing).append(f)
    for f in granted:
        S.event(projects[f["slug"]], f"승인된 작업 실행: {f['gate']}" + (f"({f['ref']})" if f.get("ref") else ""))
    if not missing:
        if opaque:
            msg = "[am guard] 무엇이 실행될지 미리 확인할 수 없는 명령입니다 — 사람이 직접 판단해야 합니다\n" + \
                  "\n".join(f"- {f['reason']}: {f['seg']}" for f in opaque) + "\n변수·치환·eval 없이 명령을 그대로 쓰면 자동으로 검사됩니다."
            return ("deny" if agent == "codex" else "ask"), msg
        for p in projects.values():
            S.save(p, root)
        return "allow", ""

    lines = ["[am guard] 사용자 승인이 필요해서 실행하지 않았습니다.",
             "사용자에게는 AGENTS.md '승인 요청 형식'대로 쉬운 말로 물어보세요 (내부 용어·기록용 이름을 앞에 쓰지 말 것)."]
    for f in missing:
        slug = f["slug"]
        lines += [f"■ 하려는 일: {S.GATES[f['gate']]}" + (f" — 대상: {f['ref']}" if f.get("ref") else ""),
                  f"  영향 범위: {f['impact']}" + (f" (프로젝트 {slug})" if slug else " (관리 대상이 아닌 저장소)"),
                  f"  위험: {f['risk']}",
                  f"  명령: {f['seg']}",
                  f"  기록용 이름: {f['gate']}"]
        if slug:
            S.add_request(projects[slug], f["gate"], f.get("ref"), f["seg"], f["impact"], f["risk"])
    for p in projects.values():
        S.save(p, root)
    managed = [f for f in missing if f["slug"]]
    if managed:
        cmds = [f"python -m am approve {f['slug']} {f['gate']}" + (f" --ref {f['ref']}" if f.get("ref") else "") for f in managed]
        lines.append("위 내용을 사용자에게 보여 주고 승인받은 뒤 기록하고 다시 실행: " + " && ".join(dict.fromkeys(cmds)))
    if agent == "claude" and not managed:
        return "ask", "\n".join(lines)  # 관리 대상이 아닌 저장소: 사람이 직접 판단
    return "deny", "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    agent = argv[0] if argv else "claude"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raw = sys.stdin.read()
    try:
        decision, msg = decide(json.loads(raw or "{}"), agent)
    except Exception as e:  # fail closed for anything that looks risky
        risky = re.search(r"push|gh\s|rm\s|Remove-Item|deploy|\.env|approve|project\.json|patch|merge", raw, re.I)
        decision, msg = ("deny" if risky else "allow"), f"[am guard] 가드 오류로 막았습니다: {e!r}"
    if decision == "deny":
        print(msg, file=sys.stderr)
        return 2
    if decision == "ask":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
