# CLAUDE.md — animalpick

애니멀픽(@animal..pickk) 인스타그램 매거진 자동화. Python 3.12, 표준 라이브러리 우선.

## 먼저 읽을 것
1. `PROJECT_STATE.md` — 진행 상태 정본 (작업 후 반드시 갱신)
2. `AGENTS.md`, `docs/PROJECT_CONTEXT.md`, `docs/ROADMAP.md`, `docs/CONTENT_GUIDELINES.md`

## 절대 규칙
- 토큰·앱 시크릿은 `.env` 에만. 출력·로그·커밋·채팅 금지. 사용자에게 토큰을 채팅으로 달라고 하지 않는다.
- 실제 Instagram 게시, GitHub 업로드는 사용자 승인 후에만.
- 실제 API 응답으로 확인하기 전에는 완료라고 보고하지 않는다. `docs/API_TEST_LOG.md` 에 비밀값 없이 기록.
- 타 계정의 저장·공유 등 API 가 주지 않는 값은 NULL. 추정치를 사실처럼 저장하지 않는다.
- 비공식 스크래핑·로그인 우회 금지.
- 게시 이미지는 로컬에만 둔다. 게시 순간에만 `animalpick/tunnel.py` (Cloudflare 임시 주소)로 공개하고 바로 닫는다. GitHub 업로드 방식은 `MEDIA_HOSTING=github` 일 때만 쓰는 보조 수단.
- 대시보드는 한 번에 하나만 뜬다(포트 3800 독점). 새 코드 반영하려면 기존 창을 닫고 다시 실행.
- `tools/cloudflared.exe` 는 Git 에 없다. 새 PC 에서는 cloudflare/cloudflared 공식 릴리스에서 받아 서명 확인 후 tools/ 에 둔다.

## 명령
```powershell
python -B -m unittest discover -s tests -v
python scripts/meta_probe.py whoami | pages
python scripts/meta_probe.py discovery --handle heodukgoo --limit 10 --save
```

AI 분석 단계(Phase 3~)는 `어치` 의 다른 프로젝트처럼 Claude 구독 인증(Agent SDK, API 키 환경변수 삭제)을 쓴다.
