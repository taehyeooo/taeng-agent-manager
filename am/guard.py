"""Execution-time permission guard — called by Claude Code / Codex PreToolUse hooks BEFORE a tool runs.

    python <manager>/am/guard.py claude|codex      (hook payload JSON on stdin)

Decisions
  forbidden → always blocked (승인 우회, 인증정보 노출, 상태 파일 위조, 관련 없는 프로젝트 수정)
  gated     → blocked unless projects/<slug>/project.json has a matching approval; the block message
              is the approval request (명령·영향 범위·위험) and is also recorded for `am report`
  approve   → `am approve` is never self-served: Claude asks the user in its permission UI,
              Codex is blocked (the user runs it with `! python -m am approve ...`)
  else      → allowed (reading, editing inside the worktree, tests, lint, build, local commits)
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

SEP = r"[^|;&\n]*"  # stay inside one shell command segment

FORBIDDEN = [
    (r"--dangerously-skip-permissions|--dangerously-bypass-approvals-and-sandbox|--yolo\b|--permission-mode[= ]+bypass",
     "승인 우회 옵션"),
    (r"--no-verify\b|--no-gpg-sign\b", "검증 단계 우회"),
    (r"\bgh\s+auth\s+token\b|\bprintenv\b|\b(cat|type|Get-Content|gc)\s" + SEP + r"(auth\.json|\.git-credentials|credentials\.json)",
     "인증정보 노출"),
    (r"\becho\s" + SEP + r"\$\{?(env:)?\w*(TOKEN|SECRET|PASSWORD|API_?KEY)\w*", "인증정보 노출"),
    (r"\bgit\s+push\b" + SEP + r"(--force(?![-\w])|\s-f\b)" + SEP + r"\b(main|master)\b", "main 강제 push"),
]

# (regex, gate, 영향 범위, 위험)
GATED = [
    (r"\bgh\s+repo\s+create\b", "repo_create", "GitHub에 새 레포가 생긴다", "공개 범위를 잘못 정하면 코드가 공개된다"),
    (r"\bgh\s+repo\s+edit\b" + SEP + r"--visibility", "repo_create", "레포 공개 범위가 바뀐다", "비공개 코드·이력이 공개될 수 있다"),
    (r"\bgh\s+repo\s+delete\b", "destructive_delete", "GitHub 레포와 이슈·PR이 지워진다", "되돌릴 수 없다"),
    (r"\bgh\s+pr\s+merge\b", "merge_main", "PR이 기본 브랜치(main)에 병합된다", "검증이 덜 된 코드가 main·배포로 간다"),
    (r"\bgit\s+push\b", "push", "커밋이 원격 저장소에 올라간다", "공개·공유되어 되돌리기 어렵고 CI·배포가 돌 수 있다"),
    (r"\b(vercel|netlify\s+deploy|fly\s+deploy|firebase\s+deploy|wrangler\s+(deploy|publish)|railway\s+up|docker\s+push|kubectl\s+(apply|delete)|eas\s+(submit|update))\b|\bnpm\s+run\s+deploy\b|\bdeploy\.sh\b",
     "deploy", "서비스가 배포된다", "사용자에게 바로 영향, 비용이 생길 수 있다"),
    (r"\b(supabase\s+db\s+(push|reset)|prisma\s+migrate\s+(deploy|reset)|prisma\s+db\s+push|drop\s+(table|database|schema)|truncate\s+table)\b",
     "db_change", "데이터베이스 구조·데이터가 바뀐다", "운영 데이터가 지워지거나 깨질 수 있다"),
    (r"\bnpm\s+(i|install)\b" + SEP + r"(\s-g\b|--global)|\bSet-ExecutionPolicy\b|\bsudo\b|\bsetx\b|\breg\s+(add|delete)\b|\bgit\s+config\s+--(global|system)\b|\b(winget|choco|scoop)\s+install\b|\bicacls\b|\bchmod\s+777\b",
     "system_permission", "이 PC의 전역 설정·설치·권한이 바뀐다", "다른 프로젝트와 시스템 보안에 영향"),
    (r"\b(cat|type|less|more|head|tail|Get-Content|gc|source)\s" + SEP + r"\.env\b(?!\.example)", "credentials",
     "비밀값(.env)을 읽는다", "키가 대화·로그에 남을 수 있다"),
]

DELETE_RE = r"\brm\s+-\w*[rR]\w*\b|\bRemove-Item\b" + SEP + r"-Recurse|\brmdir\s+/s\b|\bgit\s+clean\s+-\w*f|\bgit\s+reset\s+--hard\b|\bgit\s+branch\s+-D\b|\bgit\s+worktree\s+remove\b|\bgit\s+push\b" + SEP + r"(--delete\b|\s:\S)"
SAFE_DELETE = {"node_modules", "dist", "build", ".next", "out", "coverage", "__pycache__", ".pytest_cache", ".turbo", ".cache", "target"}
APPROVE_RE = r"\bam\s+approve\b"
STATE_WRITE_RE = r"(project\.json|\.am[/\\]local\.json)"
WRITE_OPS_RE = r"(?<![\d&])>(?!&)|\bsed\s+-i|\bSet-Content\b|\bOut-File\b|\btee\b|\bcp\b|\bmv\b|\bCopy-Item\b|\bMove-Item\b|\bpython\d?\s+-c\b|\bnode\s+-e\b"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}


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


def _segments(cmd):
    return [s for s in re.split(r"&&|\|\||;|\n|\|", cmd) if s.strip()]


def _is_safe_delete(seg):
    toks = [t.strip("'\"") for t in seg.split()[1:] if not t.startswith("-")]
    toks = [t for t in toks if t.lower() not in ("rm", "remove-item", "-recurse", "-force")]
    tmp = tempfile.gettempdir()
    return bool(toks) and all(Path(t).name in SAFE_DELETE or _under(t, tmp) for t in toks)


def _push_ref(seg, cwd):
    toks = [t for t in re.split(r"\s+", seg.split("push", 1)[1].strip()) if t and not t.startswith("-")]
    if len(toks) >= 2:
        return toks[1].split(":")[-1]
    return _git(cwd, "branch", "--show-current") or None


def classify_command(cmd, cwd):
    """→ list of findings: {kind: forbidden|gated|approve, gate?, reason, ref?, impact?, risk?}"""
    out = []
    for seg in _segments(cmd):
        for rx, why in FORBIDDEN:
            if re.search(rx, seg, re.I):
                out.append({"kind": "forbidden", "reason": why, "seg": seg.strip()})
        if re.search(APPROVE_RE, seg):
            out.append({"kind": "approve", "reason": "승인 기록은 사용자만", "seg": seg.strip()})
        if re.search(STATE_WRITE_RE, seg) and re.search(WRITE_OPS_RE, seg):
            out.append({"kind": "forbidden", "reason": "상태 파일을 am 없이 직접 수정(승인 위조 위험)", "seg": seg.strip()})
        if re.search(DELETE_RE, seg, re.I) and not _is_safe_delete(seg):
            out.append({"kind": "gated", "gate": "destructive_delete", "ref": None, "seg": seg.strip(),
                        "impact": "파일·브랜치·워크트리가 지워진다", "risk": "되돌릴 수 없을 수 있다"})
            if re.search(r"\bgit\s+push\b", seg):
                continue  # push --delete is a delete, not a publish
        for rx, gate, impact, risk in GATED:
            if re.search(rx, seg, re.I):
                ref = None
                if gate == "push":
                    ref = _push_ref(seg, cwd)
                elif gate == "merge_main":
                    m = re.search(r"\bmerge\s+#?(\d+)", seg)
                    ref = S.pr_ref(m.group(1)) if m else None
                out.append({"kind": "gated", "gate": gate, "ref": ref, "impact": impact, "risk": risk, "seg": seg.strip()})
        if re.search(r"\bgit\s+merge\b", seg) and _git(cwd, "branch", "--show-current") in ("main", "master"):
            out.append({"kind": "gated", "gate": "merge_main", "ref": None, "seg": seg.strip(),
                        "impact": "main에 직접 병합된다", "risk": "PR·CI·리뷰를 거치지 않는다"})
    return out


def classify_file(tool, path, cwd, root=None):
    root = Path(root or S.ROOT)
    out = []
    p = Path(path) if os.path.isabs(path) else Path(cwd) / path
    rel_mgr = _under(p, root)
    if tool in WRITE_TOOLS:
        if rel_mgr and (p.name == "project.json" and _under(p, root / "projects") or _under(p, root / ".am")):
            out.append({"kind": "forbidden", "reason": "상태 파일을 am 없이 직접 수정(승인 위조 위험)", "seg": str(p)})
        elif re.search(r"[/\\]knowledge-base[/\\]decisions[/\\](?!README)", str(p)):
            out.append({"kind": "gated", "gate": "architecture_policy", "ref": p.name, "seg": f"{tool} {p}",
                        "impact": "ADR(아키텍처·정책 결정)이 바뀐다", "risk": "이후 모든 작업의 기준이 바뀐다"})
        else:
            top = _git(cwd, "rev-parse", "--show-toplevel") or str(cwd)
            anc = next((a for a in [p, *p.parents] if a.exists()), p)
            other_repo = _git(anc, "rev-parse", "--show-toplevel")
            in_other_repo = bool(other_repo) and _norm(other_repo) != _norm(top)
            scratch = [tempfile.gettempdir(), Path.home() / ".claude", Path.home() / ".codex"]
            if not _under(p, top) and (in_other_repo or not any(_under(p, a) for a in scratch)):
                out.append({"kind": "forbidden", "reason": f"지금 작업 폴더({top}) 밖 파일 수정 — 관련 없는 프로젝트·매니저의 직접 코딩 금지", "seg": str(p)})
    if tool in ("Read", "view") and re.search(r"(^|[/\\])\.env(\.(?!example)[\w.]+)?$", str(p)):
        out.append({"kind": "gated", "gate": "credentials", "ref": None, "seg": f"Read {p}",
                    "impact": "비밀값(.env)을 읽는다", "risk": "키가 대화·로그에 남을 수 있다"})
    return out


def extract(payload):
    """Claude Code and Codex payloads → (tool, command|None, file_path|None, cwd)."""
    tool = payload.get("tool_name") or payload.get("tool") or ""
    ti = payload.get("tool_input") or payload.get("input") or {}
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if isinstance(cmd, list):
        cmd = " ".join(map(str, cmd))
    path = (ti.get("file_path") or ti.get("path") or ti.get("notebook_path")) if isinstance(ti, dict) else None
    cwd = payload.get("cwd") or os.getcwd()
    return tool, cmd, path, cwd


def decide(payload, agent="claude", root=None):
    """→ (decision, message). decision: allow | deny | ask"""
    tool, cmd, path, cwd = extract(payload)
    findings = []
    if cmd:
        findings += classify_command(cmd, cwd)
    if path:
        findings += classify_file(tool, path, cwd, root)
    if not findings:
        return "allow", ""

    forb = [f for f in findings if f["kind"] == "forbidden"]
    if forb:
        return "deny", "[am guard] 금지된 작업 — 승인으로도 풀 수 없습니다\n" + "\n".join(f"- {f['reason']}: {f['seg']}" for f in forb)

    if any(f["kind"] == "approve" for f in findings):
        msg = "[am guard] 승인 기록은 사용자가 직접 확인해야 합니다. 사용자가 이 대화에서 명시적으로 승인한 게 맞을 때만 허용하세요."
        if agent == "codex":
            return "deny", msg + "\n사용자에게 이 명령을 직접 실행해 달라고 요청하세요: " + cmd
        return "ask", msg

    slug = resolve_project(cwd, root)
    p = S.load(slug, root) if slug else None
    missing = [f for f in findings if f["kind"] == "gated" and not (p and S.has_approval(p, f["gate"], f.get("ref")))]
    if not missing:
        if p:
            S.event(p, "승인된 작업 실행: " + ", ".join(f["gate"] + (f"({f['ref']})" if f.get("ref") else "") for f in findings))
            S.save(p, root)
        return "allow", ""

    lines = ["[am guard] 사용자 승인이 필요합니다 — 실행하지 않았습니다."]
    for f in missing:
        lines += [f"■ {f['gate']} — {S.GATES[f['gate']]}" + (f" ({f['ref']})" if f.get("ref") else ""),
                  f"  명령: {f['seg']}",
                  f"  영향 범위: {f['impact']}" + (f" · 프로젝트 {slug}" if slug else " · 관리 대상이 아닌 레포"),
                  f"  위험: {f['risk']}"]
        if p:
            S.add_request(p, f["gate"], f.get("ref"), f["seg"], f["impact"], f["risk"])
    if p:
        S.save(p, root)
        refs = " ".join(f"&& python -m am approve {slug} {f['gate']}" + (f" --ref {f['ref']}" if f.get("ref") else "") for f in missing)
        lines.append("위 내용을 사용자에게 보여 주고 승인받은 뒤 기록하고 다시 실행: " + refs.removeprefix("&& "))
    elif agent == "claude":
        return "ask", "\n".join(lines)  # 관리 대상이 아닌 레포: 사람이 직접 판단
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
        risky = re.search(r"git\s+push|gh\s+(repo|pr)|rm\s+-|Remove-Item|deploy|\.env|am\s+approve", raw)
        decision, msg = ("deny" if risky else "allow"), f"[am guard] 가드 오류로 막았습니다: {e}"
    if decision == "deny":
        print(msg, file=sys.stderr)
        return 2
    if decision == "ask":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
