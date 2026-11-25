@AGENTS.md

## Claude Code에서만
- 설치 전 검토에서 Claude Code 쪽 검토 방법은 [공통 보안 검토 절차의 해당 절](docs/tool-security-review.md#claude-code가-검토하는-방법)을 따른다.
- 기획·지식베이스·위임 지시문·이어하기는 설치된 intaeng 스킬(`/intaeng:기획`, `/intaeng:지식베이스`, `/intaeng:맡기기`, `/intaeng:이어하기`)을 쓴다.
- 디자인 요청은 AGENTS.md '디자인'대로 `docs/design-guide.md`를 먼저 읽는다. 이 기기에서 쓸 수 있는 디자인 스킬은 그 문서의 도구 표와 Skill 목록으로 확인한다.
- 홍보 영상·게시물 요청은 AGENTS.md '마케팅(홍보 영상·게시물)'에 따라 작업 계획 전에 [마케팅 가이드](docs/marketing-guide.md)를 처음부터 읽는다.
- 리뷰는 `/reviewflow:review`. Orca 워커는 `orca skills get orchestration`의 방식대로(시작 후 턴 종료, 폴링 금지).
