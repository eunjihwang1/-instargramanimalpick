# 단계별 개발 로드맵

| 단계 | 목표 | 최소 완료 기준 | 주의 |
|---|---|---|---|
| P0 | 인수인계 검증 | README/AGENTS/컨텍스트 파악, 오프라인 테스트 통과 | 실제 API 성공 주장 금지 |
| P1 | Meta API 연결 | `/me/accounts`, `@heodukgoo` Business Discovery 호출, 오류/허용 필드 기록 | 유저 토큰 비공개 |
| P2 | 계정 수집기 | 레퍼런스 13개 중 접근 가능한 계정의 최근 게시물 DB 저장, 중복 제거, 결측치 처리 | 공개/허용 자료만 |
| P3 | 인기 포맷 분석 | 같은 계정 기준선, 공개 반응, 첫 장·캡션·주제 분류, 근거/신뢰수준 저장 | 조회수 원인 단정 금지 |
| P4 | 신규 이슈 수집 | 허용된 RSS/API 출처 기반 소재 후보, 신뢰도·시의성·중복 검사 | 사실 검증, 자료 권리 |
| P5 | 콘텐츠 제작 | 4:5 캐러셀 템플릿, 후킹/본문, 자료 라이선스 기록, 검수·미리보기 | 남 콘텐츠 복제 방지 |
| P6 | 발행 자동화 | dry-run → 사용자 승인 후 실계정 테스트 → 예약/재시도/오류 알림 | 처음부터 자동 공개 금지 |
| P7 | 성과 개선 | 게시 후 24/72시간 및 장기 성과, 도달/저장/공유 등 **본인 계정** 인사이트 피드백 | 변경된 지표 뜻 확인 |

## MVP 제안
1. 13개 계정 CSV 로드 → 하나씩 Business Discovery 호출(지원 가능한 프로 계정만) → 원문 JSON 저장.
2. `posts`/`accounts` 테이블 구성, 원본 URL·수집 시각·반응 지표 저장.
3. 인기 게시물 10개와 상대적 저성과 게시물 10개에 대한 이미지/문구 분석 결과와 근거를 보여주는 간단한 HTML 대시보드.
4. API 성공/실패 및 계정별 지원 필드 매트릭스 출력.

## 분석용 기본 데이터 모델 (초안)

### reference_accounts
`username` (unique), `label`, `category_guess`, `source`, `last_checked_at`, `access_status`, `follower_count?`, `notes`

### reference_posts
`post_id` (unique), `reference_username`, `permalink`, `published_at`, `collected_at`, `media_type`, `caption`, `like_count?`, `comments_count?`, `view_count?`, `raw_response_path`, `api_version`

### post_analysis
`post_id`, `hook_text?`, `topic_labels`, `emotion_labels`, `visual_structure`, `hypotheses`, `evidence`, `relative_performance_metric?`, `confidence`, `analyzed_at`, `model_version`

### candidate_stories
`candidate_id`, `source_urls`, `discovered_at`, `fact_check_status`, `image_rights_status`, `similar_reference_posts`, `editorial_score`, `decision`

### publishing_jobs
`job_id`, `candidate_id`, `carousel_asset_paths`, `caption`, `scheduled_at`, `status`, `approval_by_user_at?`, `ig_post_id?`, `error`

항목에 `?`는 API가 제공하지 않거나 수집 불가 시 `NULL` 유지. 스키마 확정은 첫 실측 응답 후.
