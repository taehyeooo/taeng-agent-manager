@AGENTS.md

## Claude Code에서만
- 기획·지식베이스·위임 지시문·이어하기는 설치된 intaeng 스킬(`/intaeng:기획`, `/intaeng:지식베이스`, `/intaeng:맡기기`, `/intaeng:이어하기`)을 쓴다.
- UI·UX·화면 구성·인터랙션·애니메이션 요청은 AGENTS.md 'UI 및 UX 디자인 작업 참고'대로 `docs/design-tools-reference.md`를 먼저 읽는다. 이 기기 Claude에 설치된 디자인 스킬(예: `frontend-design:frontend-design`, `expo:expo-design-system`·`expo:expo-animation`·`expo:expo-native-ui`, `uiflow:design-compare`·`design-qa`·`design-sweep`)은 Skill 목록에서 실제로 있는지 확인하고 쓴다.
- 리뷰는 `/reviewflow:review`. Orca 워커는 `orca skills get orchestration`의 방식대로(시작 후 턴 종료, 폴링 금지).
