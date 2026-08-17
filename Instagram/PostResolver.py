"""
게시물 URL -> 계정 정보 해석기. 토큰도 로그인도 필요 없습니다.

인스타그램은 로그인 없이 데이터를 안 주지만, 링크 미리보기용 og: 메타태그는
크롤러 User-Agent에 그대로 내줍니다. 여기에 계정명·좋아요·댓글·날짜·캡션이
들어 있어서 이것만으로 섭외 리스트를 만들 수 있습니다.

  og:description = "10K likes, 34 comments - nowistravel on November 12, 2024: "캡션...""
  og:title       = "표시이름 on Instagram: "캡션...""

한계:
  - 조회수는 안 나옵니다 (좋아요/댓글만). 릴스 순위는 좋아요 기준이 됩니다.
  - 검색 기능이 없습니다. URL을 어디선가 구해와야 합니다
    (구글 site:instagram.com 검색, 또는 Apify 백엔드로 수집한 URL).
  - 요청을 몰아치면 429가 납니다. 기본 딜레이를 줄이지 마세요.

사용 예:
    python PostResolver.py --urls urls.txt --out results/japan_haul/resolved.csv
    python PostResolver.py --url https://www.instagram.com/reel/C4pcbtAO29h/
"""

from __future__ import annotations

from pathlib import Path
import argparse
import html
import random
import re
import time

import pandas as pd
import requests

# 링크 미리보기 봇으로 접근해야 og 메타가 내려옵니다.
CRAWLER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

OG_DESC_RE = re.compile(r'<meta property="og:description" content="([^"]*)"')
OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')
# "10K likes, 34 comments - nowistravel on November 12, 2024:"
STATS_RE = re.compile(
    r"([\d.,]+[KM]?)\s+likes?,\s*([\d.,]+[KM]?)\s+comments?\s*-\s*(\S+)\s+on\s+([A-Za-z]+\s+\d+,\s+\d{4})"
)
SHORTCODE_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(p|reel|tv)/([A-Za-z0-9_-]+)")
# 프로필 페이지: "63K Followers, 1,874 Following, 693 Posts - See Instagram photos ..."
PROFILE_RE = re.compile(
    r"([\d.,]+[KM]?)\s+Followers?,\s*([\d.,]+[KM]?)\s+Following,\s*([\d.,]+[KM]?)\s+Posts?"
)


def parse_count(raw: str) -> int:
    """'10K' -> 10000, '1.2M' -> 1200000, '479' -> 479."""
    raw = raw.replace(",", "").strip()
    mult = 1
    if raw.endswith("K"):
        mult, raw = 1_000, raw[:-1]
    elif raw.endswith("M"):
        mult, raw = 1_000_000, raw[:-1]
    try:
        return int(float(raw) * mult)
    except ValueError:
        return 0


def resolve(url, session=None, timeout=20):
    """게시물 URL 하나를 해석합니다. 실패하면 None."""
    sess = session or requests
    try:
        resp = sess.get(url, headers={"User-Agent": CRAWLER_UA}, timeout=timeout)
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] {url}: {e}")
        return None
    if resp.status_code != 200:
        print(f"[FAIL] {url}: status {resp.status_code}")
        return None

    desc_m = OG_DESC_RE.search(resp.text)
    if not desc_m:
        print(f"[SKIP] {url}: og:description 없음 (삭제/비공개 가능성)")
        return None

    desc = html.unescape(desc_m.group(1))
    title = html.unescape(OG_TITLE_RE.search(resp.text).group(1)) if OG_TITLE_RE.search(resp.text) else ""

    stats = STATS_RE.search(desc)
    if not stats:
        print(f"[SKIP] {url}: 통계 파싱 실패")
        return None

    likes, comments, username, date = stats.groups()
    # og:description은 캡션이 잘려 나옵니다. 그래도 아이템 매칭에는 충분합니다.
    caption = desc[stats.end():].lstrip(': "').rstrip('"')
    sc = SHORTCODE_RE.search(url)
    kind = sc.group(1) if sc else "p"

    return {
        "shortcode": sc.group(2) if sc else "",
        "url": url,
        "post_type": "reel" if kind in ("reel", "tv") else "post",
        "username": username,
        "표시이름": title.split(" on Instagram")[0] if " on Instagram" in title else "",
        "caption": caption,
        "like_count": parse_count(likes),
        "comment_count": parse_count(comments),
        # og 메타에는 조회수가 없습니다. 다운스트림 스키마를 맞추려고 0으로 둡니다.
        "view_count": 0,
        "taken_at": date,
    }


