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
OPAQUE_WORDS = {"eval", "iex", "invoke-expression", "invoke-command", "xargs", "source"}
GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env", "--super-prefix"}
GIT_BUILTINS = {"add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file", "checkout", "cherry-pick",
                "clean", "clone", "commit", "config", "describe", "diff", "fetch", "format-patch", "gc", "grep", "init", "log",
                "ls-files", "ls-remote", "ls-tree", "merge", "merge-base", "mv", "notes", "pull", "push", "range-diff", "rebase",
                "reflog", "remote", "reset", "restore", "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "sparse-checkout",
                "stash", "status", "submodule", "switch", "symbolic-ref", "tag", "update-ref", "worktree", "help", "version"}
GIT_MUTATING = {"commit", "add", "push", "merge", "reset", "checkout", "switch", "rm", "mv", "stash", "apply", "am",
                "rebase", "cherry-pick", "revert", "tag", "branch", "clean", "worktree", "restore", "pull", "fetch", "update-ref"}
GH_GLOBAL_WITH_ARG = {"-R", "--repo", "--hostname"}
MAIN = {"main", "master"}
SAFE_DELETE = {"node_modules", "dist", "build", ".next", "out", "coverage", "__pycache__", ".pytest_cache", ".turbo", ".cache", "target"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
BYPASS_FLAGS = {"--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox", "--yolo"}
SECRET_LISTERS = {"printenv", "get-childitem env:", "gci env:", "dir env:", "ls env:", "set"}

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
    return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")


def _under(path, root):
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r + os.sep)


def _abs(path, cwd):
    return Path(path) if os.path.isabs(path) else Path(cwd) / path


def _top(cwd):
    return _git(cwd, "rev-parse", "--show-toplevel") or str(cwd)


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
    """Split on ; | & { } ( ) and newlines that are outside quotes."""
    out, cur, q = [], [], None
    for ch in cmd:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
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
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", name)


# ---------- command analysis ----------

def _check_script(body, seg, cwd, root):
    """Inline python -c / node -e / heredoc bodies."""
    out = []
    touches_am = re.search(r"\bfrom\s+am\b|\bimport\s+am\b|\bam\.(state|guard|cli)\b", body)
    writes = re.search(r"write_text|write_bytes|open\s*\([^)]*['\"][wax+]|writeFile|appendFile|json\.dump|unlink|rename|replace\s*\(|rmtree|copyfile|\.save\s*\(", body)
    if touches_am and (writes or re.search(r"approve\s*\(|\.save\s*\(|save_local|approvals|add_request", body)):
        out.append(_finding("forbidden", seg, reason="스크립트로 승인·상태를 직접 조작(위조 위험)"))
    elif re.search(r"project|\.am\b|local\.json", body, re.I) and writes:
        out.append(_finding("forbidden", seg, reason="스크립트로 상태 파일을 직접 수정(위조 위험)"))
    if writes:  # 스크립트가 쓰는 경로가 문자열로 드러나 있으면 그 경로를 검사
        for lit in re.findall(r"['\"]([^'\"]*[/\\][^'\"]*)['\"]", body):
            if os.path.isabs(lit):
                out += classify_file("Write", lit, cwd, root, via=seg)
    if re.search(r"print\s*\(\s*(dict\s*\()?\s*os\.environ\s*\)|console\.log\s*\(\s*process\.env\s*\)|os\.environ\.items\s*\(\)|JSON\.stringify\s*\(\s*process\.env", body):
        out.append(_finding("forbidden", seg, reason="인증정보 노출(환경 변수 전체 출력)"))
    if re.search(r"\bsubprocess\b|os\.system|os\.popen|os\.exec|child_process|\bexecSync\b|\bspawn\w*\s*\(|\bexec\s*\(|\beval\s*\(", body):
        out.append(_finding("opaque", seg, reason="스크립트 안에서 다른 명령·코드를 실행"))
    return out


def _write_targets(word, args, seg):
    """Paths a shell command writes to (redirections only outside quotes)."""
    targets = [m.group(1) for m in re.finditer(r"(?<![\d&])>>?\s*(\S+)", unquoted(seg))]
    # unquoted() blanks quoted targets; recover them from the original text at the same position
    for m in re.finditer(r"(?<![\d&])>>?\s*[\"']([^\"']+)[\"']", seg):
        if unquoted(seg)[m.start()] == ">":
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


def _push(args, seg, where):
    out = []
    force = any(a in ("--force", "-f") or a.startswith("--force-with-lease") or a == "--force-if-includes" for a in args)
    delete = "--delete" in args or "-d" in args
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
            out.append(_gated("destructive_delete", seg, ref=short or None, where=where))
        else:
            out.append(_gated("push", seg, ref=short or None, where=where))
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
                               a.startswith(("--field=", "--raw-field=", "--input=", "-f=", "-F=")) for a in args):
        method = "POST"
    endpoint = next((a for a in args if not a.startswith("-") and a.upper() != method), "")
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
    return [_gated("push", seg, where=where, what="remote_api", repo=repo)]


