"""Project state: one JSON file per project under projects/<slug>/project.json (git-tracked,
so any laptop that clones this repo sees the same state). Machine-specific paths live in
.am/local.json (git-ignored)."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PHASES = ["planning", "spec_ready", "repo_ready", "developing", "done", "paused"]
# Moving INTO a phase requires this approval gate to be recorded first.
PHASE_GATES = {"spec_ready": "spec", "repo_ready": "repo_create"}
# Gates the agent must get the user's OK for (AGENTS.md "승인 규칙"). `ref` narrows a gate
# to one instance, e.g. merge_main ref=PR#3.
# 값은 사용자에게 그대로 보여 줄 쉬운 말 (개발 용어 없이)
GATES = {
    "spec": "기획 내용(무엇을 만들고 어떤 규칙으로 할지) 확정하기",
    "repo_create": "GitHub에 새 저장소 만들기 (또는 공개/비공개 바꾸기)",
    "merge_main": "작업한 코드를 완성본(main)에 합치기",
    "push": "내 PC의 코드를 GitHub에 올리기",
    "deploy": "서비스를 실제로 배포하기 (사용자에게 공개)",
    "destructive_delete": "파일·작업 기록을 지우기 (되살리기 어려움)",
    "db_change": "데이터베이스의 구조나 데이터 바꾸기",
    "credentials": "비밀번호·API 키 같은 비밀값 읽기",
    "system_permission": "이 PC 전체 설정 바꾸기 / 프로그램을 PC 전체에 설치하기",
    "paid_service": "돈이 드는 서비스 쓰기",
    "architecture_policy": "프로젝트의 큰 규칙·구조 결정 바꾸기",
}
VERIFY_KEYS = ["tests", "review", "security"]  # 검증 에이전트가 채우는 칸 — 모두 pass여야 review/done
AGENTS = ["claude", "codex"]
TASK_STATUSES = ["todo", "dispatched", "review", "done", "failed", "blocked"]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")


class StateError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def projects_dir(root=None):
    return Path(root or ROOT) / "projects"


def project_file(slug, root=None):
    return projects_dir(root) / slug / "project.json"


def load(slug, root=None):
    f = project_file(slug, root)
    if not f.exists():
        raise StateError(f"프로젝트가 없습니다: {slug}")
    return json.loads(f.read_text(encoding="utf-8"))


def save(p, root=None):
    f = project_file(p["slug"], root)
    f.parent.mkdir(parents=True, exist_ok=True)
    p["updated_at"] = now()
    f.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def all_projects(root=None):
    d = projects_dir(root)
    if not d.exists():
        return []
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*/project.json"))]


def event(p, text):
    p.setdefault("history", []).append({"at": now(), "event": text})


def new_project(slug, name, idea, root=None):
    if not SLUG_RE.match(slug):
        raise StateError("slug는 소문자·숫자·하이픈 2~39자 (GitHub 레포 이름으로 씀)")
    if project_file(slug, root).exists():
        raise StateError(f"이미 있는 프로젝트: {slug}")
    p = {
        "slug": slug, "name": name, "idea": idea, "phase": "planning",
        "repo": None, "test_cmd": None,
        "approvals": [], "tasks": [], "history": [], "created_at": now(),
    }
    event(p, "프로젝트 생성 (planning)")
    save(p, root)
    return p


WORK_BRANCHES = "work-branches"  # push 승인 ref: main/master가 아닌 작업 브랜치 전부 (main은 절대 포함 안 됨)


def _ref_covers(approved, ref, gate):
    if approved is None or approved == ref:
        return True
    # 사용자 승인 2026-11-10: "작업 브랜치 올리기는 묻지 않고" — 브랜치 이름이 분명하고 main이 아닐 때만
    return (approved == WORK_BRANCHES and gate == "push" and ref is not None
            and re.fullmatch(r"[\w./-]+", ref) is not None and ref not in ("main", "master"))


def has_approval(p, gate, ref=None):
    """An approval without ref covers every instance of the gate; one with ref covers only that ref.
    An action whose target is unknown (ref=None) needs a ref-less (blanket) approval.
    ref "work-branches" (push only) covers every branch except main/master — never API changes or unknown targets.
    Approvals recorded under auto_gates ("auto": true) are only a log: while the gate is listed it passes anyway
    (allow_gate), and once the user takes it out of config/guard.json they must not keep approving it."""
    return any(a["gate"] == gate and not a.get("auto") and _ref_covers(a.get("ref"), ref, gate) for a in p["approvals"])


def approve(p, gate, ref=None, note="", auto=False):
    if gate not in GATES:
        raise StateError(f"알 수 없는 승인 항목: {gate} (가능: {', '.join(GATES)})")
    rec = {"gate": gate, "ref": ref, "note": note, "at": now()}
    if auto:
        rec["auto"] = True
    p["approvals"].append(rec)
    event(p, f"승인 기록: {gate}" + (f" ({ref})" if ref else "") + (" — auto_gates" if auto else ""))


# ---- 사용자 전용 가드 설정: ~/.config/agent-manager/guard.json (레포 밖, 기기마다 사용자가 직접 만든다) ----
# {"auto_gates": ["push", "merge_main", ...], "opaque": "allow"|"ask", "code_execution": "allow"|"ask"}
# auto_gates → 그 승인 항목은 묻지 않고 진행하고 기록만 남긴다. 파일·키가 없으면 지금처럼 전부 묻는다.
# 레포 밖에 두는 이유: 레포 안 파일은 git reset·checkout·stash·merge 등 끝없는 간접 경로로 되돌릴 수 있다.
# 레포 밖 파일은 git이 건드리지 못하고, 직접 쓰기·삭제·이동은 가드가 확인(Claude)·차단(Codex)한다.
# 레포 안 config/guard.json은 하위 호환으로 opaque만 읽는다(auto_gates는 무시하고 경고).
AUTO_NOTE = "auto_gates 사용자 설정"
USER_GUARD_FILE = Path.home() / ".config" / "agent-manager" / "guard.json"  # 테스트는 이 값을 임시 경로로 바꾼다
USER_GUARD_LABEL = "~/.config/agent-manager/guard.json"


def guard_config_file(root=None):
    """레포 안 config/guard.json (하위 호환: opaque만)."""
    return Path(root or ROOT) / "config" / "guard.json"


def user_guard_file():
    return Path(USER_GUARD_FILE)


def _read_config(f, label):
    """→ (dict or None, warnings). None when the file is missing or unusable."""
    if not f.exists():
        return None, []
    try:
        cfg = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"{label}을 읽지 못해 무시합니다(전부 묻기): {type(e).__name__}"]
    if not isinstance(cfg, dict):
        return None, [f"{label}은 JSON 객체여야 합니다 — 무시합니다(전부 묻기)"]
    return cfg, []


def guard_setting(key, root=None):
    """사용자 설정(레포 밖)을 먼저, 없으면 레포 안 config/guard.json(하위 호환). auto_gates는 쓰지 않는다.
    → (value or None, broken) — broken: 값을 찾을 파일이 있었지만 읽지 못함."""
    for f, label in ((user_guard_file(), USER_GUARD_LABEL), (guard_config_file(root), "config/guard.json")):
        cfg, warnings = _read_config(f, label)
        if warnings:
            return None, True
        if cfg is not None and key in cfg:
            return cfg[key], False
    return None, False


def auto_gates(root=None):
    """→ (set of gate names that run without asking, warnings). Only the user's file outside the repo counts;
    anything unreadable falls back to 'ask for all'."""
    warnings = []
    repo_cfg, _ = _read_config(guard_config_file(root), "config/guard.json")
    if repo_cfg is not None and "auto_gates" in repo_cfg:
        warnings.append(f"config/guard.json(레포 안)의 auto_gates는 무시합니다 — {USER_GUARD_LABEL}에 두세요")
    cfg, bad = _read_config(user_guard_file(), USER_GUARD_LABEL)
    warnings += bad
    if cfg is None:
        return set(), warnings
    names = cfg.get("auto_gates", [])
    if not isinstance(names, list):
        return set(), warnings + [f"{USER_GUARD_LABEL}의 auto_gates는 목록이어야 합니다 — 무시합니다(전부 묻기)"]
    gates = set()
    for n in names:
        if isinstance(n, str) and n in GATES:
            gates.add(n)
        else:
            warnings.append(f"{USER_GUARD_LABEL} auto_gates의 알 수 없는 이름 무시: {n!r} (가능: {', '.join(GATES)})")
    return gates, warnings


CODE_EXECUTION_NOTE = "자동 허용: 코드 실행 — 사용자 설정"


def code_execution(root=None):
    """→ ("allow"|"ask", warnings). "code_execution": "allow"면 짧은 코드 실행 지점(python -c·heredoc 프로그램·eval·
    변수로 정해지는 명령·sed e·git 별칭/rebase --exec·추적·미수정이 아닌 스크립트)의 확인을 허용+기록으로 바꾼다.
    auto_gates와 같이 레포 밖 사용자 파일만 인정한다(레포 안 값은 무시하고 경고). 그 밖의 값·손상된 파일은 ask."""
    warnings = []
    repo_cfg, _ = _read_config(guard_config_file(root), "config/guard.json")
    if repo_cfg is not None and "code_execution" in repo_cfg:
        warnings.append(f"config/guard.json(레포 안)의 code_execution은 무시합니다 — {USER_GUARD_LABEL}에 두세요")
    cfg, bad = _read_config(user_guard_file(), USER_GUARD_LABEL)
    warnings += bad
    value = (cfg or {}).get("code_execution", "ask")
    if value not in ("allow", "ask"):
        warnings.append(f"{USER_GUARD_LABEL}의 code_execution은 \"allow\" 또는 \"ask\"여야 합니다 — 무시합니다(묻기): {value!r}")
        value = "ask"
    return value, warnings


def auto_approve(p, gate, ref=None):
    """Record that a gated action ran without asking because the user listed the gate in auto_gates."""
    if not any(a["gate"] == gate and a.get("auto") and a.get("ref") == ref for a in p["approvals"]):
        p["approvals"].append({"gate": gate, "ref": ref, "note": AUTO_NOTE, "at": now(), "auto": True})
    event(p, f"자동 승인 실행: {gate}" + (f"({ref})" if ref else ""))


def allow_gate(p, gate, ref=None, root=None):
    """has_approval, or the gate is in auto_gates (then the automatic approval is recorded)."""
    if has_approval(p, gate, ref):
        return True
    if gate in auto_gates(root)[0]:
        auto_approve(p, gate, ref)
        return True
    return False


def add_request(p, gate, ref=None, cmd="", impact="", risk="", project_scope=""):
    """Record that an action is waiting for the user's approval (shown in am report). Deduplicated."""
    reqs = p.setdefault("requests", [])
    if any(r["gate"] == gate and r.get("ref") == ref and r.get("cmd") == cmd for r in reqs):
        return
    reqs.append({"gate": gate, "ref": ref, "cmd": cmd, "impact": impact, "risk": risk, "at": now()})
    event(p, f"승인 요청: {gate}" + (f" ({ref})" if ref else ""))


