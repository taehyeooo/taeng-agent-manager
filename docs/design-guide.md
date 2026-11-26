# 디자인 가이드 — 디자인 요청이 들어오면 먼저 읽는 문서

저장 위치: 이 레포(taeng-agent-manager)의 `docs/design-guide.md` · 만든 날: 2026-11-20 · 사용자 지시로 만들고 관리한다.

UI·UX·화면 구성·사용자 흐름·인터랙션·애니메이션·모션 그래픽에 관한 요청(예: "디자인 할 건데 디자인 문서 보고 해줘")을 받으면 매니저는 **작업을 계획하기 전에 이 문서를 처음부터 읽는다.** 디자인 규칙은 이 문서 한 곳에서만 관리한다. 디자인 도구 설명·설치 상태도 이 문서에서 관리하며, 영상 도구는 [마케팅 가이드](marketing-guide.md#영상글쓰기마케팅-도구)에 둔다. AGENTS.md·CLAUDE.md에는 이 문서를 가리키는 한 줄만 둔다.

홍보물(릴스·게시물)은 [마케팅 가이드](marketing-guide.md)를 따른다.

## 1. 진행 순서

| 단계 | 하는 일 | 누가 | 남길 것 |
|---|---|---|---|
| 1. 사용자 흐름 확인 | 대상 사용자, 목표, 핵심 작업, 사용 환경. 사용자 목표 → 진입 화면 → 주요 행동 → 결과 확인 흐름과 막히는 지점. 로딩·오류·빈 화면·완료 상태와 복구 경로(아래 'UX 검토 체크리스트') | 매니저(필요하면 조사 작업자) | 흐름 한 장, 확인 못 한 것은 '가정' 표시 |
| 2. 도구 고르기 | 아래 빠른 선택표와 도구 표에서 프로젝트 스택에 맞는 것만. **서비스 시작 때 이 서비스에 쓸 스킬 3~5개만 고른다(전부 받지 않는다).** 전체 방향은 하나를 중심으로, 나머지는 보완. 그 기기의 설치 상태 확인 | 매니저 | 고른 도구·이유·적용 범위 |
| 2-1. 설치 전 보안 검토 | 새 설치·업데이트가 필요하면 [공통 검토 절차](tool-security-review.md)로 같은 버전을 두 모델이 독립 검토. 등록·링크 참고만이면 검토 불필요 | Codex·Claude Code | 검토 기록·남은 검토자 |
| 2-2. 설치 → 사용 | 설치 요청·검토 완료·기존 승인 정책 충족 후 검토한 버전만 설치하고 사용. 기존 설치분은 확인된 범위에서 사용 | 작업자 | 기기·에이전트별 설치 상태·버전·검토 기록 갱신 |
| 시안 전 · 레퍼런스 선택 | 사용자가 레퍼런스를 원하면 아래 ‘디자인 레퍼런스 사이트’에서 사례 2~4개를 골라 직접 링크와 추천 이유를 보여 주고 사용자가 고르게 한다. | 매니저 → 사용자 | 선택한 사례와 참고할 부분 |
| 3. 시안 3개 | 서로 다른 방향 3개. 각 시안에 콘셉트 한 줄, 밝은·어두운 모드의 전/후 목업, 움직임, 바뀌는 디자인 값, 난이도·위험, 추천 순위를 담는다.<br>**모바일 앱**은 **실제 휴대폰 화면 전체로**: 기기 프레임·상태 바·헤더·탭 바까지, 지금 앱 화면과 코드를 바탕으로 바뀌는 부분만 바꾸고, 카드나 일부만 잘라 그리지 않는다(사용자 피드백 2026-11-20).<br>**웹 서비스**는 로그인 전 소개 페이지(랜딩)가 필요한지 사용자에게 확인한다. **PC 브라우저 화면(너비 1440px·1280px)을 먼저** 그리고, 휴대폰(너비 390px)에서의 반응형 화면도 함께 보여 준다(사용자 결정 2026-11-25).<br>흐름마다 지금 화면과 바뀐 화면을 나란히 놓고, 흐름 순서대로 화면 1~3장을 보여 준다. 사용자가 원하면 Claude와 Codex가 서로 결과를 보지 않고 각각 만든다 | 작업자 | 목업 파일·설명, 비교 페이지(매니저) |
| 4. 사용자 선택 | 시안 고르기는 **항상 사용자 몫**. 매니저는 한 화면에서 비교할 수 있게 보여 주고 추천만 한다. 섞어도 된다 | 사용자 | 고른 것과 사용자 말(그대로) |
| 5. 세부 | 고른 방향을 나머지 화면·상태(로딩·오류·빈 화면·완료)·다크·큰 글자·동작 줄이기까지 구체화. 필요하면 다시 고르게 한다 | 작업자 → 사용자 확인 | 세부 시안 |
| 6. 적용 | 실제 코드에 반영. 테스트 먼저, 기존 디자인 규칙(토큰) 안에서. 웹페이지·앱 디자인 QA는 [읽기·다음 행동 찾기](readability-qa.md)를 먼저 점검하고, 이어서 [45개 UI 패턴](ui-patterns-45.md) 중 기존 기능에 어울리는 것만 적용한다. | 작업자 | PR, 전/후 캡처 |
| 7. 검수 | 반대쪽 모델이 교차 검수(시각·접근성·흐름·회귀). 모바일·글자·키보드·포커스·주요 버튼·오류 복구, 동작 줄이기 | 검증 작업자 | 지적·수정 기록 |
| 8. 보고 | 아래 4장 형식 | 매니저 | 사용자에게 보고 |

단계를 건너뛰지 않는다. 사용자가 범위를 줄이라고 하면(예: "시안 없이 바로 고쳐") 그 지시를 따르고 건너뛴 단계를 보고에 적는다.

### 디자인 레퍼런스 사이트

사용자가 필요한 화면이나 흐름에 맞춰 개별 사례 링크를 추천할 때 쓰는 표다. 무료 공개 범위에서 찾고, 유료 가입·결제는 사용자 승인(`paid_service`) 없이 진행하지 않는다.

| 사이트 | 무엇을 할 때 | 특징 |
|---|---|---|
| [Mobbin](https://mobbin.com) | Trova 앱 화면을 개편하거나 온보딩·공유·지도 등 새 기능의 화면 흐름을 설계할 때 | 실제 서비스 중인 모바일 앱과 웹의 화면을 기능별·화면별로 찾고 앞뒤 사용자 흐름을 함께 볼 수 있다. 일부 유료. |
| [Refero](https://refero.design) | 로그인·결제·온보딩 화면이나 입력 폼 같은 특정 UI 요소를 빠르게 찾을 때 | 웹·iOS 사례에서 같은 UI 패턴을 찾아 화면 배치와 조작 방식을 비교하기 좋다. |
| [Land-book](https://land-book.com) | Trova 소개 사이트(trovapp.github.io)나 공유 링크로 여는 웹 화면의 레이아웃과 색 조합을 정할 때 | 랜딩 페이지·웹사이트를 섹션, 스타일, 산업별로 탐색할 수 있다. |
| [Godly](https://godly.design) | 최근 웹디자인 흐름을 살펴보거나 홍보용 웹페이지의 스크롤·모션·타이포그래피 연출을 구상할 때 | 실험적인 웹사이트에서 스크롤에 따라 글자와 화면이 어떻게 달라지는지 살펴보기 좋다. |
| [oh-my-design](https://oh-my-design.kr/design-systems) | 글꼴(특히 한글 타이포)·색·컴포넌트 기준을 정하거나, 국내 서비스(여기어때·토스·당근 등)가 화면 규칙을 어떻게 잡는지 볼 때 | 실제 서비스 622곳의 DESIGN.md를 지역·업종별로 찾을 수 있고, 에이전트에게 바로 읽힐 수 있다. 한글 화면용 스킬(/hangul)도 있다. 다른 회사의 색·로고·고유 글꼴을 그대로 가져오지 않고, 기준과 구성 방식만 참고한다. 글꼴은 라이선스를 확인하고 쓴다. |

## 2. 매니저가 지킬 것

- **다른 레포 작업도 이 문서로**: 매니저 레포에서 다른 서비스 레포(예: Trova 앱·백엔드)의 디자인을 맡길 때, 작업자는 그 레포(다른 기기일 수도 있음)에서 일하므로 이 문서를 직접 못 읽을 수 있다. **매니저가 이 문서를 읽고**, 고른 도구·선택 이유·적용 범위·진행 단계·체크리스트 중 필요한 항목을 **지시문에 옮겨 적어 전달**한다. 서비스 레포마다 이 문서를 복사해 두지 않는다.
- **도구 설치**: 도구 고르기 → [설치 전 보안 검토](tool-security-review.md) → 설치 → 사용 순서로 진행한다. 설치 요청이 있어도 검토가 끝나기 전에는 설치하지 않는다. 설치 자체는 기존 승인 정책(`system_permission`, 유료면 `paid_service`, 사용자 `auto_gates` 설정 포함)을 따른다. 설치 후 이 문서 도구 표의 기기·에이전트별 상태·버전·확인일·검토 링크를 고친다.
- 기존 디자인 시스템, 이미 합의한 흐름, 사용자 지시가 우선이다. 도구 때문에 전체 화면이나 서비스 정책을 임의로 바꾸지 않는다. 도구 목록 전체를 일괄 설치하지 않는다.
- 서비스별 디자인 결정(색의 의미, 사용자가 싫다고 한 표현 등)은 그 서비스 레포의 디자인 문서에 남기고, 지시문에 옮겨 적는다.
- 쓰지 않은 도구를 썼다고, 읽지 않은 문서를 읽었다고 보고하지 않는다. 이 문서를 찾지 못하면 그렇다고 알린다.

## 3. 작업자 지시문에 넣을 것

- 진행 단계 중 이번 작업이 맡은 단계와 결과물(파일 위치·형식)
- 모바일 앱은 기기 프레임·상태 바·헤더·탭 바를 포함한 휴대폰 전체 화면을 요청한다. 웹 서비스는 PC 브라우저(1440px·1280px) 우선 시안과 휴대폰(390px) 반응형 시안을 요청하고, 로그인 전 소개 페이지가 필요한지 사용자에게 확인한 결과를 적는다.
- 고른 도구와 이유, 그 기기에서 실제로 쓸 수 있는지 확인하라는 말, **쓴 스킬과 그 스킬이 바꾼 결정을 보고**하라는 말
- 서비스별 디자인 결정과 금지 사항, UX 검토 체크리스트 중 필요한 항목. 웹페이지·앱 디자인 QA를 맡길 때는 6단계의 두 요청문을 순서대로 옮겨 적는다.
- 사용자 선택 전에는 실제 앱 코드를 바꾸지 않는다는 것(3단계), 데이터 바꾸는 동작 금지, 비용 드는 폰트·에셋·서비스 금지

## 4. 보고 형식

선택한 도구와 이유, 적용 범위, 확인한 것과 남은 가정. **설계 제안 / 구현 확인 / 실제 사용자 테스트 결과를 구분**한다. 사용자 테스트를 하지 않았으면 '검증 필요'로 남긴다. 아래 '사용 결과 기록 예시'를 따른다.

웹페이지·앱 디자인 QA 결과는 ‘화면 / 무엇이 불편했나 / 어떻게 바꿨나 / 실행 확인·코드 판단’ 표에 기록한다. 적용한 패턴은 ‘어떻게 바꿨나’에 적는다.

## 5. 기기별 설치 상태와 도구 설명

설치 상태는 아래 [디자인 도구 표](#디자인-도구)에 통합한다. 영상·글쓰기·마케팅 도구는 [해당 표](marketing-guide.md#영상글쓰기마케팅-도구)에서 확인한다. 설치·삭제·업데이트할 때 확인 날짜와 검토 기록도 함께 갱신한다.

UI는 색상·글자·간격·배치, UX는 목표 달성 흐름·안내·오류 복구·결과 확인을 다룬다. 도구가 사용자 조사나 사용성 검증을 대신 수행한다고 가정하지 않는다. 지원 환경·현재 기능·요금제는 사용 시 원본에서 확인한다.

## 에이전트 매니저 사용 지침

1. 먼저 대상 사용자, 사용 목적, 핵심 작업과 사용 환경을 확인한다. 확인되지 않은 내용은 가정으로 표시한다.
2. 사용자 목표 → 진입 화면 → 주요 행동 → 결과 확인의 흐름을 정한다. 시각적 스타일을 정하기 전에 불필요한 단계와 막히는 지점을 검토한다.
3. 메뉴 구조, 정보 우선순위, 입력 항목, 버튼 문구를 정하고 로딩, 오류, 빈 화면, 완료 상태와 복구 경로를 함께 설계한다.
4. 프로젝트의 기술 스택, 기존 디자인 규칙, 사용자 요구사항에 맞춰 필요한 도구만 선택한다. 전체 디자인 방향은 하나의 스킬을 중심으로 잡고 다른 도구는 구체적인 보완 역할로 사용한다.
5. 기존 디자인 시스템과 이미 합의한 UX 흐름을 우선한다. 참고 도구 때문에 전체 화면이나 서비스 정책을 임의로 변경하지 않는다. 핵심 정책이 불명확하면 필요한 질문을 한다.
6. 실제 적용 전 공식 README와 설치 안내에서 지원 환경을 확인한다. 설치 명령을 추측하거나 사용하지 않은 도구를 적용했다고 보고하지 않는다.
7. 구현 후 모바일 화면, 글자 가독성, 키보드 조작, 포커스 이동, 주요 버튼과 오류 복구를 확인한다. 모션은 사용자 행동과 상태 변화를 이해하는 데 도움이 될 때 적용하고 동작 감소 설정을 고려한다.
8. 도구의 검토 결과와 실제 사용자 테스트 결과를 구분한다. 실제 사용자가 핵심 작업을 완료하는지, 어디서 멈추는지 확인한다. 테스트를 하지 않았다면 검증 필요 항목으로 남긴다.
9. 작업 계획과 결과에 선택한 도구, 선택 이유, 적용 범위, 확인한 내용과 남은 가정을 짧게 기록한다.

## UX 검토 체크리스트

- **사용자 목표:** 누구의 어떤 문제를 해결하는 화면인가?
- **정보 구조:** 사용자가 필요한 메뉴와 정보를 쉽게 찾을 수 있는가? 웹페이지·앱은 먼저 [읽기·다음 행동 찾기](readability-qa.md)를 간격 → 색 → 아이콘 → 문구 → 글자 크기·굵기 → 배치 → 테두리·그림자 → 이미지 → 움직임 순서로 점검한다. 같은 기능의 아이콘·이름·위치는 화면마다 일관적인가?
- **작업 흐름:** 목적을 달성하기 위한 단계가 불필요하게 많지 않은가? 뒤로 가기와 취소가 가능한가?
- **입력과 안내:** 필요한 항목만 입력받는가? 버튼을 누르면 무엇이 일어나는지 알 수 있는가? 이어서 [45개 UI 패턴](ui-patterns-45.md)을 살펴본다. 사용자 행동과 가장 가까운 곳에 피드백을 주고 같은 내용을 토스트로 중복 표시하지 않는가?
- **로딩:** 진행 중임을 알 수 있는가? 중복 제출을 방지하는가?
- **빈 화면:** 데이터가 없을 때 이유와 다음 행동을 안내하는가?
- **오류:** 무엇이 잘못됐고 어떻게 해결할지 알려 주는가? 가능한 경우 입력 내용이 유지되는가?
- **완료:** 작업이 성공했는지, 이후 무엇을 할 수 있는지 알 수 있는가?
- **접근성과 환경:** 모바일, 키보드, 화면 읽기 도구와 모션 감소 설정을 고려했는가?
- **사용성 확인:** 실제 사용자에게 구체적인 작업을 맡겨 완료 여부, 혼란과 실수를 관찰했는가?

## 이 문서와 서비스별 UX 문서의 역할

이 문서에는 디자인 도구의 선택 기준과 UI·UX 활용법을 둔다. 서비스의 구체적인 화면 흐름과 정책은 필요할 때 별도 문서에 기록한다. 예를 들어 `monitoring-service-ux.md`에는 오류 발견 → 원인 이해 → 해결 행동 → 해결 여부 확인의 흐름, 화면별 상태, 예외 상황과 사용자 테스트 결과를 담는다. 현재 작업에 별도 UX 문서가 없다면 핵심 흐름부터 작업 계획에 정리한다.

## 빠른 선택표

아래 조합은 작업별 추천 순서이며 필수 구성은 아니다.

| 작업 상황 | 우선 검토할 도구 | 필요할 때 추가할 도구 |
|---|---|---|
| 사용자 목표와 핵심 이용 흐름 설계 | [ui-ux-pro-max](#ui-ux-pro-max)의 UX 패턴 참고 | [google-stitch](#google-stitch)로 흐름을 시안화한 뒤 사용자 테스트 |
| 기존 흐름에서 혼란과 이탈 개선 | [impeccable](#impeccable)로 화면과 문구 검토 | 사용성 관찰로 원인과 개선 효과 확인 |
| 새 웹사이트 UI 제작 | [frontend-design](#frontend-design) | [impeccable](#impeccable)로 마무리 검토 |
| 앱 또는 대시보드 디자인 규칙 설정 | [ui-ux-pro-max](#ui-ux-pro-max) | [google-stitch](#google-stitch)로 화면 흐름 시안 확인 |
| 개성 있는 랜딩 페이지나 포트폴리오 | [taste-skill](#taste-skill) 또는 [hallmark](#hallmark) | [react-bits](#react-bits)로 필요한 시각 효과 추가 |
| 기존 화면의 간격, 색상, 강조 개선 | [impeccable](#impeccable) | [hallmark](#hallmark)로 구조 재검토 |
| 메뉴, 팝업, 버튼 애니메이션 개선 | [emil-design-eng](#emil-design-eng) | [keyframe-gallery](#keyframe-gallery)에서 사례 탐색 |
| 텍스트, 배경, 카드에 효과 추가 | [react-bits](#react-bits) | [emil-design-eng](#emil-design-eng)로 모션 검토 |
| 코딩 전 앱 화면과 이동 흐름 확인 | [google-stitch](#google-stitch) | 구현 단계에서 디자인 스킬 선택 |
| 웹, 발표 자료, 이미지 등 함께 제작 | [open-design](#open-design) | 영상이 필요하면 [hyperframes](marketing-guide.md#hyperframes) 검토 |
| 같은 형식의 영상을 반복 제작 | [remotion-skills](marketing-guide.md#remotion-skills) | [awesome-opus5-5-videos](marketing-guide.md#awesome-opus5-5-videos)에서 아이디어 참고 |
| HTML/CSS 기반 제품 소개 영상 | [hyperframes](marketing-guide.md#hyperframes) | 기존 브랜드 규칙 적용 |
| 대화 중심으로 설명 애니메이션 제작 | [claude-motion](marketing-guide.md#claude-motion) | 이용 가능한 요금제와 기능 확인 |
| 모션 그래픽이나 3D 장면 아이디어 탐색 | [awesome-opus5-5-videos](marketing-guide.md#awesome-opus5-5-videos) | 결과물에 맞는 제작 도구 별도 선택 |

## 디자인 도구

**등록됨 ≠ 설치됨.** 서비스에 필요한 스킬 3~5개만 고른 뒤, 새 설치·업데이트는 [보안 검토](tool-security-review.md) → 승인 정책에 따른 설치 → 사용 순서로 진행한다. 검색: `grep -i <키워드> docs/design-guide.md docs/marketing-guide.md`.

고를 때 차이: hallmark는 페이지 구조를 새로 잡을 때, unslop은 반복 장식을 덜어낼 때, impeccable은 전체 화면을 평가해 수정 우선순위를 정할 때 중심 도구 하나로 고른다. ui-ux-pro-max는 여러 화면의 공통 규칙, interface-design은 정보·조작 흐름, taste-skill은 레퍼런스에 맞춘 분위기 조정에 우선한다.

설치 상태는 2026-11-25 읽기 확인 기준이다. Mac Claude는 `~/.claude/skills/`와 `~/.claude/plugins/installed_plugins.json`·해당 설치 경로를 대조했다. Mac Codex는 `~/.codex/skills/`·`~/.codex/plugins/` 및 이 세션의 스킬 목록에서 확인했다. 표의 **미설치는 해당 에이전트 사용자 설치 경로에서 찾지 못했다는 뜻**이며, 모든 프로젝트의 의존성까지 조사했다는 뜻이 아니다. 현재 세션과 Mac Codex 설치 경로에는 Codex Security의 `$check-skills`가 없어 사용할 수 없으며, 이번에 설치하거나 실행하지 않았다. Windows는 직접 확인할 수 없어 2026-11-20 작업자 보고를 옮겼고 버전·현재 상태는 확인 필요다. 웹 서비스·레퍼런스 사이트는 설치형 스킬과 구분한다. 명시하지 않은 라이선스는 확인 필요다.

| 이름 | 종류 | 언제 쓰나 | 원본 URL | 설치 상태(기기·에이전트별, 버전) | 검토 기록 |
|---|---|---|---|---|---|
| <a id="frontend-design"></a>Frontend Design (`frontend-design`) | 스킬 | 명세서를 바탕으로 랜딩 페이지·웹 UI 첫 시안을 구현. 서비스 목적에 맞춰 글자·색·배치·핵심 행동의 우선순위를 정한다. | https://github.com/anthropics/skills/tree/main/skills/frontend-design | Mac Claude: 설치됨(1.1.1, 커밋 f8c5e7a1dcb71b10cb74586224b5401f3de5db28, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 설치 보고(2026-11-20), 버전·현재 상태 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="ui-ux-pro-max"></a>UI UX Pro Max (`ui-ux-pro-max`) | 스킬·플러그인 | 여러 화면의 색상·타이포그래피·컴포넌트 스타일과 UX 패턴을 정한다. 앱·대시보드·폼 흐름에 사용하고 Flutter·SwiftUI·React 등 실제 스택에 맞는 지침만 선택. 로컬 플러그인 메타데이터의 라이선스: MIT. | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill | Mac Claude: 설치됨(2.15.0, 커밋 2de1134dc4d1cfe112934611ae5803ae17a1bd7e, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 설치 보고(2026-11-20), 버전·현재 상태 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="interface-design"></a>Interface Design (`interface-design`) | 스킬 | 관리자 페이지·대시보드·설정 화면의 정보 우선순위와 조작 흐름. 쉽앤핑의 장애 상태와 다음 행동 배치에 적합. | https://github.com/Dammyjay93/interface-design | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="hallmark"></a>Hallmark (`hallmark`) | 스킬 | 큰 제목 → 기능 카드 3개 구성이 반복될 때 제품의 실제 사용 장면 중심으로 테마와 구조를 바꾼다. 모바일 가독성과 핵심 작업 수행 우선. | https://github.com/Nutlope/hallmark | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="taste-skill"></a>Taste Skill (`taste-skill`) | 스킬 | 레퍼런스로 분위기를 정하고 브라우저에서 여백·폰트·배치를 다시 조정. 정보 밀도·모션이 이해를 방해하지 않는지 확인. 수치 설정과 하위 스킬은 선택 버전 문서에서 확인. | https://github.com/Leonxlnx/taste-skill | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="unslop"></a>unslop (`unslop`) | 스킬 | 기존 화면에서 과한 그라데이션·발광·불필요한 배지 등 반복적인 AI 장식을 정리. | https://github.com/heydamir/unslop | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="impeccable"></a>Impeccable (`impeccable`) | 스킬 | 기존 화면을 평가하고 고칠 순서를 정한다. 배지·제목·카드의 강조, 문구, 글자·간격과 로딩·오류·빈 화면 안내를 점검. audit·critique 등은 설치 버전 기준. | https://github.com/pbakaus/impeccable | Mac Claude: 설치됨(SKILL.md 선언 4.10.0, 복사본·커밋 확인 불가, 2026-11-20 사용자 승인 설치; 스킬만 복사·자동 편집 훅 제외, 기존 기록상 첫 사용 시 엔진 다운로드, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="ui-skills"></a>UI Skills (`ui-skills`) | 스킬 카탈로그 사이트 | 접근성·애니메이션·컴포넌트 마감 문제에 맞는 개별 스킬을 찾는다. 사이트 자체를 설치하지 않으며 선택한 개별 스킬의 실제 원본을 검토. | https://www.ui-skills.com/ | Mac Claude·Mac Codex·Windows Codex: 사이트 자체 설치 대상 아님; 개별 스킬 설치 여부 확인 필요 | 검토 기록 없음 |
| <a id="emil-design-eng"></a>emil-design-eng (Emil Kowalski Skills) (`emil-design-eng`) | 스킬 모음 | 버튼 반응·모달 전환·팝오버·제스처의 속도·감속·타이밍을 다듬는다. animate·review-animations 등으로 상태 변화를 전달하고 반복 작업 지연·종료·입력 처리 확인. | https://github.com/emilkowalski/skills | Mac Claude: 설치됨(버전·커밋 확인 불가(복사본, 2026-11-20 사용자 승인 설치), 하위 스킬 20개, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 설치 보고(2026-11-20), 버전·현재 상태 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="web-design-guidelines"></a>web-design-guidelines (`web-design-guidelines`) | 스킬 | 배포 전 키보드 조작·포커스 표시·폼 사용성 등 웹 UI의 기본 품질 점검. | https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines | Mac Claude: 해당 스킬 미설치(vercel 플러그인 설치와 구분); Mac Codex: 미설치; Windows Codex: 설치 보고(2026-11-20), 버전·현재 상태 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="google-stitch"></a>Google Stitch (`google-stitch`) | 실행 도구(웹 작업 공간) | 코딩 전 앱·웹 화면 시안과 이동 흐름을 비교하고 DESIGN.md로 디자인 정보 전달. 사용자에게 프로토타입 작업을 맡겨 관찰하며 실제 기능 구현 완료와 구분. | https://stitch.withgoogle.com | Mac Claude·Mac Codex·Windows Codex: 웹 서비스, 계정·이용 가능 여부 확인 필요 | 검토 기록 없음 |
| <a id="open-design"></a>Open Design (`open-design`) | 실행 도구(작업 공간) | 같은 브랜드의 웹·앱 시안·발표·이미지를 한곳에서 관리하고 에이전트와 연결. 영상 연계는 [hyperframes](marketing-guide.md#hyperframes) 참조. 단일 스킬이 아니므로 실행 환경과 연결 에이전트 확인. | https://github.com/nexu-io/open-design | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="react-bits"></a>React Bits (`react-bits`) | 실행 도구(컴포넌트 모음) | React 랜딩 페이지의 텍스트·배경·카드 효과를 미리 보고 코드에 적용. Flutter·SwiftUI에 그대로 넣지 않으며 효과별 의존성·모바일 성능·가독성·키보드 조작 확인. | https://github.com/DavidHDev/react-bits | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="keyframe-gallery"></a>Keyframe Gallery (`keyframe-gallery`) | 레퍼런스 사이트 | 제품의 화면 전환·메뉴·버튼 모션 사례를 비교하고 속도·방향을 전달할 때. 현재 사용자 작업에 맞는지 판단하며 구현 도구는 별도 선택. | https://keyframe.gallery | Mac Claude·Mac Codex·Windows Codex: 사이트 자체 설치 대상 아님 | 검토 기록 없음 |
| <a id="expo-design"></a>Expo 디자인 스킬 (`expo-design`) | 스킬·플러그인 | 기존 기록의 expo-design-system·expo-animation·expo-native-ui를 Expo 앱의 디자인 시스템·애니메이션·네이티브 UI 작업에 선택. | https://github.com/expo/skills/tree/main/plugins/expo | Mac Claude: 설치됨(1.13.9, 커밋 d4f484024fec15196bfd3c272e953e3f983972cf, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="uiflow"></a>uiflow (`uiflow`) | 스킬·플러그인 | 기존 design-compare·design-qa·design-sweep으로 앱 화면 전후 비교·디자인 QA·기기별 화면 검토. 로컬 플러그인 메타데이터 라이선스: MIT. | https://github.com/taehyeooo/taeng-marketplace/tree/main/plugins/uiflow | Mac Claude: 설치됨(0.2.1, 커밋 c080db5f994df10720f439ce88bba3699280d8b5, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="hig-components-layout"></a>hig-components-layout (`hig-components-layout`) | 스킬 | Apple HIG의 사이드바·탭·목록 등 레이아웃·탐색 컴포넌트를 검토. 로컬 SOURCE·LICENSE 확인: MIT. | https://github.com/raintree-technology/apple-hig-skills | Mac Claude: 설치됨(1.0.0, SOURCE 기록 커밋 fdc3599, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="hig-patterns"></a>hig-patterns (`hig-patterns`) | 스킬 | Apple HIG의 온보딩·검색·설정·로딩·피드백 등 상호작용 패턴을 검토. 로컬 SOURCE·LICENSE 확인: MIT. | https://github.com/raintree-technology/apple-hig-skills | Mac Claude: 설치됨(1.0.0, SOURCE 기록 커밋 fdc3599, 확인 2026-11-25); Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |

설치 근거 보충: frontend-design·ui-ux-pro-max·expo·uiflow의 버전·커밋은 설치 기록과 캐시를 대조했다. `web-design-guidelines`는 설치 경로에서 발견하지 못했다. Mac Claude의 `vercel@claude-plugins-official` 0.56.0 설치만으로 그 스킬을 설치됨으로 간주하지 않는다.

emil-design-eng 행에 합친 복사본 20개: `animate`, `animate-expo`, `animation-performance`, `animation-vocabulary`, `apple-design`, `ask-emil`, `ask-sonner`, `break-ui`, `choreograph`, `emil-design-eng`, `find-animation-opportunities`, `gestures`, `improve-animations`, `learn-taste`, `mobile-native`, `pick-ui-library`, `prototype`, `remove-animations`, `review-animations`, `write-swift`. Windows의 apple-design 사용 보고도 이 묶음에 포함하며 확인 필요다. 기존 기록의 라이선스는 Emil 묶음 MIT·Impeccable Apache-2.0이나, 이 복사본에는 LICENSE가 없어 설치 대상 버전에서 다시 확인한다. Impeccable 엔진 다운로드 동작은 안전성 검토 완료를 뜻하지 않는다.


## 사용 결과 기록 예시

```text
작업: 비전공자용 모니터링 대시보드 개선
사용자 목표: 문제가 있는지 이해하고 다음 행동을 선택하기
UX 흐름: 오류 발견 → 쉬운 원인 설명 → 해결 행동 → 해결 여부 확인
선택: UI UX Pro Max + Impeccable
이유: UX 패턴과 화면 규칙을 참고하고 구현 후 문구와 정보 강조를 검토하기 위해
UI 범위: 대시보드와 오류 상세 화면의 글자, 간격, 색상과 버튼 강조
UX 범위: 문제 탐색 경로, 다음 행동 안내, 로딩·빈 화면·오류·완료 상태
구현 검증: 모바일 레이아웃, 키보드 조작, 주요 버튼과 실패 후 재시도
사용성 검증: 비전공자에게 특정 오류를 찾고 다음 행동을 선택하는 작업 요청
기록: 실제 완료 여부, 막힌 위치, 헷갈린 표현과 수정 결과
미확인: 사용자 테스트 전이라면 사용성 검증 완료로 보고하지 않음
```