def resolve_profile(username, session=None, timeout=20):
    """
    계정 프로필에서 팔로워 수를 가져옵니다.

    팔로워 수는 섭외 판단의 핵심인데 게시물 og 메타에는 없습니다.
    프로필 페이지 og:description에는 들어 있습니다.
    """
    sess = session or requests
    url = f"https://www.instagram.com/{username}/"
    try:
        resp = sess.get(url, headers={"User-Agent": CRAWLER_UA}, timeout=timeout, allow_redirects=False)
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] @{username}: {e}")
        return None
    if resp.status_code != 200:
        # 302는 대체로 레이트리밋이거나 계정 상태 변경입니다.
        print(f"[FAIL] @{username}: status {resp.status_code}")
        return None

    desc_m = OG_DESC_RE.search(resp.text)
    if not desc_m:
        print(f"[SKIP] @{username}: og:description 없음")
        return None
    desc = html.unescape(desc_m.group(1))
    stats = PROFILE_RE.search(desc)
    if not stats:
        print(f"[SKIP] @{username}: 팔로워 파싱 실패")
        return None

    followers, following, posts = stats.groups()
    title_m = OG_TITLE_RE.search(resp.text)
    display = html.unescape(title_m.group(1)).split(" (@")[0] if title_m else ""
    return {
        "username": username,
        "표시이름": display,
        "팔로워": parse_count(followers),
        "팔로잉": parse_count(following),
        "총_게시물수": parse_count(posts),
    }


def resolve_profiles(usernames, sleep_sec=3.0):
    sess = requests.Session()
    rows = []
    for i, name in enumerate(usernames, 1):
        row = resolve_profile(name, session=sess)
        if row:
            rows.append(row)
            print(f"[OK {i}/{len(usernames)}] @{name} 팔로워 {row['팔로워']:,}")
        if i < len(usernames):
            time.sleep(random.uniform(sleep_sec, sleep_sec * 2))
    return pd.DataFrame(rows)


def resolve_many(urls, sleep_sec=3.0):
    sess = requests.Session()
    rows = []
    for i, url in enumerate(urls, 1):
        row = resolve(url, session=sess)
        if row:
            rows.append(row)
            print(f"[OK {i}/{len(urls)}] @{row['username']} ♥{row['like_count']:,} 💬{row['comment_count']:,}")
        if i < len(urls):
            # 몰아치면 429가 납니다.
            time.sleep(random.uniform(sleep_sec, sleep_sec * 2))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="인스타 게시물 URL -> 계정/지표 해석 (무토큰)")
    parser.add_argument("--url", action="append", default=[], help="게시물 URL (여러 번 지정 가능)")
    parser.add_argument("--urls", help="URL이 줄단위로 든 텍스트 파일")
    parser.add_argument("--profiles", help="계정명이 줄단위로 든 파일. 팔로워 수를 가져옵니다")
    parser.add_argument("--sleep", type=float, default=3.0, help="요청 간 최소 딜레이(초)")
    parser.add_argument("--out", default="results/japan_haul/resolved_posts.csv")
    args = parser.parse_args()

    if args.profiles:
        names = [ln.strip().lstrip("@") for ln in Path(args.profiles).read_text().splitlines() if ln.strip()]
        df = resolve_profiles(list(dict.fromkeys(names)), sleep_sec=args.sleep)
        if df.empty:
            print("[WARN] 해석된 프로필이 없습니다.")
            return
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n[저장] {out} (계정 {len(df)}개)")
        return

    urls = list(args.url)
    if args.urls:
        urls += [ln.strip() for ln in Path(args.urls).read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    urls = list(dict.fromkeys(urls))
    if not urls:
        parser.error("--url 또는 --urls 중 하나는 필요합니다.")

    df = resolve_many(urls, sleep_sec=args.sleep)
    if df.empty:
        print("[WARN] 해석된 게시물이 없습니다.")
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n[저장] {out} ({len(df)}건 / 계정 {df['username'].nunique()}개)")


if __name__ == "__main__":
    main()