def pending_requests(p):
    return [r for r in p.get("requests", []) if not has_approval(p, r["gate"], r.get("ref"))]


def set_phase(p, phase, root=None):
    if phase not in PHASES:
        raise StateError(f"알 수 없는 단계: {phase} (가능: {', '.join(PHASES)})")
    gate = PHASE_GATES.get(phase)
    # repo_create는 저장소 이름 단위로 승인한다 — 이 프로젝트의 저장소 이름으로 찾는다
    ref = (p.get("repo") or "").lower() or None if gate == "repo_create" else None
    if phase == "repo_ready" and not p.get("repo"):
        raise StateError("repo_ready 전에 am set <slug> --repo owner/name 으로 레포를 기록하세요")
    if gate and not allow_gate(p, gate, ref, root):
        raise StateError(f"'{phase}'로 가려면 먼저 사용자 승인 '{gate}'({GATES[gate]})이 기록돼야 합니다: am approve {p['slug']} {gate}")
    old = p["phase"]
    p["phase"] = phase
    event(p, f"단계 {old} → {phase}")


def add_task(p, title, agent=None, task_id=None, kind="general", root=None):
    """agent=None → config/agents.json 선호대로 자동 배정 (이유를 note에 남긴다)."""
    auto = agent is None
    if auto:
        agent = pick_agent(kind, root)
    elif not routing(root)["available"].get(agent, True):
        raise StateError(f"{agent}는 사용 기간이 끝났습니다(config/agents.json) — 다른 에이전트로 배정하세요")
    if agent not in AGENTS:
        raise StateError(f"agent는 {AGENTS} 중 하나")
    tid = task_id or f"T{len(p['tasks']) + 1}"
    if any(t["id"] == tid for t in p["tasks"]):
        raise StateError(f"이미 있는 작업 id: {tid}")
    t = {"id": tid, "title": title, "agent": agent, "kind": kind, "status": "todo",
         "branch": None, "worktree": None, "pr": None, "dispatch": None,
         "note": f"자동 배정: {kind} → {agent} (config/agents.json 선호)" if auto else ""}
    p["tasks"].append(t)
    event(p, f"작업 추가 {tid} ({agent}{', 자동' if auto else ''}): {title}")
    return t


