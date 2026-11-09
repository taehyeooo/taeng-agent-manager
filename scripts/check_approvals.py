"""CI: approvals in projects/*/project.json are append-only between two commits.

    python scripts/check_approvals.py <base> <head>

Fails if any approval that existed at <base> was changed, reordered or removed at <head>.
New approvals are allowed (they can only be written through `am approve`, which the guard routes to the user).
"""
import json
import subprocess
import sys


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout


def approvals_at(rev, path):
    code, out = git("show", f"{rev}:{path}")
    return json.loads(out).get("approvals", []) if code == 0 else None


def main(base, head):
    sys.stdout.reconfigure(encoding="utf-8")
    if not base or set(base) == {"0"} or git("cat-file", "-e", f"{base}^{{commit}}")[0] != 0:
        print(f"비교할 이전 커밋 없음({base or '-'}) — 첫 push라 건너뜀")
        return 0
    _, files = git("diff", "--name-only", base, head, "--", "projects/*/project.json")
    bad = 0
    for f in filter(None, files.splitlines()):
        old, new = approvals_at(base, f), approvals_at(head, f)
        if old is None:
            continue  # 새 프로젝트
        if new is None or new[:len(old)] != old:
            print(f"::error file={f}::기존 승인 기록이 바뀌거나 지워졌습니다 (append-only)")
            bad += 1
        else:
            print(f"ok {f}: 승인 {len(old)} → {len(new)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:3] + [None, None])[:2]))
