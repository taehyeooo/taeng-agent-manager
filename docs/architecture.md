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
  - escape(bash `\`, PowerShell `` ` ``, cmd `^`)는 미리 풀지 않고 "효력 없는 글자"로 남긴 채 나눈다 — `echo \"; git push`의 `\"`가 따옴표를 열어 뒤 명령을 숨기지 못한다. cmd에는 작은따옴표가 없다는 것도 반영한다.
  - `bash -c`·`powershell -Command`·`cmd /c`·`env`·`&`·`sudo`·`npx`/`pnpm dlx`/`npm exec`, 그리고 `timeout`·`nice`·`nohup`·`stdbuf`·`setsid`·`ionice`·`chronic`·`exec`·`command`·`watch`(→ `sh -c`)를 벗겨 안쪽 명령을 그대로 분석한다. `xargs`와 `find -exec/-execdir/-ok`의 안쪽 명령도 분석하되, 미리 모르는 인수(파일 이름)로 실행되므로 읽기 명령만 자동 허용한다. `find -delete`는 삭제 승인.
  - **기본 ask**: 아는 명령(읽기·문서 도구, 작업 폴더 안 파일 쓰기 도구, git·gh, orca)과 허용 목록 실행(아래)만 자동 허용한다. 그 밖의 프로그램·`./x.sh`·`node x.js`·`python -m <unittest·am 밖>`·`pytest`·`uv run`/`pipx run`/`poetry run` 같은 실행기·표준 입력으로 받는 `sh`는 위험 단어가 없어도 사람 확인이다. 승인 대상 명령(배포·설치 등)은 승인이 곧 확인이다. git은 모르는 하위 명령(외부 `git-<x>`), 안전 목록 밖 설정 키(`-c`·`git config`), `--exec-path`, `submodule foreach`·`bisect run`·`rebase -x`·`clone/init --template`도 사람 확인이고, 작업 폴더 안 훅 폴더(`core.hooksPath`, husky 등)의 훅은 commit·push 등에서 기준과 같아야 한다. gh는 pr·issue·repo·api·run·workflow·release·search·status·label 등만(확장·별칭·`auth` 설정은 사람 확인). 읽기 도구라도 프로그램을 실행·파일을 쓰는 옵션(`rg --pre`, `sort -o`, sed `e`/`w`, awk `system`/`>` 등)은 사람 확인. 인수·쓰기 대상에 변수(`$X`, `$'…'`)가 있으면(읽기 명령 제외) 사람 확인, `~`는 홈 폴더로 풀어 본다.
  - git 앞 옵션(`-C <폴더>`는 그 폴더 기준), git 별칭(실제 내용으로 펼침, `!셸` 별칭은 사람 확인), gh `-R/--repo`(이 프로젝트 저장소와 다르면 금지), python 옵션(`-u -m am approve`)을 해석한다.
  - 명령어 자리만 보므로 커밋 메시지·echo 인수 속 글자는 무시한다.
  - 변수·eval·인코딩된 명령·짝 없는 치환·스크립트 안 subprocess처럼 미리 알 수 없는 실행은 사람에게 넘긴다(Claude 확인 창, Codex 차단).
