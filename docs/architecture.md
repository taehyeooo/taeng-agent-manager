# agent-manager MVP 아키텍처

작성 2026-11-09. 목표: Orca에서 이 레포를 열고 서비스 아이디어를 말하면, 매니저가 기획 → 레포 → 개발 위임 → 테스트·리뷰 → 보고까지 관리한다.

## 1. 조사한 환경 (2026-11-09, Windows 11)
| 도구 | 상태 |
|---|---|
| Claude Code | 2.1.319 — 사용 가능, taeng-marketplace 플러그인 설치됨 |
| Codex CLI | 0.167.0 — 2026-11-09 사용자 승인 후 설치·로그인(ChatGPT). PowerShell 실행 정책 CurrentUser RemoteSigned(승인). intaeng 0.6.0 플러그인 설치(승인) — design-flow 등 11개 스킬 사용 가능. `pre_tool_use` 훅 지원 |
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
- tasks: `{id, title, agent, status(todo|dispatched|review|done|failed|blocked), branch, worktree, pr, dispatch, note, files, verification}`.
  `review`·`done`은 `verification`(tests·review·security 모두 pass, 구현 모델과 다른 검증자)이 있어야 하고, `done`은 그 PR의 `merge_main` 승인도 있어야 한다.
- requests: 가드가 막은 승인 요청 `{gate, ref, cmd, impact, risk}` — 승인이 기록되면 대기 목록에서 빠진다(`am report`).
- `am status <slug>`의 "다음:" 줄이 이어하기 힌트.

## 5. 역할·신뢰 계층·실행 시점 강제 (2026-11-09 추가)
- 역할 4계층: 매니저(조율·승인 요청) → 코디네이터(Orca orchestration) → 작업 에이전트(Claude Code·Codex, 역할 고정 없음) → 검증 에이전트(구현 모델의 반대쪽). 세부는 AGENTS.md.
- 신뢰 계층: ① 구조·규칙 ② Lint·Test·CI ③ AGENTS.md·CLAUDE.md 지침(reviewflow) ④ 중요한 변경만 사용자.
- **`am guard`** (`am/guard.py`): Claude Code·Codex PreToolUse 훅이 도구 실행 **전에** 부른다. 훅은 레포의 `.claude/settings.json`(커밋됨 → 모든 워크트리에 적용)에 있고, 가드 위치는 기기마다 `~/.config/agent-manager/home`(`am setup`).

