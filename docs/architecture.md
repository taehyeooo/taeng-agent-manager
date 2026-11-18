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
          승인 필요 명령? ──아니오──▶ opaque 정책 검사 → 허용 또는 확인
              │예
              ▼
          cwd → git common dir → 프로젝트 → project.json에 gate(+ref) 승인? ──예──▶ opaque 정책 검사 + 기록
              │아니오
              ▼
          차단 + 승인 요청(명령·영향 범위·위험) 기록 → 매니저가 사용자에게 보여 줌
```
- 아래의 확인 설명 중 opaque(해석 불가) 판정은 최종적으로 “기본 허용 + 기록” 정책과 설정을 따른다. 금지·승인 필요·승인 기록 명령의 처리는 유지한다.
- 명령은 `&& ; | 줄바꿈` 단위로 나눠 하나씩 본다. `rm -rf node_modules` 같은 빌드 산출물·임시 폴더 삭제는 자동 진행.
- ref 단위 승인: `push`는 브랜치, `merge_main`은 `PR#n`, ref 없는 승인은 그 gate 전체.
- 훅은 `python3`의 Python 3.10 이상 실행 성공을 표준입력 없이 확인하고 실패하면 `python`을 쓴다. 위치 파일은 있지만 둘 다 실행되지 않으면 종료 코드 2로 차단한다. `am setup --install`은 오래된 훅을 제자리에서 갱신하며, `am doctor`는 격리된 위치 파일과 무해한 가드로 훅 실행을 점검한다.
- 원격 업로드(scp·rsync·sftp), SSH의 서버 변경·대화형 접속·불명확한 실행, 셸을 통한 `deploy.sh` 실행은 각각 서버·스크립트별 배포 승인 대상이다(`--dry-run`·`--checks-only` 단독은 사람 확인). 로컬 접속 프로그램·설정을 바꾸는 옵션은 배포 승인과 별도로 사람 확인이 필요하다. SSH 읽기 명령과 다운로드는 기본 사람 확인을 유지한다.
  - 배포 스크립트 승인 대상은 `repo-script:<레포 기준 상대 경로>`(예: `repo-script:scripts/deploy.sh`) — 서버 별칭(`deploy.sh`라는 SSH 서버)과 겹치지 않는다. Claude Code 플러그인에 들어 있는 스크립트(`~/.claude/plugins/cache/<마켓>/<플러그인>/<버전>/…`, `marketplaces/<마켓>/plugins/<플러그인>/…`)는 버전을 뺀 `plugin-script:<마켓>/<플러그인>/<경로>`(예: devflow의 `plugin-script:taeng-marketplace/devflow/scripts/deploy.sh`). 두 이름공간은 최상위에서 나뉘고, 경로에 콜론(`:`)이 있으면 승인 대상을 만들지 않는다 — 레포 안 `./plugin-script:…/deploy.sh` 같은 폴더 이름으로 다른 승인을 흉내 낼 수 없다. 그 밖의 레포 밖이나 PATH에서 찾는 `deploy.sh`, 콜론이 든 경로는 사람 확인 + 대상 없는(전체) 배포 승인이 필요하다.
  - **접속 프로그램 옵션은 허용 목록**(`TRANSPORT_SAFE`): ssh `-q -v -t -T -C -4 -6 -n -x -i -p -l -o`, scp·sftp는 여기에 `-r -p -P -c`(scp `-B -O -T`), rsync는 `-a -v -z -r …`·`--delete`·`--exclude`·`--progress` 같은 전송 옵션과 `-e "ssh <허용 옵션>"`(서버·명령 같은 피연산자나 `--`가 들어 있으면 rsync가 붙이는 승인된 서버 대신 그 서버·명령이 쓰이므로 사람 확인). `-o`는 BatchMode·StrictHostKeyChecking·ConnectTimeout·ServerAlive*·Port·User·IdentityFile 등 접속 설정만, `UserKnownHostsFile`은 `/dev/null`일 때만(Windows에서는 `NUL`도 — 다른 OS에서 `nul`은 보통 파일). 목록 밖 옵션(`-I` PKCS#11 라이브러리, `-A`, `-S`, `-F`, `-P` 태그, `--rsync-path` …)은 배포 승인과 별도로 사람 확인. 로컬 파일에 쓰는 옵션(ssh `-E`, rsync `--log-file`·`--write-batch`·`--only-write-batch`, `-e "ssh -E …"` 포함)은 그 경로를 파일 쓰기 검사에 넘긴다 — 상태 파일이면 금지.
  - **셸 옵션도 허용 목록**(bash·sh·zsh·dash): `-c -e -u -x -v -n -f -E -l`, `-o pipefail|errexit|nounset|xtrace|verbose|noglob|noclobber|errtrace`, `--norc --noprofile --posix --login`. `-l`은 Codex가 모든 명령을 `bash -lc`로 감싸기 때문에 허용한다(사용자 기본 프로필, 명령에 적힌 파일이 아님). 목록 밖(`--rcfile`·`--init-file`·`-i`·`-s`·`-O` …)은 초기화 파일·다른 입력을 실행할 수 있어 사람 확인. `ENV=`·`BASH_ENV=`·`ZDOTDIR=` 같은 접두는 기존 환경 변수 접두 규칙으로 사람 확인.
  - 셸의 `-c`(`-xc`·`-ec`·`--norc -c`·`-o pipefail -c` 포함)는 첫 비옵션 인수(스크립트) **앞**에 있을 때만 명령 문자열 플래그로 본다. `bash deploy.sh -c true`의 `-c`는 스크립트 인수이고, `--` 뒤의 `-c`는 스크립트 이름이다. PowerShell·cmd도 같은 규칙(`pwsh x.ps1 -c ...` → 스크립트 실행).
  - `h:/opt/`처럼 한 글자 서버는 Windows가 아닐 때 서버로 본다(Windows에서는 드라이브 문자 — 로컬 경로).
  - SSH 원격 문자열의 curl은 묶음 옵션(`-sSLX POST`)과 서버에 파일을 쓰는 옵션(`-c`·`-D`·`--trace` 등)도 변경으로 보아 배포 승인 대상이다.
  - 알려진 보수적 판정(안전 쪽, 고치지 않음): 원격 문자열 안 작은따옴표 묶음과 백틱이 섞이면 따옴표 경계를 확정하지 않고 읽기 전용으로 인정하지 않는다 — 그 명령은 배포 승인 또는 사람 확인으로 간다.
- 입력이 비었거나 깨지거나 가드 자체가 오류 나면 종료 코드 2로 막는다(fail closed). 기기에 `am setup`이 안 돼 있으면 훅은 아무것도 하지 않는다 — `am doctor`·세션 시작 규칙으로 확인.
- CI(`.github/workflows/test.yml`): 테스트를 Ubuntu·Windows에서, 그리고 `project.json`의 기존 승인 기록이 바뀌거나 지워지면 실패(append-only, `scripts/check_approvals.py`).
- **가드는 명령을 해석한다**(정규식으로 문자열 전체를 훑지 않음):
  - 따옴표 밖에서만 `; | & { } ( )`·줄바꿈으로 나누고, `$(...)`·`` `...` `` 안의 명령도 따로 검사한다.
  - escape(bash `\`, PowerShell `` ` ``, cmd `^`)는 미리 풀지 않고 "효력 없는 글자"로 남긴 채 나눈다 — `echo \"; git push`의 `\"`가 따옴표를 열어 뒤 명령을 숨기지 못한다. cmd에는 작은따옴표가 없다는 것도 반영한다.
  - `bash -c`·`powershell -Command`·`cmd /c`·`env`·`&`·`sudo`·`npx`/`pnpm dlx`/`npm exec`, 그리고 `timeout`·`nice`·`nohup`·`stdbuf`·`setsid`·`ionice`·`chronic`·`exec`·`command`·`watch`(→ `sh -c`)를 벗겨 안쪽 명령을 그대로 분석한다. `xargs`와 `find -exec/-execdir/-ok`의 안쪽 명령도 분석하되, 미리 모르는 인수(파일 이름)로 실행되므로 읽기 명령만 자동 허용한다. `find -delete`는 삭제 승인.
  - **기본 허용 + 기록**(사용자 지시 2026-11-18): 해석을 끝내지 못한 명령(opaque)도 금지·미승인 작업·승인 기록 명령이 아니고, 해당 구간에 아래 위험 신호가 없으면 확인 창 없이 실행한다. 변수 값은 읽지 않고 보이는 글자만 검사한다. 알려진 안전 명령의 커밋 메시지·문서 인수까지 위험 명령으로 취급하지 않는다.
  - 위험 신호는 `OPAQUE_RISK_SIGNALS` 상수와 테스트로 고정한다: `git push`, `gh pr merge`, `gh repo`, `gh release`, `gh api` 쓰기 메서드·필드; `ssh/scp/rsync/sftp`; `deploy`; `sudo/systemctl/service`; `rm -r/-rf`, `find -delete`, `shred`; `kill/pkill`; `docker/kubectl/terraform/helm`; `psql/mysql/mongo/redis-cli/supabase`, `DROP/DELETE FROM/TRUNCATE/ALTER`; `curl/wget/http`의 POST·PUT·PATCH·DELETE·데이터 전송; `npm/pnpm/yarn publish`; `eas submit/update`; base64 디코드·eval·source·다운로드한 코드의 셸 실행; `.env`, `*.pem`, `id_rsa`, `credentials` 비밀값 파일. 위험 신호가 보이면 Claude 확인 창·Codex 차단을 유지한다.
  - `config/guard.json`의 `{"opaque":"allow"}`가 기본이며 파일이 없어도 같다. `{"opaque":"ask"}`면 예전처럼 모든 opaque 명령을 확인한다. 설정이 깨지거나 읽히지 않으면 확인 방식으로 돌아간다. Write/Edit 같은 파일 도구의 경로 판정은 바꾸지 않는다.
  - 자동 허용한 opaque 명령은 프로젝트 이력에 `확인 없이 실행: <사유>; 명령: <요약>`으로 남긴다. 프로젝트를 찾지 못하면 `~/.config/agent-manager/guard-allowed.log`에 한 줄로 남긴다. 명령은 비밀값처럼 보이는 토큰을 가린 뒤 120자로 자르고 줄바꿈을 없앤다. 기록 실패는 실행을 막지 않는다.
  - git 앞 옵션(`-C <폴더>`는 그 폴더 기준), git 별칭(실제 내용으로 펼침, `!셸` 별칭은 사람 확인), gh `-R/--repo`(이 프로젝트 저장소와 다르면 금지), python 옵션(`-u -m am approve`)을 해석한다.
  - 해석이 끝난 명령은 명령어 자리를 보므로 커밋 메시지·echo 인수 속 글자는 무시한다. opaque 구간의 위험 신호 검사는 인수의 글자도 본다.
  - 변수·인코딩된 명령·짝 없는 치환·스크립트 안 subprocess도 opaque 정책을 따른다. eval 등 위험 신호가 보이거나 `ask` 설정일 때 사람에게 넘긴다(Claude 확인 창, Codex 차단).
- **한계(남는 위험) — 정직하게**: 가드는 안전벨트지 샌드박스가 아니다. 아래의 “사람 확인”·“자동 허용하지 않음”은 파서가 opaque로 분류한다는 뜻이다. 최종 실행은 위의 2026-11-18 정책을 따른다. 위험 신호가 안 보이는 동적 코드·수정된 스크립트는 기본 설정에서 실행될 수 있다. 글자 검사로 숨겨진 실행을 증명할 수 없고, 로그 마스킹도 모든 비밀값을 알아내지는 못한다.
  - 테스트·빌드는 좁은 명령 목록으로 허용한다: `gradlew[.bat]`의 `test/build/check/compileJava/compileTestJava/classes/testClasses/assemble` 및 제한된 옵션, 트리 안 경로의 `-p`; `npm ci`·`npm install`(인수 없음), package.json scripts에 있는 `npm test`·`npm run[-script] build/test/lint`, `npx vitest run`; 작업 트리 안 경로·점 이름만 받는 `python -m unittest`(작업 폴더에 `unittest.py`·`unittest/`가 있으면 `python -I -m unittest`만), 추적된 `.py`·`.sh`의 `python`·`sh` 실행, 이 매니저 레포 자신의 `python -m am`이다. 파일 실행은 기준(`refs/remotes/origin/main`)과 인덱스·실제 파일 내용이 모두 같아야 하고(CRLF/LF 차이는 허용, 경로 여러 개를 `ls-files -v`·`cat-file --batch` 두 번으로 묶어 본다), `python` 스크립트는 같은 폴더의 `.py`도 모두 기준과 같고 추적 안 된 `.py`가 없어야 한다(모듈 shadowing). assume-unchanged·skip-worktree·심볼릭 링크는 허용하지 않는다.
  - 기준은 **믿을 수 있을 때만** 쓴다: origin이 네트워크 주소(insteadOf 적용 후)이고, 그 ref의 마지막 reflog가 fetch·pull·clone·push가 남긴 것이며 현재 값과 같아야 한다. `git replace`는 무시하고 읽는다. 아니면 사람 확인. 같은 세션에서 기준을 바꿀 수 있는 명령(`update-ref`·`replace`·`symbolic-ref` 쓰기, `fetch`/`pull`의 `:` refspec·경로/URL 원격, `remote add/set-url/…`, `remote.*`·`url.*` 설정, `git -c remote.…/url.…`, `push .`·`refs/remotes/`로의 push)과 `.git/` 안 파일 쓰기도 사람 확인이다. 자동 fetch는 하지 않는다. 남는 한계: 사람이 승인한 위 명령(`update-ref -m "fetch …"`)이나 가드 밖 프로그램이 reflog까지 맞춰 위조하면 막지 못한다.
  - Gradle은 wrapper·build/settings.gradle(.kts)·gradle.properties·init.d·buildSrc·build-logic(트리 전체, 무시된 파일 포함, 무시된 폴더는 접어서 본다), npm/npx는 현재 폴더와 그 위 폴더들의 package.json·package-lock.json·npm-shrinkwrap.json·.npmrc(무시된 파일 포함, workspaces가 있으면 트리 전체)의 추가·삭제·수정을 같은 기준으로 검사한다. 기준 ref·reflog는 `.git` 파일에서 직접 읽고(reftable 저장소면 기준 없음 → 사람 확인), 한 판정 안의 git 조회는 캐시한다 — `npm test` 판정은 git 프로세스 3개, `npm -v` 같은 읽기 명령은 0개. 브랜치가 이 파일들을 정당하게 바꿔도(의존성 추가) 기준에 들어가기 전까지는 사람 확인이다.
  - 환경: 명령 앞 `X=1`, `env X=1`, 그리고 **같은 명령줄에서 환경을 바꾸는 것**(`export`·`declare`·`typeset`·`readonly`·`local`·`unset`·`setx`·`shopt`·`alias`, `set -a`/`allexport`/cmd `set X=1`, `.`·`source`, PowerShell `$env:`·`Set-Item env:`)이 있으면 자동 허용하지 않는다(위험 변수 목록이 아니라 "바꾸면 사람 확인"). 기준과 같은 package.json 스크립트 본문의 접두(`NODE_ENV=test jest`)만 예외. `export`·`declare -x`처럼 값을 출력만 하는 형태는 인증정보 노출로 금지.
  - npm: 앞 옵션(`--prefix`·`-w` 등)과 별칭(`t`, `rum`, `urn`, `x`, `i`…)을 풀어 실행 이름을 정하고, deploy/release/publish/push면 배포 승인을 요구한다. 스크립트와 pre/post(`ci`·`install`이면 install·prepare 계열) 본문의 승인 대상 명령도 승인을 요구한다. 읽기만 하는 `ls`·`view`·`audit`(fix 제외)·`-v` 등은 허용, `npm install <패키지>`·옵션 붙은 실행·`pnpm`·`yarn`·`bun` 실행·`npx`/`pnpx`/`bunx`/`npm x`/`pnpm dlx`/`yarn dlx`/`bun x`는 사람 확인.
  - heredoc: 머리줄을 일반 명령처럼 `; && | &`로 나눠 하나씩 판정한다. 본문을 프로그램으로 검사해 자동 허용하는 건 `python`·`py -3`·`node`에 인수가 없거나 `-`만 있는 단일 명령뿐이다(버전·`pythonw` 같은 이름 포함). 단일 일반 명령(`cat`·`tee`·gh·Orca 등 기존 KNOWN 목록)의 본문은 데이터로 보고, 머리줄의 쓰기·승인·금지는 그대로 검사한다. Git은 `commit/notes/tag`의 `-F -`·`--file -`·`--file=-` 메시지 입력만 데이터로 본다. 따라서 커밋 메시지·위임 문서 속 배포 예시는 실행으로 오인하지 않는다. 셸·인터프리터·SSH·실행 래퍼·알 수 없는 명령이나 복합 머리줄은 기존 사람 확인과 본문 검사를 유지한다. 종료 태그는 정확히 일치해야 하며, `<<-`·`<<<`·인용 안 한 본문의 `$(…)`는 사람 확인이다. Python AST 경로 검사와 불명확한 쓰기의 상태 파일 문자열 안전망을 함께 적용한다. 동적 import·exec/eval·알 수 없는 함수·제어 흐름은 자동 허용하지 않는다.
  - 폴더 이동은 `if`·`!`·`time`·`command`·`builtin` 뒤와 괄호·중괄호 안의 `cd`·`pushd`·`chdir`·`Set-Location`까지 보고, 작업 트리 안의 존재하는 폴더로 가는 `cd`만 따라간다(그 밖은 사람 확인). Windows에서는 Git Bash의 `/c/...`도 실제 드라이브 경로로 검사한다. 네이티브 `/c` 폴더가 따로 존재해 해석이 모호하면 사람 확인이다. 심볼릭 링크·junction의 실제 경로도 작업 트리 안이어야 한다.
  - 읽기 조합은 별도 우회 허용 없이 각 조각을 기존 검사로 판정한다. `cat/ls/grep/head/tail/wc/sort/uniq/find`와 Git 읽기를 `;`, `&&`, `||`, 파이프로 연결하거나 `2>/dev/null`로 오류 출력을 버려도 모든 조각이 통과하면 자동 허용이다. 읽기 도구의 파일 출력(`cat > x`, `sort -o`, `uniq INPUT OUTPUT`)과 조합 안 `find -exec/-execdir/-ok/-okdir/-delete/-fprint/-fprint0/-fprintf/-fls`는 사람 확인 이상이다. 상태 파일·인증정보·승인 대상 명령은 기존 금지·승인 판정을 유지한다. 문서용 heredoc 쓰기와 단독 `find -exec`의 기존 읽기 전용 규칙은 유지한다.
  - `git apply/am`의 heredoc과 패치 파일은 같은 `_patch_paths` 검사로 생성·수정·삭제·이름 변경의 양쪽 경로를 검사한다. `-p`·`--directory`·`git -C`를 반영한 실제 위치를 원래 작업 폴더 기준으로 검사하므로 상태 파일과 다른 프로젝트 수정은 계속 금지하며, 경로 이스케이프·미지원 옵션·읽을 수 없는 입력은 사람 확인이다. `git am`은 전체 메일 묶음에서 7bit/8bit 이외의 Content-Transfer-Encoding, multipart 또는 연속된 base64 모양 본문을 발견하면 사람 확인으로 돌린다. heredoc과 파일 입력에 같은 검사를 적용하며, 전송 인코딩을 해석하지 않고 경로를 신뢰하는 우회를 막기 위해 긴 영숫자 본문도 보수적으로 확인할 수 있다.
  - 작업 트리 밖으로 `cd`해도 존재하는 절대·홈 경로이고 이후가 읽기 목록·제한된 Git 조회·`orca orchestration check/worker-show`뿐이면 허용한다. 이동한 폴더에서 각 명령의 옵션·출력·인증정보를 다시 검사하므로 쓰기·업로드·배포·삭제가 섞이면 예외가 적용되지 않는다. 상대 경로로 트리 밖 이동·변수·명령 치환·없는 경로는 계속 사람 확인이다.
  - 추적된 검사 스크립트는 기존 원격 기준·인덱스·파일·Python 형제 모듈 검사를 유지하며 `bash/zsh/dash`의 `.sh` 실행과 명시적 경로(`./scripts/check.py` 등) 실행도 같은 기준을 적용한다. 새 파일·로컬 커밋만 된 파일·변경 파일·추적되지 않은 형제 모듈은 계속 사람 확인이며 T6의 `deploy.sh` 판정이 먼저 적용된다.
  - 설치된 `taeng-marketplace/reviewflow/scripts/review.sh collect [대상]`는 검토한 0.1.0 파일의 SHA-256과 일치할 때만 허용한다. 플러그인 설치 경로·내부 심볼릭 링크·단일 숫자/브랜치 인수를 검사하며, 내용 변경·다른 작업(`record/comment`)·추가 인수는 사람 확인이다. 이 버전은 origin에서 자료를 가져오고 로컬 보고서/사용 로그를 쓰므로 완전한 읽기 전용은 아니며, 버전 갱신은 다시 검토해야 한다.
  - `.am`의 실제 설정은 `am/state.py`가 읽는 `.am/local.json`, 승인·상태는 `projects/<slug>/project.json`이다. 현재 작업 트리의 `.am/specs/` 하위 지시문·재현 파일만 직접 쓰기를 허용하고, 디렉터리 자체·상태 파일 이름·중첩 `.am/.git`·상위 경로 탈출·설정 폴더를 바꾸는 심볼릭 링크는 보호한다. macOS의 `/private/tmp/claude-*/…/scratchpad/`도 실제 경로 기준 임시 문서 쓰기를 허용하되 다른 프로젝트와 상태 파일 보호는 먼저 적용한다.
  - Gradle 출력·테스트 옵션은 `--console=plain|rich|auto`, `--info`, `--quiet/-q`, `--stacktrace`, `--warning-mode=값`, `--tests 패턴`, `--rerun/--rerun-tasks`, `--no-daemon`, `--offline`을 허용한다. `--tests`는 비어 있거나 옵션으로 시작하는 값을 받지 않는다. `cd backend; ./gradlew.bat test --console=plain`과 `npm test -- src/unit.test.ts -t "테스트 이름"`도 빌드 입력·스크립트가 기준과 같아야 허용한다. 출력 옵션으로 입력 파일 검사를 생략하지 않는다.
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