- **한계(남는 위험) — 정직하게**: 가드는 안전벨트지 샌드박스가 아니다.
  - 테스트·빌드는 좁은 명령 목록으로 허용한다: `gradlew[.bat]`의 `test/build/check/compileJava/compileTestJava/classes/testClasses/assemble` 및 제한된 옵션, 트리 안 경로의 `-p`; `npm ci`·`npm install`(인수 없음), package.json scripts에 있는 `npm test`·`npm run[-script] build/test/lint`, `npx vitest run`; 작업 트리 안 경로·점 이름만 받는 `python -m unittest`(작업 폴더에 `unittest.py`·`unittest/`가 있으면 `python -I -m unittest`만), 추적된 `.py`·`.sh`의 `python`·`sh` 실행, 이 매니저 레포 자신의 `python -m am`이다. 파일 실행은 기준(`refs/remotes/origin/main`)과 인덱스·실제 파일 내용이 모두 같아야 하고(CRLF/LF 차이는 허용, 경로 여러 개를 `ls-files -v`·`cat-file --batch` 두 번으로 묶어 본다), `python` 스크립트는 같은 폴더의 `.py`도 모두 기준과 같고 추적 안 된 `.py`가 없어야 한다(모듈 shadowing). assume-unchanged·skip-worktree·심볼릭 링크는 허용하지 않는다.
  - 기준은 **믿을 수 있을 때만** 쓴다: origin이 네트워크 주소(insteadOf 적용 후)이고, 그 ref의 마지막 reflog가 fetch·pull·clone·push가 남긴 것이며 현재 값과 같아야 한다. `git replace`는 무시하고 읽는다. 아니면 사람 확인. 같은 세션에서 기준을 바꿀 수 있는 명령(`update-ref`·`replace`·`symbolic-ref` 쓰기, `fetch`/`pull`의 `:` refspec·경로/URL 원격, `remote add/set-url/…`, `remote.*`·`url.*` 설정, `git -c remote.…/url.…`, `push .`·`refs/remotes/`로의 push)과 `.git/` 안 파일 쓰기도 사람 확인이다. 자동 fetch는 하지 않는다. 남는 한계: 사람이 승인한 위 명령(`update-ref -m "fetch …"`)이나 가드 밖 프로그램이 reflog까지 맞춰 위조하면 막지 못한다.
  - Gradle은 wrapper·build/settings.gradle(.kts)·gradle.properties·init.d·buildSrc·build-logic(트리 전체, 무시된 파일 포함, 무시된 폴더는 접어서 본다), npm/npx는 현재 폴더와 그 위 폴더들의 package.json·package-lock.json·npm-shrinkwrap.json·.npmrc(무시된 파일 포함, workspaces가 있으면 트리 전체)의 추가·삭제·수정을 같은 기준으로 검사한다. 기준 ref·reflog는 `.git` 파일에서 직접 읽고(reftable 저장소면 기준 없음 → 사람 확인), 한 판정 안의 git 조회는 캐시한다 — `npm test` 판정은 git 프로세스 3개, `npm -v` 같은 읽기 명령은 0개. 브랜치가 이 파일들을 정당하게 바꿔도(의존성 추가) 기준에 들어가기 전까지는 사람 확인이다.
  - 환경: 명령 앞 `X=1`, `env X=1`, 그리고 **같은 명령줄에서 환경을 바꾸는 것**(`export`·`declare`·`typeset`·`readonly`·`local`·`unset`·`setx`·`shopt`·`alias`, `set -a`/`allexport`/cmd `set X=1`, `.`·`source`, PowerShell `$env:`·`Set-Item env:`)이 있으면 자동 허용하지 않는다(위험 변수 목록이 아니라 "바꾸면 사람 확인"). 기준과 같은 package.json 스크립트 본문의 접두(`NODE_ENV=test jest`)만 예외. `export`·`declare -x`처럼 값을 출력만 하는 형태는 인증정보 노출로 금지.
  - npm: 앞 옵션(`--prefix`·`-w` 등)과 별칭(`t`, `rum`, `urn`, `x`, `i`…)을 풀어 실행 이름을 정하고, deploy/release/publish/push면 배포 승인을 요구한다. 스크립트와 pre/post(`ci`·`install`이면 install·prepare 계열) 본문의 승인 대상 명령도 승인을 요구한다. 읽기만 하는 `ls`·`view`·`audit`(fix 제외)·`-v` 등은 허용, `npm install <패키지>`·옵션 붙은 실행·`pnpm`·`yarn`·`bun` 실행·`npx`/`pnpx`/`bunx`/`npm x`/`pnpm dlx`/`yarn dlx`/`bun x`는 사람 확인.
  - heredoc: 머리줄을 일반 명령처럼 `; && | &`로 나눠 하나씩 판정한다. 본문을 프로그램으로 검사해 자동 허용하는 건 `python`·`py -3`·`node`에 인수가 없거나 `-`만 있는 단일 명령뿐이고(버전·`pythonw` 같은 이름 포함), `cat`·`tee`·`git commit -F -`·`gh … --body-file -`의 본문은 문서로 본다. 그 밖의 명령이 heredoc을 받으면 사람 확인이고 본문은 셸 줄로도 검사한다. 종료 태그는 정확히 일치해야 하며, `<<-`·`<<<`·인용 안 한 본문의 `$(…)`는 사람 확인이다. Python AST 경로 검사와 불명확한 쓰기의 상태 파일 문자열 안전망을 함께 적용한다. 동적 import·exec/eval·알 수 없는 함수·제어 흐름은 자동 허용하지 않는다.
  - 폴더 이동은 `if`·`!`·`time`·`command`·`builtin` 뒤와 괄호·중괄호 안의 `cd`·`pushd`·`chdir`·`Set-Location`까지 보고, 작업 트리 안 한 단계 이동만 따라간다(그 밖은 사람 확인).
  - 이 검사는 코드 실행의 안전성을 증명하지 않는다. 기준 브랜치 자체의 악성 코드, 테스트 러너가 읽는 수정된 테스트·cwd의 모듈 shadowing(`python -m unittest`), build 설정에서 불러오는 외부 파일·플러그인, 이미 상속된 환경 변수·사용자 홈 설정·설치된 의존성·npm lifecycle의 간접 실행, 검사 후 실행 전 변경은 완전하게 검사하지 못한다. 허용된 테스트·빌드 역시 임의 코드를 실행할 수 있으므로 격리와 교차 검증이 필요하다.
  - Git 읽기 명령은 하위 명령별 옵션 허용 목록을 적용한다. `--upload-pack`·`--exec`·`--ext-diff`·`--textconv`와 목록 밖 옵션은 사람 확인이다. `diff/log/show/format-patch/archive --output`, `format-patch -o`, `config --file/-f`, `bundle create`의 출력 위치와 `sort -o/--output`(결합 `-noFILE` 포함), `uniq`의 두 번째 이후 피연산자, `tree -o`를 파일 쓰기로 검사한다. 상태 파일은 금지, `.git/hooks` 등 Git 내부 파일은 사람 확인이다. 출력 위치가 불명확하거나 xargs/find가 이 도구들에 동적 인수를 공급하면 자동 허용하지 않는다. Git 설정의 명확한 읽기 모드(`--get`·`--list`)는 출력 파일 쓰기로 보지 않는다.
  - `cmd /c`의 `%NAME%`·`!NAME!`(부분 문자열·치환 포함)에서 비밀 변수 이름을 찾으면 금지하고, 해석하지 못한 확장은 사람 확인이다. PowerShell `$env:NAME` 출력 및 `Get-ChildItem env:`·별칭·와일드카드 환경 조회도 검사한다. 실제 환경 변수 값은 읽지 않는다. 비밀 이름 판별은 이름 패턴에 의존하므로 평범한 이름에 저장된 비밀값과 동적으로 구성한 이름을 모두 식별하지는 못한다.
  - Python 스크립트 자체는 기준과 같지만 형제 `.py`가 다르면, 차단 안내에 원인이 된 파일 이름과 모듈 가림 위험을 표시한다. 형제 파일 검사는 유지하며, 기준에 반영되기 전에는 사람 확인이 필요하다(Claude 확인 창, Codex 차단 후 사용자 직접 실행).
  - 실행 전에 알 수 없는 것: 허용 목록으로 실행되는 테스트 코드 자체(`python -m unittest`가 읽는 테스트 파일, `npm test`가 부르는 vitest 설정·파일 — 기준과 같은 package.json 스크립트 본문은 믿는다), PATH·셸 함수 바꿔치기, 이미 상속된 환경, `.git/hooks` 안 기존 훅, 읽기 명령으로 둔 도구의 드문 실행 옵션(목록에 없는 것). Git 변경 명령 전체의 옵션은 허용 목록 방식으로 전환하지 않았으며 알려진 실행·출력 옵션을 차단한다. 셸·도구 버전별 문법 전체를 구현한 샌드박스는 아니다.
  - CI의 append-only 검사는 **기존 승인이 바뀌거나 지워지는 것**만 잡는다. 새 승인이 정말 사용자에게서 왔는지는 증명하지 못한다 — 그건 가드(`am approve`는 사람 확인)에 기댄다.
  - 비밀값 유출·외부 저장소 변경을 CI가 막지는 못한다(가드가 실행 전에 막는 것이 전부).
  - 그래서 서버가 강제하는 마지막 겹이 필요하다: GitHub main 보호 규칙(PR·CI 통과 없이는 main에 못 들어감). 저장소 설정 변경이라 **사용자 승인 후 적용 — 아직 적용 안 됨**.
