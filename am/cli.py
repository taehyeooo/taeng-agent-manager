"""am — agent-manager CLI. Tool-neutral: Claude Code and Codex managers both drive projects
through these commands. Run: python -m am <command> (from the repo root)."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import state as S


def _p(args):
    return S.load(args.slug, args.root)


def cmd_new(a):
    p = S.new_project(a.slug, a.name or a.slug, a.idea, a.root)
    print(f"생성: projects/{p['slug']}/project.json (phase=planning)")
    print("다음:", S.next_action(p).replace("<slug>", p["slug"]))


def cmd_list(a):
    ps = S.all_projects(a.root)
    if not ps:
        print("프로젝트 없음 — am new <slug> --idea \"...\"")
        return
    for p in ps:
        open_n = sum(t["status"] != "done" for t in p["tasks"])
        print(f"{p['slug']:<24} {p['phase']:<11} 작업 {open_n}/{len(p['tasks'])} 남음  {p.get('repo') or '-'}")


def cmd_status(a):
    if not a.slug:
        return cmd_list(a)
    p = _p(a)
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return
    local = S.load_local(a.root)["paths"].get(p["slug"])
    print(f"# {p['name']} ({p['slug']})")
    print(f"단계: {p['phase']}   레포: {p.get('repo') or '-'}   로컬: {local or '(이 기기에 없음 — am restore)'}")
    print(f"아이디어: {p['idea']}")
    if p["approvals"]:
        print("승인:", ", ".join(a_["gate"] + (f"({a_['ref']})" if a_.get("ref") else "") for a_ in p["approvals"]))
    for t in p["tasks"]:
        extra = " ".join(f"{k}={t[k]}" for k in ("branch", "pr", "dispatch") if t.get(k))
        print(f"  [{t['status']:<10}] {t['id']} ({t['agent']}) {t['title']} {extra}")
    print("다음:", S.next_action(p).replace("<slug>", p["slug"]))


def cmd_phase(a):
    p = _p(a)
    S.set_phase(p, a.phase)
    S.save(p, a.root)
    print(f"{p['slug']}: phase={p['phase']}")


def cmd_approve(a):
    p = _p(a)
    S.approve(p, a.gate, a.ref, a.note)
    S.save(p, a.root)
    print(f"승인 기록: {a.gate}" + (f" ({a.ref})" if a.ref else ""))


def cmd_require(a):
    """Exit 0 if the approval exists, 1 otherwise — call this before any gated action."""
    p = _p(a)
    if S.has_approval(p, a.gate, a.ref):
        print(f"승인 있음: {a.gate}")
        return
    print(f"승인 없음: {a.gate}" + (f" ({a.ref})" if a.ref else "") + f" — {S.GATES.get(a.gate, '?')}. 사용자에게 물어보세요.")
    sys.exit(1)


def cmd_set(a):
    p = _p(a)
    for k in ("repo", "test_cmd"):
        v = getattr(a, k)
        if v is not None:
            p[k] = v
            S.event(p, f"{k}={v}")
    S.save(p, a.root)
    if a.local_path:
        cfg = S.load_local(a.root)
        cfg["paths"][p["slug"]] = str(Path(a.local_path).resolve())
        S.save_local(cfg, a.root)
    print("저장됨")


def cmd_task_add(a):
    p = _p(a)
    t = S.add_task(p, a.title, a.agent, a.id)
    S.save(p, a.root)
    print(f"작업 {t['id']} ({t['agent']}): {t['title']}")


def cmd_task_set(a):
    p = _p(a)
    S.update_task(p, a.id, status=a.status, agent=a.agent, branch=a.branch, worktree=a.worktree,
                  pr=a.pr, dispatch=a.dispatch, note=a.note)
    S.save(p, a.root)
    print(f"작업 {a.id} 갱신")


def cmd_restore(a):
    """On a new laptop: clone every project repo missing locally. Dry-run unless --apply."""
    cfg = S.load_local(a.root)
    ws = a.workspace or cfg.get("workspace_root")
    if not ws:
        print("작업 폴더를 정하세요: am restore --workspace <폴더> (한 번 정하면 .am/local.json에 저장)")
        sys.exit(1)
    cfg["workspace_root"] = str(Path(ws).resolve())
    todo = []
    for p in S.all_projects(a.root):
        if not p.get("repo"):
            continue
        path = cfg["paths"].get(p["slug"])
        if path and Path(path).exists():
            continue
        todo.append((p, Path(cfg["workspace_root"]) / p["slug"]))
    if not todo:
        print("복원할 것 없음 — 모든 프로젝트 레포가 이 기기에 있습니다")
    for p, dest in todo:
        clone = ["gh", "repo", "clone", p["repo"], str(dest)]
        add = ["orca", "repo", "add", "--path", str(dest)]
        if not a.apply:
            print("계획:", " ".join(clone), "&&", " ".join(add))
            continue
        if not dest.exists():
            subprocess.run(clone, check=True)
        subprocess.run(add, check=False)  # Orca not running is not fatal
        cfg["paths"][p["slug"]] = str(dest)
        print(f"복원: {p['slug']} → {dest}")
    S.save_local(cfg, a.root)
    if todo and not a.apply:
        print("실행하려면 --apply")


def build_parser():
    ap = argparse.ArgumentParser(prog="am", description="agent-manager: 여러 프로젝트의 단계·승인·작업 상태")
    ap.add_argument("--root", default=None, help=argparse.SUPPRESS)  # tests point this at a temp dir
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", help="새 프로젝트(아이디어) 등록")
    s.add_argument("slug"); s.add_argument("--idea", required=True); s.add_argument("--name")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("list", help="전체 프로젝트"); s.set_defaults(fn=cmd_list)

    s = sub.add_parser("status", help="프로젝트 상태와 다음 할 일")
    s.add_argument("slug", nargs="?"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("phase", help="단계 변경 (승인 필요한 단계는 확인)")
    s.add_argument("slug"); s.add_argument("phase", choices=S.PHASES); s.set_defaults(fn=cmd_phase)

    s = sub.add_parser("approve", help="사용자 승인 기록 (사용자가 실제로 승인했을 때만)")
    s.add_argument("slug"); s.add_argument("gate", choices=list(S.GATES))
    s.add_argument("--ref"); s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("require", help="승인 있는지 확인 (없으면 종료 코드 1)")
    s.add_argument("slug"); s.add_argument("gate"); s.add_argument("--ref")
    s.set_defaults(fn=cmd_require)

    s = sub.add_parser("set", help="레포·테스트 명령·로컬 경로 기록")
    s.add_argument("slug"); s.add_argument("--repo"); s.add_argument("--test-cmd", dest="test_cmd")
    s.add_argument("--local-path"); s.set_defaults(fn=cmd_set)

    t = sub.add_parser("task", help="작업").add_subparsers(dest="tcmd", required=True)
    s = t.add_parser("add"); s.add_argument("slug"); s.add_argument("title")
    s.add_argument("--agent", required=True, choices=S.AGENTS); s.add_argument("--id")
    s.set_defaults(fn=cmd_task_add)
    s = t.add_parser("set"); s.add_argument("slug"); s.add_argument("id")
    s.add_argument("--status", choices=S.TASK_STATUSES); s.add_argument("--agent", choices=S.AGENTS)
    for f in ("branch", "worktree", "pr", "dispatch", "note"):
        s.add_argument(f"--{f}")
    s.set_defaults(fn=cmd_task_set)

    s = sub.add_parser("restore", help="다른 기기에서: 없는 프로젝트 레포 clone + Orca 등록")
    s.add_argument("--workspace"); s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_restore)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    a = build_parser().parse_args(argv)
    try:
        a.fn(a)
    except S.StateError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(2)