TASK_FIELDS = ["status", "agent", "branch", "worktree", "pr", "dispatch", "note"]


def check_task_invariants(p, t, root=None):
    st = t.get("status")
    if st not in ("review", "done"):
        return
    if not verified(t):
        raise StateError(f"{t['id']}: 검증(tests·review·security 모두 pass)이 기록돼야 '{st}'로 갈 수 있습니다 — am task verify")
    by = (t.get("verification") or {}).get("by", "")
    other_available = any(ok for a, ok in routing(root)["available"].items() if a != t["agent"])
    if by.split(":")[0] == t["agent"] and other_available:
        raise StateError(f"{t['id']}: 검증자({by})가 구현 에이전트({t['agent']})와 같습니다 — 다른 에이전트가 다시 검증해야 합니다")
    if st == "done":
        if not t.get("pr"):
            raise StateError(f"{t['id']}: PR 번호가 기록돼야 done으로 갈 수 있습니다 (am task set --pr <번호>)")
        if not allow_gate(p, "merge_main", pr_ref(t["pr"]), root):
            raise StateError(f"{t['id']}: {pr_ref(t['pr'])} main 병합 승인이 기록되지 않았습니다")


def update_task(p, task_id, root=None, **fields):
    t = next((t for t in p["tasks"] if t["id"] == task_id), None)
    if not t:
        raise StateError(f"작업이 없습니다: {task_id}")
    if fields.get("status") and fields["status"] not in TASK_STATUSES:
        raise StateError(f"status는 {TASK_STATUSES} 중 하나")
    if fields.get("agent") and fields["agent"] not in AGENTS:
        raise StateError(f"agent는 {AGENTS} 중 하나")
    changed = {k: v for k, v in fields.items() if v is not None and k in TASK_FIELDS}
    if changed.get("agent") and changed["agent"] != t["agent"] and not routing(root)["available"].get(changed["agent"], True):
        raise StateError(f"{changed['agent']}는 사용 기간이 끝났습니다(config/agents.json) — 다른 에이전트로 배정하세요")
    check_task_invariants(p, {**t, **changed}, root)  # 바뀐 뒤의 상태로 검사 (에이전트·PR을 나중에 바꿔도 빠져나가지 못하게)
    t.update(changed)
    event(p, f"작업 {task_id} 변경: " + ", ".join(f"{k}={v}" for k, v in changed.items()))
    return t