def _delete_safe(paths, cwd):
    if not paths or any(SUBST in p for p in paths):
        return False
    top, tmp = _top(cwd), tempfile.gettempdir()

    def scratch(a):  # temp folder that is not part of any git repo
        anc = next((x for x in [a, *a.parents] if x.exists()), a)
        return _under(a, tmp) and not _git(anc, "rev-parse", "--show-toplevel")
    return all((Path(p).name in SAFE_DELETE and _under(_abs(p, cwd), top)) or scratch(_abs(p, cwd)) for p in paths)


def _strip_prefixes(toks, seg, out):
    """env/sudo/&/npx… → the real command. `env` alone prints every secret."""
    while toks:
        w = _word(toks[0])
        if re.match(r"^[A-Za-z_]\w*=", toks[0]) or toks[0].lower() in PREFIXES:
            toks = toks[1:]
        elif w == "env":
            rest = [t for t in toks[1:] if not t.startswith("-")]
            if not any(not re.match(r"^[A-Za-z_]\w*=", t) for t in rest):
                out.append(_finding("forbidden", seg, reason="인증정보 노출(환경 변수 전체 출력)"))
                return []
            toks = toks[1:]
        elif w == "sudo":
            out.append(_gated("system_permission", seg))
            toks = toks[1:]
        elif w in ("pnpm", "yarn") and len(toks) > 1 and toks[1].lower() == "dlx" or w == "npm" and len(toks) > 1 and toks[1].lower() == "exec":
            toks = toks[2:]
            if toks and toks[0] == "--":
                toks = toks[1:]
        else:
            break
    return toks


def analyze(seg, cwd, root, depth=0):
    """One simple command → findings."""
    out = []
    toks = _strip_prefixes(tokens(seg), seg, out)
    if not toks:
        return out
    word, args = _word(toks[0]), toks[1:]
    low = [a.lower() for a in args]
    joined = " ".join(toks)

    if SUBST in toks[0] or word.startswith("$") or word in OPAQUE_WORDS:
        return out + [_finding("opaque", seg, reason="실행할 명령을 미리 알 수 없음(변수·치환·eval)")]

    # always-forbidden flags (exact tokens — not text inside a commit message)
    if any(a in BYPASS_FLAGS or a.startswith("--permission-mode=bypass") for a in args) or \
            any(a == "--permission-mode" and i + 1 < len(args) and args[i + 1].lower().startswith("bypass") for i, a in enumerate(args)):
        out.append(_finding("forbidden", seg, reason="승인 우회 옵션"))
    if word == "git" and any(a in ("--no-verify", "--no-gpg-sign", "-n") and sub_is(args, ("commit", "push")) for a in args):
        out.append(_finding("forbidden", seg, reason="검증 단계 우회"))
    if (word == "gh" and [a for a in low if not a.startswith("-")][:2] == ["auth", "token"]) or joined.lower() in SECRET_LISTERS \
            or word in ("printenv",):
        out.append(_finding("forbidden", seg, reason="인증정보 노출"))
    if word in ("echo", "write-output", "write-host", "printf") and re.search(r"\$\{?(env:)?\w*(TOKEN|SECRET|PASSWORD|API_?KEY)\w*", seg, re.I):
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
                if depth >= 4:
                    return out + [_finding("opaque", seg, reason="너무 깊게 감싼 명령")]
                return out + classify_command(inner, cwd, root, depth + 1)
        if args and not args[0].startswith("-") and args[0].endswith((".sh", ".ps1", ".bat", ".cmd")):
            return out + [_finding("opaque", seg, reason="셸 스크립트 파일 실행")]
        return out

    if word in SCRIPTERS:
        i, module, body = 0, None, None
        while i < len(args):
            a = args[i]
            if a in PY_OPTS_WITH_ARG:
                i += 2
                continue
            if a in ("-m",) and i + 1 < len(args):
                module, margs = args[i + 1], [x.lower() for x in args[i + 2:]]
                break
            if a in ("-c", "-e", "--eval", "-p") and i + 1 < len(args):
                body = " ".join(args[i + 1:])
                break
            if not a.startswith("-"):
                if re.search(r"am[/\\](__main__|cli)\.py$", a) and "approve" in low:
                    out.append(_finding("approve", seg, reason="승인 기록은 사용자만"))
                break
            i += 1
        if module == "am" and "approve" in margs:
            out.append(_finding("approve", seg, reason="승인 기록은 사용자만"))
        if body is not None:
            out += _check_script(body, seg, cwd, root)

    # writes through the shell
    for t in _write_targets(word, args, seg):
        out += classify_file("Write", t, cwd, root, via=seg)

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
        out.append(_gated("system_permission", seg))
    if word in ("cat", "type", "less", "more", "head", "tail", "get-content", "gc", "source", "bat") and any(
            re.search(r"(^|[/\\])\.env(\.(?!example$)[\w.]+)?$", a) for a in args):
        out.append(_gated("credentials", seg))
    return out


def sub_is(args, subs):
    rest = [a for a in args if not a.startswith("-")]
    return bool(rest) and rest[0] in subs


