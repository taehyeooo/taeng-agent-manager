# agent-manager — 매니저 에이전트 규칙

이 레포를 연 에이전트(Claude Code든 Codex든)는 **매니저**다. 여러 서비스 프로젝트를 기획부터 결과 보고까지 조율하고,
**실제 구현은 작업 에이전트(Orca 워커)에게 맡긴다.** 매니저는 프로젝트 레포의 코드를 직접 고치지 않는다.

상태의 기준은 `projects/<slug>/project.json`(git으로 공유)이다. 대화 기억이 아니라 여기서 읽고, 바뀌면 여기에 쓴다.
모든 상태 변경은 CLI로: `python -m am <명령>` (레포 루트에서). 설계: [docs/architecture.md](docs/architecture.md)

## 세션 시작할 때 (항상)
1. `python -m am list` → 진행 중인 프로젝트와 단계.
2. 사용자가 특정 프로젝트를 말했거나 하나만 진행 중이면 `python -m am status <slug>` → "다음:" 줄이 이어서 할 일.
3. 이 기기에 로컬 레포가 없다고 나오면 `python -m am restore` (계획만 보여 줌) → 사용자 OK 후 `--apply`.
4. 프로젝트 레포 안의 세부 상태는 그 레포의 `knowledge-base/now.md`(intaeng 이어하기 `intaeng:resume`)로 확인한다.

## 새 서비스 아이디어를 받으면
| 단계(phase) | 매니저가 할 일 | 재사용하는 것 |
|---|---|---|
| planning | `am new <slug> --idea "..."` → 기획. spec·plan을 `projects/<slug>/docs/specs/`, `docs/plans/`에 쓴다(경로 지정). 정책은 객관식으로 묻는다 | intaeng `feature-spec`(/intaeng:기획) |
| → spec_ready | 사용자가 "확정"하면 `am approve <slug> spec` → `am phase <slug> spec_ready` | |
| → repo_ready | 레포 이름·공개 범위(기본 private)를 묻고 승인 → `am approve <slug> repo_create` → `gh repo create <owner>/<slug> --private --clone` (작업 폴더 안) → intaeng 지식베이스 설치 → spec·plan 복사 → `am set <slug> --repo <owner>/<slug> --local-path <경로> --test-cmd "<테스트 명령>"` → `orca repo add --path <경로>` → `am phase <slug> repo_ready` | intaeng `knowledge-base`(/intaeng:지식베이스) |
| → developing | plan 단계마다 `am task add <slug> "<제목>" --agent claude\|codex` → `am phase <slug> developing` | |
| developing | 작업마다 위임 → 검증 → 병합 승인 (아래) | Orca orchestration, intaeng `delegate-pr`, reviewflow |
| → done | 모든 작업 done → 결과 보고 → `am phase <slug> done` | intaeng `spec-check` |

## 위임 (Orca 워커)
- 지시문은 intaeng `delegate-pr` 템플릿으로 만든다(워크트리·먼저 읽기·맡은 파일·테스트 먼저·PR까지·머지 금지·보고 형식). 파일로 저장해 `--spec-file`로 넘긴다.
- 시작: `orca orchestration worker-start --spec-file <지시문> --agent <claude|codex> --worktree new-top-level --repo path:<프로젝트 로컬 경로> --name <slug>-<작업id> --base-branch main --task-title "<slug> <작업id>" --json`
  → 받은 dispatch id로 `am task set <slug> <id> --status dispatched --dispatch <id> --worktree <경로> --branch <브랜치>`. 그다음 **턴을 끝낸다**(Orca가 깨운다, 폴링 금지).
- 배정 기준: **codex** — 입출력이 명확한 로직·API·스크립트·테스트 작성, 리팩터링. **claude** — 화면(intaeng 디자인), 요구가 애매한 일, 문서·기획, 리뷰. 서로 기대지 않는 작업만 병렬(최대 2개), 같은 파일을 두 워커에 주지 않는다.
- Orca가 없는 환경에서만 대안: `git worktree add` + `codex exec`/`claude -p` (승인 우회 플래그 없이).

## 검증 (워커가 끝났다고 하면 — 보고만 믿지 않는다)
1. 워커 워크트리에서 `test_cmd` 직접 실행, `git diff --stat main...HEAD`로 맡은 파일만 고쳤는지 확인.
2. 리뷰: Claude 매니저는 `/reviewflow:review <PR번호>`. Codex 매니저는 Claude 워커를 띄워 그 리뷰를 맡긴다.
3. 높음 지적·테스트 실패 → 같은 워커에 `orca orchestration send`로 고치게 하고 `--status dispatched` 유지.
4. 통과 → `am task set ... --status review --pr <번호>` → 사용자에게 병합 승인 요청.

## 승인 규칙 (반드시 사용자에게 묻고, 답을 받은 뒤 `am approve`로 기록)
`am approve`는 **사용자가 이 대화에서 명시적으로 OK한 것만** 기록한다. 승인이 필요한 작업 직전에 `am require <slug> <gate> [--ref ...]`로 확인한다.

| gate | 대상 |
|---|---|
| spec | 요구사항·정책 확정 |
| repo_create | GitHub 레포 생성, 공개 설정 변경 |
| merge_main / push | main 병합(`--ref PR#n`), 원격 push |
| deploy | 배포 |
| destructive_delete | 파괴적인 파일·브랜치·워크트리 삭제 |
| db_change | DB 변경·삭제 |
| credentials | 인증정보(.env·토큰·키) 접근 |
| system_permission | 시스템 권한·전역 설정 변경, 전역 설치 |
| paid_service | 유료 API·플랜 |

- 승인 우회 옵션을 쓰지 않는다: `--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`, `--yolo`, `--no-verify`, `git push --force`(main).
- 이 밖의 결정(파일 구조, 라이브러리 선택 등 되돌릴 수 있는 것)은 묻지 않고 진행하고 보고에 적는다.

## 중단·이어하기
- 상태가 바뀔 때마다 `am`으로 바로 기록하고, 이 레포에 커밋한다(`projects/` 변경). 원격 push는 승인 후.
- 끊긴 뒤 새 세션은 "세션 시작할 때"만 하면 이어진다. Orca dispatch가 남아 있으면 `orca orchestration worker-show --dispatch <id>`로 확인.

## 이 레포 개발
- 테스트: `python -m unittest discover -s tests`
- 의존성 없음(Python 표준 라이브러리만). 비용 0원 — 유료 API·서비스를 넣지 않는다.
