"""am report <slug> — 완료·중간 보고. 대화 기억이 아니라 project.json 기록으로 만든다."""
from . import state as S

AUTO_PREFIXES = ("작업 추가", "작업 ", "단계 ", "승인된 작업 실행", "test_cmd=", "repo=")


def render(p):
    L = [f"# 보고 — {p['name']} ({p['slug']})", f"단계: {p['phase']} · 레포: {p.get('repo') or '-'} · 갱신 {p.get('updated_at', '-')}", ""]

    L += ["## 작업별 결과", "| 작업 | 수행 에이전트 | 상태 | 변경 파일·기능 | 테스트 | 리뷰 | 보안 | 검증자 |", "|---|---|---|---|---|---|---|---|"]
    for t in p["tasks"]:
        v = t.get("verification") or {}
        files = ", ".join(t.get("files") or []) or "-"
        L.append(f"| {t['id']} {t['title']} | {t['agent']} | {t['status']} | {files} | {v.get('tests', '-')} | {v.get('review', '-')} | {v.get('security', '-')} | {v.get('by', '-')} |")
    if not p["tasks"]:
        L.append("| (작업 없음) | | | | | | | |")

    L += ["", "## 실패·미해결"]
    bad = [t for t in p["tasks"] if t["status"] in ("failed", "blocked") or
           any(v == "fail" for k, v in (t.get("verification") or {}).items() if k in S.VERIFY_KEYS)]
    L += [f"- {t['id']} {t['title']} — {t['status']}: {(t.get('verification') or {}).get('summary') or t.get('note') or '사유 미기록'}" for t in bad] or ["- 없음"]

    L += ["", "## 자동 진행한 작업"]
    auto = [h for h in p.get("history", []) if h["event"].startswith(AUTO_PREFIXES)]
    L += [f"- {h['at'][:16]} {h['event']}" for h in auto[-15:]] or ["- 없음"]

    L += ["", "## 사용자 승인이 필요한 작업"]
    pend = S.pending_requests(p)
    for r in pend:
        L += [f"- **{r['gate']}** ({S.GATES.get(r['gate'], '?')})" + (f" `{r['ref']}`" if r.get("ref") else ""),
              f"  - 명령: `{r.get('cmd') or '-'}`", f"  - 영향 범위: {r.get('impact') or '-'}", f"  - 위험: {r.get('risk') or '-'}"]
    if not pend:
        L.append("- 없음")

    L += ["", "## 받은 승인"]
    L += [f"- {a['at'][:16]} {a['gate']}" + (f" ({a['ref']})" if a.get("ref") else "") + (f" — {a['note']}" if a.get("note") else "")
          for a in p["approvals"]] or ["- 없음"]
    L += ["", f"다음: {S.next_action(p).replace('<slug>', p['slug'])}"]
    return "\n".join(L)
