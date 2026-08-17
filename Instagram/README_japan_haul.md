# 일본여행 필수템 인스타그램 수집 — 가능 여부 검증 & 실행 가이드

## 1. 결론 먼저

| 방식 | 2026년 8월 현재 | 비고 |
|---|---|---|
| 비로그인 HTML/JSON 크롤링 | ❌ **완전 차단** | 아래 실측 참고 |
| 공식 Graph API로 남의 계정/해시태그 수집 | ❌ 불가 | 내 소유 비즈니스 계정 데이터만 가능 |
| 로그인 세션 + instagrapi/instaloader | ⚠️ 가능하나 계정 밴 리스크 | 버려도 되는 계정 + 프록시 필수 |
| 관리형 스크래핑 API (Apify 등) | ✅ **가능, 이게 정답** | 유료지만 저렴하고 밴 리스크 없음 |

**"매번 실패했던" 이유는 코드 문제가 아니라 인스타가 해당 경로를 막았기 때문입니다.**

## 2. 실측 결과 (2026-08-17, 이 저장소 환경에서 직접 확인)

| 엔드포인트 | 응답 |
|---|---|
| `GET /explore/tags/일본여행/` | 200이지만 **JS 셸만 옴** (`shortcode`, `edge_hashtag_to_media` 등 데이터 0건) |
| `GET /api/v1/users/web_profile_info/?username=instagram` | **401** `{"require_login": true}` |
| `GET /api/v1/tags/web_info/?tag_name=...` | **302** (로그인 리다이렉트) |
| `GET /explore/tags/japantravel/?__a=1&__d=dis` | **500** (2022년 폐기된 엔드포인트) |

기존 `InstagramCrawler.py`의 `get_recent_posts_by_tag()`는 세 번째 항목인
`i.instagram.com/api/v1/tags/web_info/`를 사용합니다. 유효한 로그인 쿠키가 없으면
구조적으로 절대 동작하지 않습니다.

## 3. 실행 방법

### 방식 A — Apify (권장)

1. https://console.apify.com 가입 → Settings → API tokens 에서 토큰 발급
   (무료 플랜 월 $5 크레딧 ≈ 약 2,000~3,000건 수집 가능)
2. 실행:

```bash
pip install -r ../requirements.txt
export APIFY_TOKEN="apify_api_xxxxxxxx"

# 해시태그당 릴스 80건씩 수집
python JapanHaulCrawler.py --backend apify --limit 80 --type reels

# 태그를 직접 지정하고 싶을 때
python JapanHaulCrawler.py --backend apify --tags 일본여행필수템 돈키호테쇼핑리스트 --limit 100
```

비용 감각: 액터 `apify/instagram-hashtag-scraper` 기준 유료 플랜 약 **$1.90 / 1,000건**,
무료 플랜 $2.60 / 1,000건. 시드 태그 12개 × 80건 = 약 960건 ≈ **$2 내외**.

### 방식 B — instagrapi (계정 로그인)

```bash
export IG_USERNAME="..." IG_PASSWORD="..."
python JapanHaulCrawler.py --backend instagrapi --limit 40
```

주의사항:
- **본 계정 절대 사용 금지.** 밴/체크포인트가 실제로 발생합니다.
- 첫 로그인 후 `ig_session.json`이 생성되며, 이후 재사용합니다. 매번 새로 로그인하면 즉시 차단됩니다.
- 태그 사이 랜덤 딜레이가 들어가 있습니다. 줄이지 마세요.
- 한국 IP로 대량 요청 시 차단 확률이 높으므로 주거용(residential) 프록시를 권장합니다.

## 4. 산출물

`results/japan_haul/` 아래에 생성됩니다.

- `raw_posts.json` — 백엔드 원본 응답 (재분석용)
- `japan_haul_items_<날짜>.csv` — **필수템 랭킹**
  - 컬럼: 아이템 / 카테고리 / 언급_게시물수 / 총_좋아요+댓글 / 총_조회수 / 대표_영상(조회수 상위 3개 링크)
- `japan_haul_videos_<날짜>.csv` — **영상(릴스) 리스트**
  - 컬럼: post_type / url / username / view_count / like_count / comment_count / taken_at / 매칭_아이템 / source_tag / caption

## 5. 아이템 사전 확장

`japan_haul_keywords.json`의 `categories`에 항목을 추가하면 바로 반영됩니다.

```json
{ "name": "표시할 이름", "aliases": ["한글표기", "일본어표기", "english"] }
```

매칭은 캡션을 소문자화 + 공백/구두점 제거 후 부분문자열로 수행합니다.
따라서 "카 베진", "#카베진", "카베진코와" 모두 `카베진`으로 잡힙니다.

수집 후 `japan_haul_videos_*.csv`에서 `매칭_아이템`이 비어 있는데 조회수가 높은 릴스를
훑어보면, 사전에 빠진 신상 아이템을 찾을 수 있습니다. 그것을 사전에 추가하고
`raw_posts.json`으로 재집계하는 방식으로 사전을 키우는 것을 권장합니다.

## 6. 시드 해시태그

`japan_haul_keywords.json`의 `seed_hashtags` 참고. 기본값 12개:

```
일본여행필수템, 일본쇼핑리스트, 돈키호테쇼핑리스트, 돈키호테필수템,
일본드럭스토어, 일본여행쇼핑, 일본하울, 도쿄쇼핑,
오사카쇼핑, 후쿠오카쇼핑, 일본편의점추천, 일본약국추천
```