- **두 번째 그물(tripwire)**: 해석기가 명령을 볼 때마다 "봤다" 표시(`git:push`, `gh:pr.merge`, `known`, `build`…)를 남긴다. 명령줄(코드로 실행되는 인용문 포함, 커밋 메시지 같은 일반 인용문 제외)에 위험 단어(push·merge·approve·배포 도구·environ·project.json·no-verify·DB 변경어)가 있는데 그 단어를 설명하는 표시가 없으면 판단 불가로 사람에게 넘긴다. 명령 이름만 적은 `cmd:<이름>` 표시는 설명으로 치지 않는다. 기본 ask 이후에는 겹으로 남은 안전망이다.
- 교차 검증 기록(codex): 1차 우회 13건(`VerifierFindingsTest`) → 2차 11건·과잉 차단 4건(`VerifierRound2Test`) → 3차 9건·과잉 차단 3건(`VerifierRound3Test`: 붙여 쓴 옵션, 셸 조건문·역슬래시, 실행기 옵션, gh URL·-R 붙여쓰기, git -CX·--git-dir·core.hooksPath, 2> 리다이렉트, 상태 파일 rm/mv, done 뒤 실패 검증, 기간 끝난 에이전트 재배정). 3차부터는 사례별 수정과 함께 두 번째 그물을 더했다. 4차(`BuildAllowlistTest.test_r4_*`)에서 사례별 차단을 멈추고 "모르는 것은 허용하지 않는다(기본 ask)"로 구조를 바꿨다. 각 재현이 테스트로 남아 있고, `scripts/check_guard_mutations.py`가 허용 조건 하나씩을 지워 테스트가 잡는지 본다.

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
