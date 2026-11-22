# Meta API 연결 가이드 (P1)

> 유의: 여기에는 실제 인증 토큰/계정 ID가 들어 있지 않다. 2026-10-19 당시 기준이며 Meta의 최신 공식 제품 문서를 다시 확인할 것.

## 현재 사용자 상황
- 본인 Instagram 프로페셔널 계정, Facebook 페이지, Meta Developers 앱까지 생성 완료.
- 아직 Facebook 페이지와 Instagram 프로 계정이 실제 연결되어 있는지는 미확인.

## 우리가 선택한 API 경로
- 제3자 프로 계정 게시물/기본 지표 분석 → **Instagram API with Facebook Login** 기반 Business Discovery 시험.
- 이 경로는 Instagram 프로 계정과 Facebook 페이지 링크가 필요.
- Instagram Login 경로는 본인 콘텐츠 관리에 유용하나 목적에 맞는 Business Discovery를 같은 식으로 제공한다고 가정하지 말 것.

## 사용자 안내 순서
1. `https://business.facebook.com/` 에서 페이지와 애니멀픽 Instagram 프로 계정이 연결되었는지 확인.
2. `https://developers.facebook.com/apps/` 앱 대시보드에서 적절한 Instagram/FB Login 제품·이용 사례 설정 확인.
3. `https://developers.facebook.com/tools/explorer/`에서 **자신의 앱** 선택 → User Access Token 발급.
4. 초기 스코프 `pages_show_list`, `pages_read_engagement`, `instagram_basic` 및 해당 Business Discovery 제품에서 추가 요구하는 최신 스코프를 공식 문서로 확인. 토큰 승인 후 스코프와 자산 선택도 확인.
5. `GET /me/accounts?fields=id,name,instagram_business_account{id,username}` 성공 확인.
6. 반환된 본인 IG ID를 통해 Business Discovery 호출: `GET /{IG_ID}?fields=business_discovery.username(heodukgoo){username,followers_count,media_count,media.limit(10){id,caption,media_type,timestamp,permalink,like_count,comments_count}}`
7. 실패 시 **오류 코드/메시지와 토큰 만료/스코프/페이지 연결 상태만** 수집해 원인 진단. 비밀값을 공유하지 않음.

## Python 스모크 테스트
1. `Copy-Item .env.example .env` (Windows PowerShell).
2. `.env`에 `META_ACCESS_TOKEN` 입력 (로컬 파일이며 Git 제외). `META_GRAPH_API_VERSION` 기본 예제는 `v26.0` — Meta 실제 지원 여부 확인.
3. `python scripts/meta_probe.py pages`
4. 반환된 `instagram_business_account.id`를 `META_IG_USER_ID`에 입력.
5. `python scripts/meta_probe.py discovery --handle heodukgoo --limit 10`
6. 소스별로 누락된 지표를 명확히 기록.

실제 네트워크 요청 전후로 코드 리뷰 가능. 토큰을 URL 쿼리스트링이나 로그에 남기지 않고 `Authorization: Bearer` 헤더를 사용하도록 구현.

## 보안/법규/한계
- 타 계정은 프로페셔널 계정이라도 권한·숨김 설정·제품 지원에 따라 데이터 반환이 달라진다.
- 댓글/좋아요 등은 일부 공개 지표가 접근 가능하지만, 타 계정의 공유/저장/도달 인사이트는 일반적으로 불가.
- 계정이 라이선스 없이 타 게시물 사진을 가져와 재게시해서는 안 된다.
- 비공식 로그인 세션 자동화, 차단 우회, CAPTCHA 우회 등을 대체 수단으로 쓰지 않는다.
- 수집 결과를 '조회수'라고 명명하려면 실제 조회수 필드가 반환되었는지 확인해야 한다.

## 공식/1차 문서 참조
- Meta Developers: https://developers.facebook.com/apps/
- Graph API Explorer: https://developers.facebook.com/tools/explorer/
- Instagram API with Facebook Login (Meta Postman): https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- Business Discovery (공식 경로; 리다이렉트 가능): https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery
- OpenAI Codex repo guidance: https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex
