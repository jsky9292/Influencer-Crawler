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

## 3. 수집 축: 해시태그 + 검색어

두 축을 **같이** 돌리는 걸 권장합니다 (`--mode both`, 기본값).

| 모드 | 무엇을 훑나 | 강점 | 약점 |
|---|---|---|---|
| `hashtag` | `#일본여행필수템` 태그 피드 | 의도가 명확해 정확도 높음 | 태그를 성실히 다는 계정만 잡힘 |
| `keyword` | `"돈키호테 쇼핑리스트"` 자연어 검색 | **해시태그 없이 캡션에만 언급한 릴스**까지 잡힘 | 관련 없는 게시물 섞임 |
| `both` | 둘 다 | 커버리지 최대 | 쿼리 수만큼 비용 증가 |

기본 시드는 해시태그 12개 + 검색어 10개 = **쿼리 22개**입니다.

같은 게시물이 여러 쿼리에서 잡히면 중복 제거하되 `중복_쿼리수` 컬럼에 몇 개 쿼리에서
걸렸는지 기록합니다. 이 값이 높을수록 해당 주제의 핵심 게시물입니다.

`--mode` 를 바꾸거나 시드를 직접 넘길 수 있습니다:

```bash
# 검색어만
python JapanHaulCrawler.py --backend apify --mode keyword

# 검색어 직접 지정
python JapanHaulCrawler.py --backend apify --mode keyword \
  --queries "돈키호테 쇼핑리스트" "일본 약국 추천템"

# 해시태그 직접 지정
python JapanHaulCrawler.py --backend apify --mode hashtag \
  --tags 일본여행필수템 돈키호테필수템
```

### 백엔드별 keyword 모드 구현 차이

- **apify**: 액터의 `keywordSearch: true` 플래그로 자연어 검색을 그대로 수행합니다.
- **instagrapi**: 비공식 API에는 게시물 전문 검색이 없습니다. 대신
  `search_hashtags(검색어)` 로 **연관 해시태그 상위 5개를 뽑아 확장 수집**합니다.
  결과의 `resolved_tag` 컬럼에서 실제로 어떤 태그로 치환됐는지 확인할 수 있습니다.

## 4. 실행 방법

### 방식 A — Apify (권장)

1. https://console.apify.com 가입 → Settings → API tokens 에서 토큰 발급
   (무료 플랜 월 $5 크레딧 ≈ 약 2,000~3,000건 수집 가능)
2. 실행:

```bash
pip install -r ../requirements.txt
export APIFY_TOKEN="apify_api_xxxxxxxx"

# 해시태그 + 검색어 모두, 쿼리당 릴스 80건씩
python JapanHaulCrawler.py --backend apify --mode both --limit 80 --type reels
```

비용 감각: 액터 `apify/instagram-hashtag-scraper` 기준 유료 플랜 약 **$1.90 / 1,000건**,
무료 플랜 $2.60 / 1,000건.
기본 시드 22쿼리 × 80건 = 최대 약 1,760건 ≈ **$3.5~4.5**.
먼저 `--limit 20` (약 440건, $1 내외)으로 돌려 쿼리 성과를 본 뒤,
잘 먹히는 쿼리만 남기고 `--limit`을 올리는 순서를 권장합니다.

### 방식 B — instagrapi (계정 로그인)

```bash
export IG_USERNAME="..." IG_PASSWORD="..."
python JapanHaulCrawler.py --backend instagrapi --mode both --limit 40
```

주의사항:
- **본 계정 절대 사용 금지.** 밴/체크포인트가 실제로 발생합니다.
- 첫 로그인 후 `ig_session.json`이 생성되며, 이후 재사용합니다. 매번 새로 로그인하면 즉시 차단됩니다.
- 태그 사이 랜덤 딜레이가 들어가 있습니다. 줄이지 마세요.
- 한국 IP로 대량 요청 시 차단 확률이 높으므로 주거용(residential) 프록시를 권장합니다.

## 5. 산출물

`results/japan_haul/` 아래에 생성됩니다.

- `raw_posts.json` — 백엔드 원본 응답 (쿼리 출처 포함, 재분석용)
- `japan_haul_items_<날짜>.csv` — **필수템 랭킹**
  - 아이템 / 카테고리 / 언급_게시물수 / 총_좋아요+댓글 / 총_조회수 / 대표_영상(조회수 상위 3개 링크) / 발견_쿼리수 / 발견_쿼리
- `japan_haul_videos_<날짜>.csv` — **영상(릴스) 리스트**
  - post_type / url / username / view_count / like_count / comment_count / taken_at / 매칭_아이템 / 아이템_개수 / source_mode / source_query / resolved_tag / 중복_쿼리수 / caption
- `japan_haul_queries_<날짜>.csv` — **쿼리 성과표**
  - 모드 / 쿼리 / 수집_게시물수 / 아이템_매칭_게시물수 / 매칭률 / 발굴_아이템수 / 총_조회수

쿼리 성과표를 보고 시드를 다듬으세요. **매칭률이 낮은 쿼리는 돈만 쓰고 노이즈만 가져오므로
`seed_hashtags` / `seed_keywords`에서 빼면 됩니다.**

## 6. 아이템 사전 확장

`japan_haul_keywords.json`의 `categories`에 항목을 추가하면 바로 반영됩니다.

```json
{ "name": "표시할 이름", "aliases": ["한글표기", "일본어표기", "english"] }
```

매칭은 캡션을 소문자화 + 공백/구두점 제거 후 부분문자열로 수행합니다.
따라서 "카 베진", "#카베진", "카베진코와" 모두 `카베진`으로 잡힙니다.

수집 후 `japan_haul_videos_*.csv`에서 `매칭_아이템`이 비어 있는데 조회수가 높은 릴스를
훑어보면, 사전에 빠진 신상 아이템을 찾을 수 있습니다. 그것을 사전에 추가하고
`raw_posts.json`으로 재집계하는 방식으로 사전을 키우는 것을 권장합니다.

## 7. 시드 해시태그 & 검색어

`japan_haul_keywords.json` 참고.

**`seed_hashtags` (12개)**
```
일본여행필수템, 일본쇼핑리스트, 돈키호테쇼핑리스트, 돈키호테필수템,
일본드럭스토어, 일본여행쇼핑, 일본하울, 도쿄쇼핑,
오사카쇼핑, 후쿠오카쇼핑, 일본편의점추천, 일본약국추천
```

**`seed_keywords` (10개)**
```
일본여행 필수템, 돈키호테 쇼핑리스트, 일본 드럭스토어 추천템, 일본 하울,
일본 약국 추천템, 일본 편의점 추천, 일본 사올것, 일본 여행 살거리,
오사카 쇼핑 추천, 도쿄 쇼핑 추천
```