def pr_ref(pr):
    return "PR#" + str(pr).lstrip("#").removeprefix("PR#")


def verified(t):
    v = t.get("verification") or {}
    return all(v.get(k) == "pass" for k in VERIFY_KEYS)


def verify_task(p, task_id, by, tests, review, security, summary="", files=None, root=None):
    """Record the verification agent's result. Anything other than pass keeps the task out of review/done."""
    t = next((t for t in p["tasks"] if t["id"] == task_id), None)
    if not t:
        raise StateError(f"작업이 없습니다: {task_id}")
    vals = {"tests": tests, "review": review, "security": security}
    for k, v in vals.items():
        if v not in ("pass", "fail", "skip"):
            raise StateError(f"{k}는 pass|fail|skip")
    # 교차 검증: 구현한 모델과 다른 모델이 검증한다 (예: codex 구현 → claude:reviewflow, claude 구현 → codex:review)
    if by.split(":")[0] not in AGENTS:
        raise StateError(f"검증자는 {AGENTS} 중 하나로 시작해야 합니다 (예: claude:reviewflow, codex:review)")
    # 다른 모델을 쓸 수 없을 때(예: Codex 사용 기간 끝)만 같은 모델의 다른 세션이 검증해도 된다 —
    # 그때는 검증 세션을 `<모델>:<dispatch id>`로 적고, 구현 dispatch와 달라야 한다
    other_available = any(ok for a, ok in routing(root)["available"].items() if a != t["agent"])
    if by.split(":")[0] == t["agent"]:
        if other_available:
            raise StateError(f"검증은 구현한 에이전트({t['agent']})와 다른 에이전트가 해야 합니다 (예: claude:reviewflow, codex:review)")
        session = by.partition(":")[2]
        if not re.fullmatch(r"ctx_[0-9a-f]+", session) or session == t.get("dispatch"):
            raise StateError(f"같은 모델로 검증할 때는 다른 세션이어야 합니다: --by {t['agent']}:<검증 워커 dispatch id> (구현 dispatch {t.get('dispatch')}와 달라야 함)")
    if t["status"] == "done" and any(v != "pass" for v in vals.values()):
        raise StateError(f"{task_id}는 이미 main에 병합된 작업입니다 — 문제는 새 작업(수정)으로 등록하세요: am task add")
    t["verification"] = {**vals, "by": by, "summary": summary, "at": now()}
    if t["status"] == "review" and not verified(t):
        t["status"] = "dispatched"  # 검토 대기였는데 검증 실패 → 다시 수정으로
        event(p, f"작업 {task_id} 검증 실패로 review → dispatched")
    if files:
        t["files"] = files
    event(p, f"작업 {task_id} 검증({by}): " + ", ".join(f"{k}={v}" for k, v in vals.items()))
    return t


