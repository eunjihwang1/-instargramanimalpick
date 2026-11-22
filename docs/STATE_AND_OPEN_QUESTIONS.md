> **이 문서는 2026-10-19 ChatGPT 인수인계 당시 기록이다. 현재 상태는 루트의 [PROJECT_STATE.md](../PROJECT_STATE.md) 를 본다.**

# 현재 진행 상태 (2026-10-19 현재, ChatGPT에서 전달)

## 사용자가 완료했다고 밝힌 사실
- [x] 애니멀픽 인스타그램 계정을 프로페셔널 계정으로 전환함.
- [x] Facebook 페이지를 생성함. (처음 오류가 있었지만 해결했다고 함)
- [x] Meta Developers에서 앱 생성(Step 3)까지 마침.
- [x] 레퍼런스 계정 목록 스크린샷 3개 및 애니멀픽 프로필 스크린샷 1개 전달함.

## 아직 확인되지 않은 사실
- [ ] Instagram 계정과 Facebook **페이지의 연결** 완료 여부.
- [ ] 앱에 **Instagram API with Facebook Login** 이용 사례/제품을 올바르게 추가했는지.
- [ ] 앱의 Instagram/Facebook 관련 권한이 승인되고 사용자/앱 역할이 테스트 가능한지.
- [ ] Graph API Explorer에서 사용자 액세스 토큰을 생성했는지.
- [ ] `/me/accounts?fields=id,name,instagram_business_account{id,username}` 응답.
- [ ] 대상 계정 `@heodukgoo`의 Business Discovery 요청 실제 성공 여부.
- [ ] 계정 유형이 Business인지 Creator인지. 둘 다 프로 계정일 수 있으므로 실제 지원/권한 점검.
- [ ] 인스타 핸들 `animal..pickk`의 정확한 철자/숫자 IG ID.
- [ ] 팔로잉 14번째 참고 계정.

## 아직 개발/실행하지 않은 것
- [ ] 인증 데이터 저장 및 토큰 갱신 운영 프로세스.
- [ ] 13개 참고 계정의 실측 지표 DB.
- [ ] 이미지/제목/성과 패턴 분석 엔진.
- [ ] 외부 뉴스·트렌드 수집, 사실 검증, 권리 확인 엔진.
- [ ] 캐러셀 자동 합성 템플릿 및 큐레이션 승인 UI.
- [ ] 자동 예약 발행 및 성과 피드백.

## 다음 작업 (딱 하나)
우선 Meta Graph API Explorer 또는 `scripts/meta_probe.py`로 **자기 계정과 Facebook 페이지의 연결**을 확인한다. 앱/페이지 연결이 안 되어 있으면 먼저 해결한다. 이후 Business Discovery를 검증한다.

## 기존 대화에 관한 불확실성
- 이전 답변의 Meta 보너스 자격 수치와 API 필드 목록은 자동으로 진실이라고 간주하지 않는다. 플랫폼이 변할 수 있고 사용자의 계정 화면과 공식 API 응답이 우선이다.
- 파일에 토큰/API 키/개인 로그인 정보는 존재하지 않는다. 사용자에게 비밀값을 제출하라고 요청하지 않는다.