def _analyze_git(args, seg, cwd, root, depth):
    out, where, rest = [], cwd, list(args)
    while rest and rest[0].startswith("-"):
        opt = rest.pop(0)
        name, _, val = opt.partition("=")
        if name in GIT_GLOBAL_WITH_ARG and not val and rest:
            val = rest.pop(0)
        if name == "-C":
            where = str(_abs(val, where))
        if name in ("-c", "--config-env") and val.lower().startswith("alias."):
            return out + [_finding("opaque", seg, reason="명령 안에서 git 별칭을 새로 정의")]
    if not rest:
        return out
    sub, sargs = rest[0].lower(), rest[1:]
    if SUBST in sub or sub.startswith("$"):
        return out + [_finding("opaque", seg, reason="git 하위 명령을 미리 알 수 없음")]
    if sub not in GIT_BUILTINS:
        alias = _git(where, "config", "--get", f"alias.{sub}")
        if alias:
            if alias.startswith("!") or depth >= 4:
                return out + [_finding("opaque", seg, reason=f"git 별칭 '{sub}'이 셸 명령을 실행")]
            return out + classify_command("git " + alias + " " + " ".join(sargs), where, root, depth + 1)
    if _norm(where) != _norm(cwd) and sub in GIT_MUTATING and not _under(_top(where), _top(cwd)):
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
    if any(SUBST in a for a in pos[:2]):
        return [_finding("opaque", seg, reason="gh 하위 명령을 미리 알 수 없음")]
    if pos[:2] == ["auth", "token"]:
        return [_finding("forbidden", seg, reason="인증정보 노출")]
    if pos[:2] == ["repo", "create"]:
        out.append(_gated("repo_create", seg))
    elif pos[:2] == ["repo", "edit"] and any(a.startswith("--visibility") for a in low):
        out.append(_gated("repo_create", seg, what="repo_visibility", repo=repo or (pos[2] if len(pos) > 2 else None)))
    elif pos[:2] == ["repo", "delete"]:
        out.append(_gated("destructive_delete", seg, what="repo_delete", repo=repo or (pos[2] if len(pos) > 2 else None)))
    elif pos[:2] == ["pr", "merge"]:
        num = next((a.lstrip("#") for a in pos[2:] if re.fullmatch(r"#?\d+", a)), None)
        out.append(_gated("merge_main", seg, ref=S.pr_ref(num) if num else None, repo=repo))
    elif pos[:1] == ["api"]:
        idx = low.index("api")
        out += _gh_api(rest[idx + 1:], seg, cwd, repo)
    return out


def classify_command(cmd, cwd, root=None, depth=0):
    out = []
    if "<<" in cmd and re.search(r"\b(python3?|py|node)\b", cmd):  # heredoc script body
        out += _check_script(cmd, cmd.splitlines()[0], cwd, root)
    if "*** Begin Patch" in cmd:
        return out + _patch_paths(cmd, cwd, root)
    flat, inner, broken = extract_substitutions(cmd)
    if broken:
        return out + [_finding("opaque", cmd[:120], reason="닫히지 않은 명령 치환")]
    for sub in inner:
        out += classify_command(sub, cwd, root, depth + 1) if depth < 4 else [_finding("opaque", sub, reason="너무 깊은 치환")]
    for seg in split_commands(flat):
        out += analyze(seg, cwd, root, depth)
    for f in out:
        f["seg"] = f["seg"].replace(SUBST, "$(…)")
    return out


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
        if _under(p, root) and (p.name.lower() == "project.json" and _under(p, root / "projects") or _under(p, root / ".am")):
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
    if cmd:
        findings += classify_command(cmd, cwd, root)
    if path:
        findings += classify_file(tool, path, cwd, root)
    if patch:
        findings += _patch_paths(patch, cwd, root)
    if not findings:
        return "allow", ""

    # a gh -R / repos/<owner>/<name> target that is not this project's repo = unrelated project
    projects = {}

    def proj(where):
        slug = resolve_project(where, root)
        if slug and slug not in projects:
            projects[slug] = S.load(slug, root)
        return slug, projects.get(slug)
    for f in findings:
        if f["kind"] == "gated" and f.get("repo"):
            slug, p = proj(f.get("where") or cwd)
            if p and _repo_of(p) and f["repo"].lower() != _repo_of(p):
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
    if opaque:
        msg = "[am guard] 무엇이 실행될지 미리 확인할 수 없는 명령입니다 — 사람이 직접 판단해야 합니다\n" + \
              "\n".join(f"- {f['reason']}: {f['seg']}" for f in opaque) + "\n변수·치환·eval 없이 명령을 그대로 쓰면 자동으로 검사됩니다."
        return ("deny" if agent == "codex" else "ask"), msg

    missing, granted = [], []
    for f in findings:
        slug, p = proj(f.get("where") or cwd)
        f["slug"] = slug
        (granted if p and S.has_approval(p, f["gate"], f.get("ref")) else missing).append(f)
    for f in granted:
        S.event(projects[f["slug"]], f"승인된 작업 실행: {f['gate']}" + (f"({f['ref']})" if f.get("ref") else ""))
    if not missing:
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