```
도구 호출 ─▶ 금지? ──예──▶ 차단 (승인으로도 불가)
              │아니오
              ▼
          am approve? ──예──▶ Claude: 사용자 확인 창 / Codex: 차단(사용자가 직접)
              │아니오
              ▼
          승인 필요 명령? ──아니오──▶ 허용 (읽기·수정·테스트·로컬 커밋)
              │예
              ▼
          cwd → git common dir → 프로젝트 → project.json에 gate(+ref) 승인? ──예──▶ 허용 + 기록
              │아니오
              ▼
          차단 + 승인 요청(명령·영향 범위·위험) 기록 → 매니저가 사용자에게 보여 줌
```
- 명령은 `&& ; | 줄바꿈` 단위로 나눠 하나씩 본다. `rm -rf node_modules` 같은 빌드 산출물·임시 폴더 삭제는 자동 진행.
- ref 단위 승인: `push`는 브랜치, `merge_main`은 `PR#n`, ref 없는 승인은 그 gate 전체.
- 가드 자체가 오류 나면 위험해 보이는 명령만 막는다(fail closed). 기기에 `am setup`이 안 돼 있으면 훅은 아무것도 하지 않는다 — `am doctor`·세션 시작 규칙으로 확인.
- CI(`.github/workflows/test.yml`): 테스트를 Ubuntu·Windows에서, 그리고 `project.json`의 기존 승인 기록이 바뀌거나 지워지면 실패(append-only, `scripts/check_approvals.py`).
- **가드는 명령을 해석한다**(정규식으로 문자열 전체를 훑지 않음):
  - 따옴표 밖에서만 `; | & { } ( )`·줄바꿈으로 나누고, `$(...)`·`` `...` `` 안의 명령도 따로 검사한다.
  - `bash -c`·`powershell -Command`(`` ` `` escape 해석)·`cmd /c`(`^` escape 해석)·`env`·`&`·`sudo`·`npx`/`pnpm dlx`/`npm exec`를 벗긴다.
  - git 앞 옵션(`-C <폴더>`는 그 폴더 기준), git 별칭(실제 내용으로 펼침, `!셸` 별칭은 사람 확인), gh `-R/--repo`(이 프로젝트 저장소와 다르면 금지), python 옵션(`-u -m am approve`)을 해석한다.
  - 명령어 자리만 보므로 커밋 메시지·echo 인수 속 글자는 무시한다.
  - 변수·eval·인코딩된 명령·짝 없는 치환·스크립트 안 subprocess처럼 미리 알 수 없는 실행은 사람에게 넘긴다(Claude 확인 창, Codex 차단).
- **한계(남는 위험) — 정직하게**: 가드는 안전벨트지 샌드박스가 아니다.
  - 실행 전에 알 수 없는 것: 파일로 된 스크립트(`python script.py`, `npm run x`의 내용) 안의 동작, PATH·셸 함수 바꿔치기, 이미 저장소 설정에 있던 훅.
  - CI의 append-only 검사는 **기존 승인이 바뀌거나 지워지는 것**만 잡는다. 새 승인이 정말 사용자에게서 왔는지는 증명하지 못한다 — 그건 가드(`am approve`는 사람 확인)에 기댄다.
  - 비밀값 유출·외부 저장소 변경을 CI가 막지는 못한다(가드가 실행 전에 막는 것이 전부).
  - 그래서 서버가 강제하는 마지막 겹이 필요하다: GitHub main 보호 규칙(PR·CI 통과 없이는 main에 못 들어감). 저장소 설정 변경이라 **사용자 승인 후 적용 — 아직 적용 안 됨**.
- **두 번째 그물(tripwire)**: 해석기가 명령을 볼 때마다 "봤다" 표시(`git:push`, `gh:pr.merge`, `cmd:vercel`…)를 남긴다. 명령줄(코드로 실행되는 인용문 포함, 커밋 메시지 같은 일반 인용문 제외)에 위험 단어(push·merge·approve·배포 도구·environ·project.json·no-verify·DB 변경어)가 있는데 그 단어를 설명하는 표시가 없으면 판단 불가로 사람에게 넘긴다. 해석기가 모르는 새 형태를 "모르는 것"으로 거르는 장치다.
- 교차 검증 기록(codex): 1차 우회 13건(`VerifierFindingsTest`) → 2차 11건·과잉 차단 4건(`VerifierRound2Test`) → 3차 9건·과잉 차단 3건(`VerifierRound3Test`: 붙여 쓴 옵션, 셸 조건문·역슬래시, 실행기 옵션, gh URL·-R 붙여쓰기, git -CX·--git-dir·core.hooksPath, 2> 리다이렉트, 상태 파일 rm/mv, done 뒤 실패 검증, 기간 끝난 에이전트 재배정). 3차부터는 사례별 수정과 함께 두 번째 그물을 더했다. 각 재현이 테스트로 남아 있다.

## 6. 결정
- **CLI는 Python 표준 라이브러리** — intaeng 스크립트와 같은 방식, 설치 없이 Claude·Codex 어디서나 `python -m am`.
- **디스패치는 Orca에 맡긴다** — worker-start가 워크트리 생성·에이전트 실행·완료 메일·질문을 이미 한다. `am`은 dispatch id만 기록.
- **기획 문서는 레포 생성 전 매니저 레포에** — 요구사항 10(다른 노트북)과 레포 생성 승인 전 기획을 함께 만족.
- **기기 경로는 git 밖** — 같은 project.json을 노트북마다 다른 폴더에서 쓴다.
- **승인은 기록이지 우회가 아님** — `am approve`는 사용자가 OK한 뒤에만 쓴다(AGENTS.md).

## 7. 개발 계획
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
| 9 | 역할 4계층·승인 3분류·신뢰 계층·`am guard`(실행 시점 권한 검사)·`am task verify`·`am report`·CI | claude(매니저) | 완료 |
| 10 | Codex 워커에 가드 연결 — Codex 훅 형식 확인 후 프로젝트 레포에 설치 | codex | 예정 (Codex는 `pre_tool_use` 훅 지원 확인, 레포 단위 설정 위치는 미확인) |
| 11 | `am doctor`에 가드 설치 여부(`~/.config/agent-manager/home`, 레포 훅) 확인 추가 | codex | 예정 |
