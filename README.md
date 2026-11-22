# AnimalPick — Instagram AI Magazine Automation

## 사용법 (2026-11-22 ~ Claude Code)

1. **`대시보드 실행.bat`** 더블클릭 → 브라우저에 `http://localhost:3800` 제작실이 열립니다.
   (처음 한 번은 필요한 프로그램을 자동 설치합니다)
2. 왼쪽에 **소재 한 줄 + 출처 기사 주소**를 넣고 [원고 + 캐러셀 만들기] → 1~2분 뒤 캐러셀 완성.
   - 원고: Claude 구독으로 작성 (출처에 있는 사실만)
   - 사진: 위키미디어 공용에서 사용 허가(CC0·퍼블릭 도메인·CC BY)된 것만, 저작자 자동 표기
3. 문구를 고치고 검수 체크리스트 4개를 확인한 뒤 **[승인]**.
4. **[테스트 게시]**로 확인 → **[Instagram 에 지금 게시]** 또는 예약 시각 저장.

처음 한 번만: `.env` 에 `META_ACCESS_TOKEN`(Graph API 탐색기 토큰), `META_APP_ID`, `META_APP_SECRET` 를 넣고
대시보드 위쪽 [토큰 설정 실행]을 누르면 만료 없는 게시용 토큰으로 바뀌어 저장됩니다.

진행 상태는 [PROJECT_STATE.md](PROJECT_STATE.md) 에 있습니다. 아래는 Codex 시절 인수인계 원문입니다.

---

> **Codex handoff | 2026-10-19 | project state: design + Meta app created; API verification pending**

이 저장소는 기존 ChatGPT 대화에서 논의한 **애니멀픽** 인스타그램 매거진 자동화 프로젝트를 Codex에서 이어가기 위한 인수인계 패키지입니다.

## 먼저 읽을 순서
1. `AGENTS.md` — Codex에 적용할 지속적 작업 지침
2. `docs/PROJECT_CONTEXT.md` — 사용자 의도, 레퍼런스, 합의된 방향
3. `docs/STATE_AND_OPEN_QUESTIONS.md` — 실제 완료/미확인/미구현 상태
4. `docs/ROADMAP.md` — 단계별 개발 목표와 완료 기준
5. `docs/META_API_SETUP.md` — 첫 실제 API 테스트 및 보안 가이드
6. `CODEX_FIRST_PROMPT.md` — Codex 첫 메시지로 사용할 프롬프트

## 핵심 목표
- 공개적으로 접근 가능한 레퍼런스 매거진 계정의 게시물/공개 지표를 수집하고, **왜 반응이 좋았는지 가설을 분석**한다.
- 동시에 국내외에서 새로운 흥미로운 소재를 찾고 사실/권리 검증 후 독창적인 매거진형 캐러셀로 제작한다.
- 이후 **하루 10개** 콘텐츠 발행을 목표로 예약 게시와 성과 피드백 루프를 자동화한다.
- 보너스 참여/초대는 보장되지 않으며, 계정 자격/정책을 지속 확인한다.

## 현재까지 확인된 상태
- Instagram **프로페셔널 계정 전환 완료** — 사용자 확인.
- Facebook **페이지 생성 완료** — 사용자 확인.
- Meta **개발자 앱 생성 완료** — 사용자 확인.
- Facebook 페이지와 Instagram 실제 연결 여부 **미확인**.
- 권한 승인, 토큰 발급, `/me/accounts` 호출, Business Discovery **아직 검증되지 않음**.
- 별도의 Instagram 자동 게시 프로그램 **아직 구현/배포되지 않음**.

## 준비된 파일
- `data/reference_accounts.csv`: 제공된 계정 13개 목록. 팔로잉 수는 14로 보였으나 스크린샷은 13개 → 1개 미확인.
- `assets/reference_screenshots/`: 사용자가 제공한 계정 및 팔로잉 스크린샷(참고용).
- `scripts/meta_probe.py`: **읽기 전용** Graph API `me/accounts` 및 `business_discovery` 시험용 Python CLI.
- `.env.example`: 로컬 비밀 정보 설정 양식. 토큰이 포함되어 있지 않음.
- `tests/`: 네트워크 없는 기본 단위 테스트.

## 로컬 빠른 시작 (Python 3.10+)

```powershell
# ZIP 압축 해제 후 프로젝트 루트에서 (Windows PowerShell)
Copy-Item .env.example .env
# .env를 열어 META_ACCESS_TOKEN 등을 직접 입력 (절대로 채팅/저장소에 올리지 마세요)
python -m unittest discover -s tests -v
python scripts/meta_probe.py pages
# me/accounts 결과로 내 Instagram ID를 확인한 다음 .env에 META_IG_USER_ID 설정
python scripts/meta_probe.py discovery --handle heodukgoo --limit 10
```

현재는 Meta 인증 설정이 안 됐으므로 API 실행 시 오류가 나오는 것이 정상입니다. 토큰은 직접 발급받아 로컬 `.env`에만 저장합니다.

## Codex로 가져오기
- **Codex 데스크톱/CLI**: 압축을 풀고 이 폴더를 프로젝트로 열어 `CODEX_FIRST_PROMPT.md`의 메시지를 전송합니다.
- **Codex Cloud**: 이 폴더를 개인 Git 저장소에 커밋/푸시(비밀파일 제외)하여 저장소를 연결한 뒤 같은 메시지로 시작합니다.
- Codex는 기존 ChatGPT 대화가 **자동으로 이전되는 것처럼 가정하지 말고**, 이 저장소 문서를 정본으로 읽어야 합니다.

## 유의사항
Meta API 버전·권한·보너스 정책은 변경될 수 있습니다. 실제 개발에 앞서 공식 문서와 실측 API 응답을 우선해 확인하세요. 특히 타인 콘텐츠의 공유·저장·도달 인사이트는 일반적으로 얻을 수 없으며, 접근 불가능한 지표는 추정치라고 명시하세요.
