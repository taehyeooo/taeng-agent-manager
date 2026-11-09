# agent-manager MVP 아키텍처

작성 2026-11-09. 목표: Orca에서 이 레포를 열고 서비스 아이디어를 말하면, 매니저가 기획 → 레포 → 개발 위임 → 테스트·리뷰 → 보고까지 관리한다.

## 1. 조사한 환경 (2026-11-09, Windows 11)
| 도구 | 상태 |
|---|---|
| Claude Code | 2.1.319 — 사용 가능, taeng-marketplace 플러그인 설치됨 |
| Codex CLI | **설치 안 됨** (`~/.codex/hooks.json`만 있음 — Orca가 만든 것) |
| Orca | 1.4.253 — 실행 중, runtime ready. `orchestration`(task·dispatch·worker·gate), `worktree create`, `agent launch --agent codex\|claude` |
| GitHub CLI | 2.102.0 — taehyeooo 로그인됨 |
| git / python / node | 2.55 / 3.14 / 24 |

## 2. 재사용 vs 새로 만드는 것
| 필요한 기능 | 담당 | 비고 |
|---|---|---|
| 아이디어 → PRD → spec(R-ID) → plan, 객관식 정책 질문 | **intaeng** feature-spec | 경로 지정 가능 → `projects/<slug>/docs/`에 쓴다 |
| 프로젝트 레포의 규칙·지식베이스·커밋 훅·CI | **intaeng** knowledge-base | 새 레포마다 설치 |
| 이슈 → 브랜치 → 워크트리, 자율 모드 권한 묶음 | **intaeng** start-task | |
| 작업 지시문(맡은 파일·테스트 먼저·PR까지·머지 금지) | **intaeng** delegate-pr | 지시문을 Orca 워커에 넘긴다 |
| PR 마무리·순서대로 머지·워크트리 정리 | **intaeng** wrap-up, merge-train | 머지는 승인 후 |
| 레포 안 인계(now.md)·이어하기 | **intaeng** resume | |
| 요구사항 구현 점검 | **intaeng** spec-check | 완료 보고의 근거 |
| 코드 리뷰(4관점 + 검증) | **reviewflow** | Claude 전용 → Codex 매니저는 Claude 워커로 |
| API 스모크·QA 데이터·시나리오 | **qaflow** | 서버·앱이 생긴 프로젝트에서 설정 후 |
| 커밋 메시지 가드·배포·로컬 서버 | **devflow** | 배포는 승인 후 |
| Claude/Codex 워커 실행·워크트리 격리·완료 보고·질문 | **Orca** orchestration | 새로 만들지 않음 |
| **여러 프로젝트의 단계·승인 기록·작업 배정** | **새로: `am` CLI** | intaeng은 레포 하나 단위라 없음 |
| **다른 노트북에서 복원** | **새로: `am restore`** | Orca 상태는 기기 로컬이라 없음 |
| **매니저 규칙(Claude·Codex 공통)** | **새로: AGENTS.md** | CLAUDE.md는 `@AGENTS.md` |

## 3. 구조
```
taeng-agent-manager/            ← Orca에서 여는 매니저 레포
  AGENTS.md / CLAUDE.md         매니저 규칙 (Codex는 AGENTS.md, Claude는 CLAUDE.md → AGENTS.md)
  am/                           CLI (Python 표준 라이브러리만) — python -m am
  projects/<slug>/project.json  프로젝트 상태: phase·repo·test_cmd·approvals·tasks·history  (git 공유)
  projects/<slug>/docs/         기획 중 spec·plan (레포 생성 전에도 다른 기기에서 보임)
  .am/local.json                기기별 경로(workspace_root, slug→로컬 경로)  (git 제외)

<workspace>/<slug>/             ← 프로젝트마다 별도 GitHub 레포 (intaeng 지식베이스 설치)
  └ Orca 워크트리들             ← 작업마다 하나, 워커(claude/codex)가 PR까지
```

```
사용자 ──아이디어──▶ 매니저(Claude 또는 Codex, 이 레포)
                      │ am new / approve / phase / task         (상태 = projects/*.json)
                      │ intaeng 기획 → spec·plan
                      │ [승인] gh repo create → intaeng 지식베이스
                      ▼
            orca orchestration worker-start --agent codex|claude --worktree new-top-level
                      │                      (프로젝트 레포의 새 워크트리, PR까지, 머지 안 함)
                      ▼ worker_done
            매니저 검증: test_cmd · diff 범위 · reviewflow → [승인] main 병합 → am task done
```

## 4. 상태 모델
- phase: `planning → spec_ready → repo_ready → developing → done` (+ `paused`).
  `spec_ready`는 승인 `spec`, `repo_ready`는 승인 `repo_create`와 기록된 repo가 있어야 들어간다(CLI가 막음).
- approvals: `{gate, ref, note, at}` 누적. 다른 세션·다른 에이전트가 "이미 승인됐나"를 대화 기억 없이 안다.
- tasks: `{id, title, agent, status(todo|dispatched|review|done|failed|blocked), branch, worktree, pr, dispatch, note}`.
- `am status <slug>`의 "다음:" 줄이 이어하기 힌트.

## 5. 결정
- **CLI는 Python 표준 라이브러리** — intaeng 스크립트와 같은 방식, 설치 없이 Claude·Codex 어디서나 `python -m am`.
- **디스패치는 Orca에 맡긴다** — worker-start가 워크트리 생성·에이전트 실행·완료 메일·질문을 이미 한다. `am`은 dispatch id만 기록.
- **기획 문서는 레포 생성 전 매니저 레포에** — 요구사항 10(다른 노트북)과 레포 생성 승인 전 기획을 함께 만족.
- **기기 경로는 git 밖** — 같은 project.json을 노트북마다 다른 폴더에서 쓴다.
- **승인은 기록이지 우회가 아님** — `am approve`는 사용자가 OK한 뒤에만 쓴다(AGENTS.md).

## 6. 개발 계획
| # | 내용 | 담당 | 상태 |
|---|---|---|---|
| 1 | `am` 핵심: new·list·status·phase·approve·require·set·task·restore + 테스트 | claude(매니저) | 완료 |
| 2 | `am doctor` — 도구 설치·gh 로그인·Orca runtime 확인 | **codex** (첫 위임) | 완료 — 매니저 검증에서 Windows .cmd 버그 1건 발견 → 같은 워커가 수정 |
| 3 | `am brief <slug> <task>` — intaeng delegate-pr 템플릿을 채운 지시문 파일 생성 | codex | 예정 |
| 4 | `am dispatch` — brief + `orca orchestration worker-start` 실행 + dispatch 기록 한 번에 | claude | 예정 |
| 5 | `am verify` — 워크트리에서 test_cmd 실행·diff 범위 확인·결과 기록 | codex | 예정 |
| 6 | 첫 실제 프로젝트로 끝까지 리허설(작은 웹앱) | 전체 | 예정 |
| 7 | 매니저 레포에 intaeng 지식베이스(규칙·CI) 적용 여부 결정 | 사용자 | 질문 |
| 8 | 클라우드 실행(Orca 원격 환경 `--environment`) | — | 나중 |
