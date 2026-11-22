# Codex 첫 메시지 (아래 내용을 그대로 복사)

나는 이전 ChatGPT 대화에서 **AnimalPick 인스타그램 AI 매거진 자동화 프로젝트**를 설계했고, 이 저장소는 그 인수인계 패키지야. `AGENTS.md`, `README.md`, `docs/PROJECT_CONTEXT.md`, `docs/STATE_AND_OPEN_QUESTIONS.md`, `docs/ROADMAP.md`, `docs/META_API_SETUP.md`를 먼저 읽고 기존 결정 사항을 존중해 줘.

**지금 진행할 단계는 Phase 1 — Meta 공식 Instagram Graph API 연결 검증**이야.

1. 저장소 구조와 `scripts/meta_probe.py`, 오프라인 테스트를 점검해 실제 작동 가능 여부를 검토해 줘.
2. 내가 만든 Facebook 페이지가 Instagram 프로페셔널 계정과 연결되어 있는지 확인할 수 있도록 **비밀 정보 노출 없는 설정 안내**를 먼저 해줘. 연결은 아직 확인하지 않았어.
3. Meta 앱은 이미 생성했어. 내가 Graph API Explorer에서 액세스 토큰을 발급받되 **토큰을 채팅에 공유하지 않고 로컬 `.env`에 저장**하도록 도와줘.
4. `/me/accounts` 조회 성공을 확인하고 나서 `@heodukgoo` 대상 Business Discovery에서 어떤 필드가 실제 반환되는지 테스트해줘. 사용 권한과 API 버전은 최신 공식 문서로 재확인해 줘.
5. 성공 시 `docs/API_TEST_LOG.md`에 **검증된 정보만** 정리하고 13개 참고 계정 수집기 개발을 제안해 줘. 실패 시 응답 오류 코드만 보고 원인을 좁혀 줘.
6. 수집·분석부터 진행하고 자동 발행은 **별도 사용자 승인 전까지 하지 마**. 타인의 비공개 공유/저장 인사이트나 실제 조회수를 추측해서 확정치로 보고하지 마.

먼저 네가 이해한 현재 진행 상태와 **내가 지금 해야 할 단 한 단계**를 한국어로 설명해 줘.
