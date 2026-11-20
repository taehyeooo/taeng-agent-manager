@AGENTS.md

## Claude Code에서만
- 기획·지식베이스·위임 지시문·이어하기는 설치된 intaeng 스킬(`/intaeng:기획`, `/intaeng:지식베이스`, `/intaeng:맡기기`, `/intaeng:이어하기`)을 쓴다.
- 디자인 요청은 AGENTS.md '디자인'대로 `docs/design-guide.md`를 먼저 읽는다. 이 기기에서 쓸 수 있는 디자인 스킬은 그 문서 5장 표와 Skill 목록으로 확인한다.
- 리뷰는 `/reviewflow:review`. Orca 워커는 `orca skills get orchestration`의 방식대로(시작 후 턴 종료, 폴링 금지).
