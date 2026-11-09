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
GATES = {
    "spec": "요구사항·정책 확정",
    "repo_create": "GitHub 레포 생성·공개 설정",
    "merge_main": "main 병합",
    "push": "원격 push",
    "deploy": "배포",
    "destructive_delete": "파괴적인 파일 삭제",
    "db_change": "DB 변경·삭제",
    "credentials": "인증정보 접근",
    "system_permission": "시스템 권한 변경",
    "paid_service": "유료 서비스 사용",
}
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
    return any(a["gate"] == gate and (ref is None or a.get("ref") == ref) for a in p["approvals"])


def approve(p, gate, ref=None, note=""):
    if gate not in GATES:
        raise StateError(f"알 수 없는 승인 항목: {gate} (가능: {', '.join(GATES)})")
    p["approvals"].append({"gate": gate, "ref": ref, "note": note, "at": now()})
    event(p, f"승인 기록: {gate}" + (f" ({ref})" if ref else ""))


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


def add_task(p, title, agent, task_id=None):
    if agent not in AGENTS:
        raise StateError(f"agent는 {AGENTS} 중 하나")
    tid = task_id or f"T{len(p['tasks']) + 1}"
    if any(t["id"] == tid for t in p["tasks"]):
        raise StateError(f"이미 있는 작업 id: {tid}")
    t = {"id": tid, "title": title, "agent": agent, "status": "todo",
         "branch": None, "worktree": None, "pr": None, "dispatch": None, "note": ""}
    p["tasks"].append(t)
    event(p, f"작업 추가 {tid} ({agent}): {title}")
    return t


TASK_FIELDS = ["status", "agent", "branch", "worktree", "pr", "dispatch", "note"]


def update_task(p, task_id, **fields):
    t = next((t for t in p["tasks"] if t["id"] == task_id), None)
    if not t:
        raise StateError(f"작업이 없습니다: {task_id}")
    if fields.get("status") and fields["status"] not in TASK_STATUSES:
        raise StateError(f"status는 {TASK_STATUSES} 중 하나")
    if fields.get("agent") and fields["agent"] not in AGENTS:
        raise StateError(f"agent는 {AGENTS} 중 하나")
    changed = {k: v for k, v in fields.items() if v is not None and k in TASK_FIELDS}
    t.update(changed)
    event(p, f"작업 {task_id} 변경: " + ", ".join(f"{k}={v}" for k, v in changed.items()))
    return t


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
            "dispatched": f"{t['id']} 작업자 보고 대기 (dispatch {t['dispatch']})",
            "review": f"{t['id']} 검증: 테스트 + reviewflow 리뷰 → main 병합 승인 요청",
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
