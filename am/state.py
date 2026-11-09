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


def has_approval(p, gate, ref=None):
    """An approval without ref covers every instance of the gate; one with ref covers only that ref.
    An action whose target is unknown (ref=None) needs a ref-less (blanket) approval."""
    return any(a["gate"] == gate and a.get("ref") in (None, ref) for a in p["approvals"])


def approve(p, gate, ref=None, note=""):
    if gate not in GATES:
        raise StateError(f"알 수 없는 승인 항목: {gate} (가능: {', '.join(GATES)})")
    p["approvals"].append({"gate": gate, "ref": ref, "note": note, "at": now()})
    event(p, f"승인 기록: {gate}" + (f" ({ref})" if ref else ""))


def add_request(p, gate, ref=None, cmd="", impact="", risk="", project_scope=""):
    """Record that an action is waiting for the user's approval (shown in am report). Deduplicated."""
    reqs = p.setdefault("requests", [])
    if any(r["gate"] == gate and r.get("ref") == ref and r.get("cmd") == cmd for r in reqs):
        return
    reqs.append({"gate": gate, "ref": ref, "cmd": cmd, "impact": impact, "risk": risk, "at": now()})
    event(p, f"승인 요청: {gate}" + (f" ({ref})" if ref else ""))


def pending_requests(p):
    return [r for r in p.get("requests", []) if not has_approval(p, r["gate"], r.get("ref"))]


def set_phase(p, phase):
    if phase not in PHASES:
        raise StateError(f"알 수 없는 단계: {phase} (가능: {', '.join(PHASES)})")
    gate = PHASE_GATES.get(phase)
    if gate and not has_approval(p, gate):
        raise StateError(f"'{phase}'로 가려면 먼저 사용자 승인 '{gate}'({GATES[gate]})이 기록돼야 합니다: am approve {p['slug']} {gate}")
    if phase == "repo_ready" and not p.get("repo"):
        raise StateError("repo_ready 전에 am set <slug> --repo owner/name 으로 레포를 기록하세요")
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
        if not has_approval(p, "merge_main", pr_ref(t["pr"])):
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
    # 다른 모델을 쓸 수 없을 때(예: Codex 사용 기간 끝)만 같은 모델의 다른 세션이 검증해도 된다
    other_available = any(ok for a, ok in routing(root)["available"].items() if a != t["agent"])
    if by.split(":")[0] == t["agent"] and other_available:
        raise StateError(f"검증은 구현한 에이전트({t['agent']})와 다른 에이전트가 해야 합니다 (예: claude:reviewflow, codex:review)")
    t["verification"] = {**vals, "by": by, "summary": summary, "at": now()}
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
