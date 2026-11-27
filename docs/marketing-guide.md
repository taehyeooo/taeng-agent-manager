# 마케팅 가이드 — 릴스·홍보 영상·게시물

매니저는 홍보 영상이나 인스타그램·스레드 게시물을 계획하기 전에 이 문서를 처음부터 읽는다(사용자 지시 2026-11-23). 앱 안의 화면과 애니메이션은 [디자인 가이드](design-guide.md)를 따른다.

게시·발송은 사용자가 직접 한다.

## 1. 릴스·홍보 영상 제작 순서

| 단계 | 누가 | 하는 일 |
|---|---|---|
| 1. 요청 확인 | 매니저 | 시청자가 영상을 보고 무엇을 알거나 하길 원하는지, 영상 길이, 올릴 곳(인스타그램 릴스·유튜브 쇼츠·스레드 등)을 확인한다. 요청에서 빠진 항목만 묻는다. |
| 1-1. 도구 고르기 | 매니저 | **서비스 시작 때 이 서비스에 쓸 스킬 3~5개만 고른다(전부 받지 않는다).** 아래 도구 표에서 목적·스택·설치 상태를 보고 필요한 것만 선택한다. |
| 1-2. 설치 전 보안 검토 | Codex·Claude Code | 새 설치·업데이트가 필요하면 [공통 검토 절차](tool-security-review.md)로 같은 버전을 독립 검토한다. 등록·링크 참고만이면 검토 불필요. |
| 1-3. 설치 → 사용 | 작업자 | 설치 요청·검토 완료·기존 승인 정책(`system_permission`, 유료면 `paid_service`, 사용자 `auto_gates` 설정 포함) 충족 후 검토한 버전만 설치해 사용하고 이 문서 도구 표의 상태·버전·확인일·검토 링크를 갱신한다. |
| 2. 레퍼런스 추천·선택 | 매니저 → 사용자 | 아래 표의 Prompt Motion부터 살펴보고 요청에 맞는 작품 3개를 추천한다. 작품별 **직접 링크, 글자 크기, 색, 컷이 바뀌는 속도, 화면 녹화가 차지하는 비중**을 나란히 보여 주고 사용자가 고르게 한다. 사용자가 링크를 주면 해당 영상을 직접 재생해 장면을 확인한다. 로그인 화면에서는 비밀번호를 입력하지 않는다. 재생할 수 없으면 그 사실을 알리고 공개 링크나 영상 파일을 요청한다. |
| 3. 구조 정하기 | 매니저 | 선택한 작품에서 참고할 점을 짚고 Hook(눈길을 끄는 첫 장면) → Problem(불편) → Solution(해결 방법) → Demo(실제 앱 시연) → CTA(다음 행동 안내) 순서로 구간별 시간과 메시지를 정한다. |
| 4. 제작 위임 | Codex 워커 | 매니저는 선택한 작품 링크, 참고할 연출, 게시할 곳, 길이와 구간별 메시지를 지시문에 적는다. 레퍼런스가 정해지면 워커는 `trova-marketing`의 `video/` 도구를 활용해 [HyperFrames](#hyperframes)로 비교판 없이 최종 영상 한 편만 만들고, 장면별 대표 프레임을 뽑는다. 이모지와 장식 효과를 빼고 정돈된 회사 공식 영상 톤으로 만든다. **앱 장면은 실제 앱 녹화만** 사용하고 녹화에 쓰는 데이터만 데모용으로 준비한다. |
| 5. 장면 검수 | 매니저 | 최종 영상에서 뽑은 프레임을 장면 순서대로 보며 큰 글자가 읽히는지, 실제 앱 화면과 문구가 맞는지 확인한다. 영상을 재생해 컷 속도와 글자를 읽을 시간을 점검한다. |
| 6. 결과 보고 | 매니저 | 최종 영상 및 프레임 미리보기와 함께, 5장 표에 구간별 큰 글자와 화면 종류를 기록한다. |

## 2. 용도별 레퍼런스 사이트

| 사이트 | 무엇을 볼 때 | 메모 |
|---|---|---|
| [Prompt Motion](https://prompt-motion.com/) | **사용자가 지정한 1순위.** Claude로 만든 모션 그래픽 영상과 작품별 프롬프트·스킬을 함께 살펴볼 때 | 앱 시연은 Product UI·Phone, 움직이는 글자는 Kinetic type, 설명 장면은 Charts·Diagrams·Characters 필터부터 본다. 프롬프트는 원 작성자의 저작권과 이용 조건을 확인해 복사·변형하고 출처 링크를 유지한다. |
| [Meta 광고 라이브러리](https://www.facebook.com/ads/library) | 국내 앱이 실제 집행 중인 인스타그램·페이스북 광고(릴스 포함)의 흐름을 볼 때 | 국가를 대한민국으로 두고 첫 장면부터 앱 시연까지의 순서를 본다. |
| [TikTok Creative Center (Top Ads)](https://ads.tiktok.com/business/creativecenter) | 성과가 좋은 숏폼 광고의 도입부와 컷 속도를 비교할 때 | Top Ads에서 업종·지역을 좁힌다. |
| Mobbin | 데모 장면 구성 시 → [디자인 가이드의 Mobbin](design-guide.md#디자인-레퍼런스-사이트) | — |
| [Screensdesign](https://screensdesign.com) | 앱 화면·온보딩 녹화에서 조작 순서를 볼 때 | 어떤 조작부터 결과 화면까지 보여 주는지 본다. |
| [Dribbble (Motion)](https://dribbble.com/search/motion) | 짧은 UI 모션이나 전환 아이디어를 찾을 때 | 화면 요소가 등장하고 사라지는 타이밍을 본다. |
| [Jitter](https://jitter.video/templates) | 제품 UI 모션 템플릿의 구성을 볼 때 | 글자와 제품 화면이 번갈아 등장하는 순서를 참고한다. |
| [Rotato](https://rotato.app) | 휴대폰 목업이 기울거나 회전하는 연출을 볼 때 | 공개 예시만 참고한다. 유료 앱은 구매하지 않는다. |

레퍼런스에서는 **구성·리듬·타이포그래피 방식만** 가져온다. 문구·로고·캐릭터·화면·색 조합을 그대로 쓰지 않는다. 유료 가입이나 구매에는 사용자 승인(`paid_service`)이 필요하므로 레퍼런스를 찾을 때는 무료 공개 자료를 사용한다.

## 3. 인스타그램·스레드 게시물 형식

| 형식 | 제작 기준 | 확인할 것 |
|---|---|---|
| 인스타그램 캐러셀 | 카드마다 1080×1350px(4:5) | 첫 카드에서 주제를 알 수 있고, 손가락으로 넘기는 순서대로 내용이 이어지는지 확인한다. |
| 인스타그램 릴스 | 1080×1920px(9:16) | Trova 릴스의 안전 영역 기준에 따라 위 250px·아래 420px·오른쪽 140px를 피해 제목, 자막, 앱의 핵심 조작을 배치한다. 장면별 프레임에서 움직이는 글자까지 확인한다. |
| 스레드 | 본문 400자 이내 | 끝 줄, 공백, 줄바꿈, 본문에 넣은 해시태그까지 포함해 글자 수를 센다. |

## 4. 홍보 문구와 해시태그

Trova 홍보 영상과 게시물에는 다음 기준을 적용한다.

- 가격·수치·실제 사용자 정보·과장 표현(“최고”, “유일” 등)·지어낸 경험담을 넣지 않는다.
- 일정은 “초안”으로 표시한다.
- **스레드 글 첫 줄은 독자가 자기 경험을 떠올릴 장면이나 질문으로 시작한다**(사용자 지시 2026-11-25). 기능 소개("릴스를 저장하는 기능")가 아니라 "분명 저장했는데 필요할 때 못 찾는 순간" 같은 상황에서 시작하고, 첫 문장이 질문 역할까지 하게 한다. 흐름은 장면·질문 → 공감 한두 줄(가벼운 구어체 가능) → "그래서 Trova를 만들었어요/이렇게 해 봤어요" 순서. 특정인의 실제 경험처럼 꾸미지 않고 누구나 겪을 법한 상황으로 쓴다. 슬랙에 원고를 보낼 때도 같다.
  예: 친구가 "전에 네가 보낸 그 성수 파스타집 어디였지?" 하고 물어보면, 다들 어디부터 찾아봐요? / 릴스에 저장해 둔 것 같기도 하고, 카톡으로 보낸 것 같기도 하고 ㅋㅋ / 이럴 때 저장한 영상을 다시 찾기 쉽게 만들고 싶어서 Trova라는 앱을 만들었어요.
- 해시태그는 `trova-marketing`의 `posts/hashtags.md` 목록에서 내용에 맞는 인기 국내여행 태그와 지역 태그를 골라 총 5~10개 쓴다. 내용과 관계없는 인기 태그는 넣지 않는다.

## 5. 영상 구간별 보고 형식

최종본을 기준으로 장면마다 한 줄씩 채운다. 큰 글자는 화면에 나온 문구 그대로 쓰고, 없으면 ‘없음’으로 적는다. 다음은 Trova 영상의 작성 예시다.

| 구간 | 화면에 나온 큰 글자 | 화면 종류와 내용 |
|---|---|---|
| 0–2초 · 도입 | 다음 여행, 어디부터 갈까? | 글자 연출 · 질문을 한 줄씩 표시 |
| 2–5초 · 불편 | 저장한 장소가 여기저기 흩어져 있다면 | 글자 연출 · 장소 이름을 화면 곳곳에 배치 |
| 5–8초 · 해결 | 여행 일정 초안으로 모아 봐요 | 글자 연출 · 흩어진 이름을 한 줄로 정렬 |
| 8–12초 · 시연 | 없음 | 실제 앱 녹화 · 데모 일정 초안에서 장소를 확인하는 장면 |
| 12–15초 · 다음 행동 | 내 여행 일정 초안 만들기 | 글자 연출 · 마지막 화면에 다음 행동 표시 |

## 영상·글쓰기·마케팅 도구

**등록됨 ≠ 설치됨.** 필요한 스킬 3~5개만 고른 뒤 새 설치·업데이트는 [보안 검토](tool-security-review.md) → 승인 정책에 따른 설치 → 사용 순서로 진행한다. 검색: `grep -i <키워드> docs/design-guide.md docs/marketing-guide.md`. 앱 UI는 [디자인 도구 표](design-guide.md#디자인-도구)를 참고한다.

고를 때 차이: hyperframes는 HTML 장면으로 짧은 홍보 MP4, remotion-skills는 React 템플릿을 반복 제작할 때, motion-skills는 글자·도형으로 작동 과정을 설명할 때 중심 도구 하나로 고른다. openmontage는 제작 전 과정을 묶을 때, ffmpeg-skill은 기존 영상 후처리, whisper는 음성 자막 초안, video2x는 화질·프레임 보완에 선택한다. marketing-skills는 구체적인 개선 문제별 스킬, anthropic-marketing은 출시 캠페인 묶음 작업에 선택한다. 설명 영상 제작을 제품 UX 개선이나 실제 사용자 검증 완료로 취급하지 않는다.

2026-11-25 읽기 확인. Mac Claude는 `~/.claude/skills/`·`~/.claude/plugins/installed_plugins.json`·설치 경로, Mac Codex는 `~/.codex/skills/`·`~/.codex/plugins/`·이 세션 스킬 목록 기준이다. **미설치는 해당 에이전트 사용자 설치 경로에서 찾지 못했다는 뜻**이며, 프로젝트의 실행 프로그램·의존성까지 모두 확인한 것은 아니다. Windows는 직접 확인하지 못했다. 라이선스는 모두 확인 필요다. HyperFrames의 프로젝트 로컬 사용은 기존 가이드 기록을 옮겼다. [trova-marketing package.json](https://github.com/Trovapp/trova-marketing/blob/8cce4eb30a3226ccffda8e5dff2d11cf8cd3a874/video/hyperframes/package.json)을 GitHub API와 Mac 로컬 파일로 읽어 의존성 선언 `hyperframes: 0.9.85`를 확인했다. Mac의 해당 경로에는 `node_modules/hyperframes/package.json`이 없어 현재 실행 패키지 설치는 확인되지 않는다. 의존성 선언과 실제 설치를 구분한다. 설치 상태와 검토 이력을 혼동하지 않는다.

| 이름 | 종류 | 언제 쓰나 | 원본 URL | 설치 상태(기기·에이전트별, 버전) | 검토 기록 |
|---|---|---|---|---|---|
| <a id="hyperframes"></a>HyperFrames (`hyperframes`) | 실행 도구(HTML 영상 제작) | 앱 스크린샷·홍보 문구·화면 전환을 HTML/CSS와 미디어로 구성해 짧은 MP4 제작. 웹 미리보기와 렌더링 완료를 구분하고 최종 MP4 화면·재생을 확인. | https://github.com/heygen-com/hyperframes | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요; 프로젝트: trova-marketing `video/hyperframes` (레포 로컬), 의존성 선언 0.9.85(레포 커밋 8cce4eb30a3226ccffda8e5dff2d11cf8cd3a874, 확인 2026-11-25); Mac 현재 패키지 미설치(node_modules 없음), 기존 제작 기록 있음; 다른 기기 설치 확인 필요 | 검토 기록 없음(이 절차 이전 설치) |
| <a id="remotion-skills"></a>Remotion Skills (Remotion) (`remotion-skills`) | 스킬·실행 도구 | React로 재사용할 영상 템플릿을 만든다. 인트로·자막 위치를 유지하며 기능별 릴스·주간 리포트 등을 데이터·문구만 바꿔 반복 제작. [프레임워크](https://github.com/remotion-dev/remotion)와 스킬을 구분해 검토. | https://github.com/remotion-dev/skills | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="motion-skills"></a>Motion Skills (`motion-skills`) | 스킬 | 텍스트·도형을 움직여 서비스 작동 과정을 짧은 설명 영상으로 제작. 동명 도구 중 verbodh-pte-ltd 프로젝트를 사용. | https://github.com/verbodh-pte-ltd/motion-skills | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="openmontage"></a>OpenMontage (`openmontage`) | 실행 도구 | 대본·장면 구성·소재 준비·합성까지 이어지는 영상 제작 작업. | https://github.com/OpenMontageInc/OpenMontage | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="ffmpeg-skill"></a>FFmpeg Skill (`ffmpeg-skill`) | 스킬 | 기존 녹화본 자르기·세로 비율 변환·자막·음량 조절·플랫폼별 내보내기. kajisho5 구현을 사용하며 FFmpeg 실행 프로그램은 별도 설치 대상 확인. | https://github.com/kajisho5/ffmpeg-skill | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="whisper"></a>Whisper (`whisper`) | 음성 인식 | 한국어 내레이션·시연 영상에서 시간 정보가 있는 자막 초안 생성. 결과의 고유명사·타이밍을 검수. | https://github.com/openai/whisper | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="melotts"></a>MeloTTS (`melotts`) | 음성 합성(무료·로컬) | 기계음이 아닌 자연스러운 한국어 내레이션 wav 제작. 화자 KR, 속도 1.1, "트루바" 발음 보정 포함. 합성은 네트워크 차단(`--network none`) 격리 상자에서만 하고, 결과는 음성 인식으로 받아쓰기 검수. 사용법은 Mac `~/orca/tts-melotts/README.md`. | https://github.com/myshell-ai/MeloTTS | Mac 본체: 미설치(사용자 결정); Mac Docker 이미지 `trova-melotts-kr:2091453`에만 설치(커밋 209145371cff8fc3bd60d7be902ea69cbdb7965a, torch 2.7.1 CPU, 확인 2026-11-27); Windows: 미설치 | [melotts-2091453](tool-reviews/melotts-2091453.md) |
| <a id="video2x"></a>Video2X (`video2x`) | 영상 처리 | 다시 촬영하기 어려운 저해상도 영상의 해상도를 높이거나 프레임 보간. | https://github.com/k4yt3x/video2x | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="threejs-skills"></a>Three.js Skills (`threejs-skills`) | 스킬 | 회전하는 휴대폰 목업·3D 제품 소개·웹에서 조작하는 입체 장면 제작. | https://github.com/CloudAI-X/threejs-skills | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="im-not-ai"></a>im-not-ai (`im-not-ai`) | 스킬(글쓰기) | 한국어 앱 소개글·출시 공지·자기소개서를 평소 말투로 다듬는다. 사실·수치는 유지하고 과장·반복 구조·불필요한 가운뎃점 정리. | https://github.com/epoko77-ai/im-not-ai | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="marketing-skills"></a>Marketing Skills (`marketing-skills`) | 스킬 모음(마케팅) | 다운로드 버튼 전환율·소개 페이지 문구·SEO 등 해결할 문제가 구체적일 때 필요한 개별 스킬만 선택. | https://github.com/coreyhaines31/marketingskills | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="anthropic-marketing"></a>Anthropic Marketing 플러그인 (`anthropic-marketing`) | 플러그인(마케팅) | 앱 출시 캠페인의 타깃·핵심 메시지·채널별 게시물·성과 보고서를 함께 준비. | https://github.com/anthropics/knowledge-work-plugins/tree/main/marketing | Mac Claude: 미설치; Mac Codex: 미설치; Windows Codex: 확인 필요 | 검토 기록 없음 |
| <a id="claude-motion"></a>Claude Motion (`claude-motion`) | 실행 도구(Claude 내 기능) | 대화로 데이터·아이디어를 설명 애니메이션으로 만들고 문구·타이밍 수정·내보내기. 기존 기록은 2026-10-08 Team·Enterprise 베타 안내 기준이므로 현재 계정의 요금제·제공 기능 확인. Codex 설치 스킬이 아니다. | https://claude.com/resources/articles/dashboards-and-motion | Mac Claude·Mac Codex·Windows Codex: 로컬 설치 대상 아님; 계정의 기능 이용 가능 여부 확인 필요 | 검토 기록 없음 |
| <a id="awesome-opus5-5-videos"></a>Awesome Opus 5.5 Videos (`awesome-opus5-5-videos`) | 레퍼런스 모음 | 모션 그래픽·3D·인터랙티브 작품과 프롬프트·원작 링크로 장면 아이디어 탐색. 일부 프롬프트는 없을 수 있고 다른 모델의 재현을 보장하지 않는다. 원작 이용 조건 확인. | https://github.com/yihui-dev/awesome-opus5-5-videos | Mac Claude·Mac Codex·Windows Codex: 참고 자료, 설치 대상 아님 | 검토 기록 없음 |
