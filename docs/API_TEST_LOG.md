# API 실험 기록

**기록 규칙:** 토큰, 쿠키, 앱 시크릿은 이 문서에 남기지 않는다. 네트워크 테스트를 실행하지 않았으면 성공 표시하지 않는다.

| 날짜 | API 버전 | Endpoint | 결과 | 실제 반환 내용 | 비고 |
|---|---|---|---|---|---|
| 2026-11-22 14:22 | v27.0 | `GET /me`, `GET /me/permissions` | 성공 | 승인: instagram_basic, pages_show_list, pages_read_engagement. **instagram_content_publish 없음** | Graph API 탐색기 단기 사용자 토큰 |
| 2026-11-22 14:22 | v27.0 | `GET /me/accounts` | 성공 | 페이지 '애니멀픽' ↔ Instagram **@animal._.pick** (IG ID 17841439246013647) | 핸들은 기획 문서의 `animal..pickk` 가 아니라 `animal._.pick` |
| 2026-11-22 14:22 | v27.0 | Business Discovery `heodukgoo` limit 10 | 성공 | 팔로워 195,755 / 게시물 10,039. 10개 모두 like_count·comments_count 있음 | `data/animalpick.db` 와 `data/raw/` 에 저장 |
| 2026-11-22 14:23 | v27.0 | Business Discovery 필드 실측 (게시물 3개) | 성공 | 기본 필드 전부 있음. `view_count` 는 **VIDEO 만**(캐러셀 0/3). `media_url` 3/3, `children{media_type,media_url}` 3/3, `thumbnail_url` 0/3 | 문서와 달리 캐러셀 조회수는 받을 수 없음 → 캐러셀 성과는 좋아요·댓글로 비교 |
| 2026-11-22 14:30 | v27.0 | 토큰 교환 (fb_exchange_token → /me/accounts) | 성공 | 페이지 토큰 type=PAGE, **만료 없음(expires_at=0)**, 권한에 instagram_content_publish 포함 | 첫 토큰엔 게시 권한이 빠져 탐색기에서 다시 골라 재발급 |
| 2026-11-22 14:30 | v27.0 | `GET /{ig}/content_publishing_limit` (페이지 토큰) | 성공 | quota_usage 0, **quota_total 100 / 86400초** | 문서의 100 vs 50 상충 → 실제 계정 한도는 100. 우리 안전 한도는 50 유지 |
| 2026-11-22 14:55 | v27.0 | **첫 실제 캐러셀 게시** (사용자가 대시보드에서 실행) | 성공 | 2장 캐러셀, https://www.instagram.com/p/DgCGl9RjwkZ/ | 이미지는 Cloudflare 임시 주소로 전달 → Instagram 이 정상 수신, 게시 후 주소 닫힘 |

## 결론
- Phase 1 완료 기준(실제 API 응답으로 다른 계정 게시물 조회·저장) 충족.
- 레퍼런스 분석에서 캐러셀은 조회수 없이 좋아요·댓글·팔로워 대비 비율로 비교해야 한다.
- 캐러셀의 하위 이미지 주소(`children.media_url`)를 받을 수 있어 첫 장·구성 분석이 가능하다 (분석용으로만, 재게시 금지).