TASK_KINDS = ["general", "design", "test", "docs"]


def routing(root=None, today=None):
    """config/agents.json → {'pick': {kind: agent}, 'available': {agent: bool}, 'days_left': int|None, ...}"""
    f = Path(root or ROOT) / "config" / "agents.json"
    cfg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    today = today or datetime.now().date()
    until = cfg.get("prefer_until")
    days_left = (datetime.fromisoformat(until).date() - today).days if until else None
    active = days_left is None or days_left >= 0
    prefer, after = cfg.get("prefer"), cfg.get("after", "claude")
    available = {a: True for a in AGENTS}
    if prefer and not active:
        available[prefer] = False  # 사용 기간이 끝난 에이전트
    pick = {k: (cfg.get("kinds", {}).get(k, prefer) if active else after) or "claude" for k in TASK_KINDS}
    return {"pick": pick, "available": available, "prefer": prefer, "until": until, "days_left": days_left, "active": active}


def pick_agent(kind="general", root=None, today=None):
    if kind not in TASK_KINDS:
        raise StateError(f"kind는 {TASK_KINDS} 중 하나")
    return routing(root, today)["pick"][kind]


def routing_warning(p=None, root=None, today=None):
    r = routing(root, today)
    if not r["prefer"] or r["days_left"] is None:
        return ""
    if not r["active"]:
        stuck = [t["id"] for t in (p or {}).get("tasks", []) if t["agent"] == r["prefer"] and t["status"] not in ("done",)]
        return f"{r['prefer']} 사용 기간({r['until']})이 끝났습니다 — 새 작업은 {r['pick']['general']}로 배정" + (
            f". {r['prefer']}에 남은 작업 재배정 필요: {', '.join(stuck)}" if stuck else "")
    if r["days_left"] <= 3:
        return f"{r['prefer']} 사용 기간 D-{r['days_left']} ({r['until']}까지) — 큰 작업은 지금 맡기거나 {r['pick']['general'] if not r['active'] else 'claude'}로 넘길 준비"
    return ""


def next_action(p):
    """What the manager should do next — the resume hint for a fresh session."""
    ph = p["phase"]
    if ph == "planning":
        return "기획: intaeng 기획(feature-spec)으로 spec·plan을 projects/<slug>/docs/ 에 쓰고, 사용자 확정 후 am approve <slug> spec"
    if ph == "spec_ready":
        return "레포 생성 승인 요청(이름·공개 범위) → am approve <slug> repo_create → gh repo create → am set --repo"
    if ph == "repo_ready":
        return "plan 단계를 작업으로 나눠 am task add (claude/codex 배정) → am phase <slug> developing"
    if ph == "developing":
        open_t = [t for t in p["tasks"] if t["status"] not in ("done",)]
        if not open_t:
            return "모든 작업 완료 — 최종 보고 후 am phase <slug> done"
        t = open_t[0]
        return {
            "todo": f"{t['id']} 위임: orca orchestration worker-start --agent {t['agent']} (AGENTS.md '위임')",
            "dispatched": f"{t['id']} 작업자 보고 대기 (dispatch {t['dispatch']}) → 보고 오면 교차 검증 후 am task verify",
            "review": f"{t['id']} 검증 통과 — main 병합 승인 요청 (명령·영향 범위·위험 표시) → am approve <slug> merge_main --ref PR#n",
            "failed": f"{t['id']} 실패 — 원인 확인 후 재위임 또는 사용자에게 보고",
            "blocked": f"{t['id']} 막힘 — note 확인: {t.get('note', '')}",
        }[t["status"]]
    if ph == "paused":
        return "일시 중지됨 — 사용자에게 재개 여부 확인"
    return "완료"


# ---- machine-local config (.am/local.json, git-ignored) ----

def local_file(root=None):
    return Path(root or ROOT) / ".am" / "local.json"


def load_local(root=None):
    f = local_file(root)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"workspace_root": None, "paths": {}}


def save_local(cfg, root=None):
    f = local_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
