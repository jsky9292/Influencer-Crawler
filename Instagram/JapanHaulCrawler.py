"""
일본여행 필수템(하울) 인스타그램 수집기.

2026년 현재 인스타그램은 비로그인 상태에서 게시물 데이터를 전혀 내주지 않습니다.
(`?__a=1` -> 500, `/api/v1/tags/web_info/` -> 302, `/api/v1/users/web_profile_info/` -> 401)
따라서 이 크롤러는 "동작이 확인된 두 가지 경로"만 백엔드로 제공합니다.

  1. apify      : Apify 관리형 액터 호출. APIFY_TOKEN 필요. 계정 밴 리스크 없음. (권장)
  2. instagrapi : 로그인 세션 기반 비공식 API. IG 계정 필요. 밴 리스크 있음.

사용 예:
    export APIFY_TOKEN="apify_api_..."
    python JapanHaulCrawler.py --backend apify --limit 80 --type reels

    export IG_USERNAME="..." IG_PASSWORD="..."
    python JapanHaulCrawler.py --backend instagrapi --limit 40
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import json
import os
import random
import re
import time

import pandas as pd
import requests

KEYWORD_FILE = Path(__file__).parent / "japan_haul_keywords.json"
APIFY_ACTOR = "apify~instagram-hashtag-scraper"
APIFY_ENDPOINT = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"


def normalize(text: str) -> str:
    """캡션 매칭용 정규화: 소문자 + 공백/구두점 제거."""
    if not text:
        return ""
    return re.sub(r"[\s\-_·・.,!?~()\[\]#]+", "", text.lower())


class JapanHaulCrawler:
    def __init__(
        self,
        backend="apify",
        limit_per_tag=80,
        results_type="reels",
        output_dir="results/japan_haul",
        sleep_sec=2.0,
        keyword_file=KEYWORD_FILE,
    ):
        self.backend = backend
        self.limit_per_tag = limit_per_tag
        self.results_type = results_type
        self.sleep_sec = sleep_sec
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        with open(keyword_file, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self.hashtags = self.config.get("seed_hashtags", [])
        # {정규화 별칭: (아이템명, 카테고리)}
        self.alias_map = {}
        for category, items in self.config.get("categories", {}).items():
            for item in items:
                for alias in item.get("aliases", []):
                    self.alias_map[normalize(alias)] = (item["name"], category)

    # ------------------------------------------------------------------ #
    # 수집 백엔드
    # ------------------------------------------------------------------ #
    def collect(self, hashtags=None):
        hashtags = hashtags or self.hashtags
        if self.backend == "apify":
            raw = self._collect_apify(hashtags)
        elif self.backend == "instagrapi":
            raw = self._collect_instagrapi(hashtags)
        else:
            raise ValueError(f"알 수 없는 backend: {self.backend}")

        with open(self.output_dir / "raw_posts.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2, default=str)

        posts = self._dedupe([p for p in (self._normalize_post(r) for r in raw) if p])
        print(f"[INFO] 정규화/중복제거 후 {len(posts)}건")
        return posts

    def _collect_apify(self, hashtags):
        token = os.environ.get("APIFY_TOKEN")
        if not token:
            raise RuntimeError("APIFY_TOKEN 환경변수가 필요합니다. https://console.apify.com/settings/integrations")

        payload = {
            "hashtags": hashtags,
            "resultsType": "posts" if self.results_type == "posts" else "reels",
            "resultsLimit": self.limit_per_tag,
        }
        print(f"[INFO] Apify 액터 실행: {len(hashtags)}개 태그 x {self.limit_per_tag}건")
        resp = requests.post(
            APIFY_ENDPOINT,
            params={"token": token},
            json=payload,
            timeout=900,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Apify 호출 실패 ({resp.status_code}): {resp.text[:500]}")
        items = resp.json()
        print(f"[INFO] Apify 원본 {len(items)}건 수신")
        return items

    def _collect_instagrapi(self, hashtags):
        from instagrapi import Client

        username = os.environ.get("IG_USERNAME")
        password = os.environ.get("IG_PASSWORD")
        if not username or not password:
            raise RuntimeError("IG_USERNAME / IG_PASSWORD 환경변수가 필요합니다.")

        session_file = Path(__file__).parent / "ig_session.json"
        cl = Client()
        # 세션 재사용이 밴 리스크를 크게 낮춥니다. 매번 새로 로그인하지 마세요.
        if session_file.exists():
            cl.load_settings(session_file)
        cl.login(username, password)
        cl.dump_settings(session_file)

        results = []
        for tag in hashtags:
            try:
                medias = cl.hashtag_medias_top(tag, amount=self.limit_per_tag)
                for m in medias:
                    results.append({"_source_tag": tag, "_media": m.dict()})
                print(f"[OK] #{tag} -> {len(medias)}건")
            except Exception as e:
                print(f"[FAIL] #{tag}: {e}")
            # 태그 사이 딜레이 없이 돌리면 바로 차단됩니다.
            time.sleep(random.uniform(self.sleep_sec, self.sleep_sec * 3))
        return results

    # ------------------------------------------------------------------ #
    # 정규화
    # ------------------------------------------------------------------ #
    def _normalize_post(self, raw):
        """백엔드별 응답을 공통 스키마로 변환."""
        if "_media" in raw:  # instagrapi
            m = raw["_media"]
            code = m.get("code")
            is_reel = m.get("product_type") == "clips" or m.get("media_type") == 2
            return {
                "source_tag": raw.get("_source_tag"),
                "shortcode": code,
                "url": f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{code}/" if code else None,
                "post_type": "reel" if is_reel else "post",
                "username": (m.get("user") or {}).get("username"),
                "caption": m.get("caption_text") or "",
                "like_count": m.get("like_count") or 0,
                "comment_count": m.get("comment_count") or 0,
                "view_count": m.get("play_count") or m.get("view_count") or 0,
                "taken_at": str(m.get("taken_at") or ""),
                "thumbnail_url": str(m.get("thumbnail_url") or ""),
                "video_url": str(m.get("video_url") or ""),
            }

        # apify — 액터별로 키가 조금씩 달라 후보를 순서대로 확인합니다.
        code = raw.get("shortCode") or raw.get("shortcode")
        if not code:
            return None
        is_reel = (raw.get("productType") == "clips") or (raw.get("type") == "Video")
        return {
            "source_tag": raw.get("hashtag") or raw.get("searchTerm") or raw.get("inputUrl"),
            "shortcode": code,
            "url": raw.get("url") or f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{code}/",
            "post_type": "reel" if is_reel else "post",
            "username": raw.get("ownerUsername"),
            "caption": raw.get("caption") or "",
            "like_count": raw.get("likesCount") or 0,
            "comment_count": raw.get("commentsCount") or 0,
            "view_count": raw.get("videoPlayCount") or raw.get("igPlayCount") or raw.get("videoViewCount") or 0,
            "taken_at": raw.get("timestamp") or "",
            "thumbnail_url": raw.get("displayUrl") or "",
            "video_url": raw.get("videoUrl") or "",
        }

    @staticmethod
    def _dedupe(posts):
        seen, out = set(), []
        for p in posts:
            if p["shortcode"] in seen:
                continue
            seen.add(p["shortcode"])
            out.append(p)
        return out

    # ------------------------------------------------------------------ #
    # 아이템 추출 / 랭킹
    # ------------------------------------------------------------------ #
    def match_items(self, caption):
        """캡션에서 사전에 등록된 아이템을 찾아 (아이템명, 카테고리) 리스트로 반환."""
        norm = normalize(caption)
        hits = {}
        for alias, (name, category) in self.alias_map.items():
            if alias and alias in norm:
                hits[name] = category
        return sorted(hits.items())

    def build_item_ranking(self, posts):
        stats = {}
        for p in posts:
            engagement = (p["like_count"] or 0) + (p["comment_count"] or 0)
            for name, category in self.match_items(p["caption"]):
                s = stats.setdefault(
                    name,
                    {
                        "아이템": name,
                        "카테고리": category,
                        "언급_게시물수": 0,
                        "총_좋아요+댓글": 0,
                        "총_조회수": 0,
                        "대표_영상": [],
                    },
                )
                s["언급_게시물수"] += 1
                s["총_좋아요+댓글"] += engagement
                s["총_조회수"] += p["view_count"] or 0
                if p["post_type"] == "reel":
                    s["대표_영상"].append((p["view_count"] or 0, p["url"]))

        rows = []
        for s in stats.values():
            top = sorted(s["대표_영상"], reverse=True)[:3]
            s["대표_영상"] = " | ".join(url for _, url in top)
            rows.append(s)

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values(
            ["언급_게시물수", "총_조회수", "총_좋아요+댓글"], ascending=False
        ).reset_index(drop=True)

    def build_video_list(self, posts):
        rows = []
        for p in posts:
            items = [name for name, _ in self.match_items(p["caption"])]
            rows.append(
                {
                    "post_type": p["post_type"],
                    "url": p["url"],
                    "username": p["username"],
                    "view_count": p["view_count"],
                    "like_count": p["like_count"],
                    "comment_count": p["comment_count"],
                    "taken_at": p["taken_at"],
                    "매칭_아이템": ", ".join(items),
                    "아이템_개수": len(items),
                    "source_tag": p["source_tag"],
                    "caption": (p["caption"] or "").replace("\n", " ")[:300],
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values(["view_count", "like_count"], ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    def run(self):
        posts = self.collect()
        if not posts:
            print("[WARN] 수집된 게시물이 없습니다.")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        items_df = self.build_item_ranking(posts)
        videos_df = self.build_video_list(posts)

        items_path = self.output_dir / f"japan_haul_items_{stamp}.csv"
        videos_path = self.output_dir / f"japan_haul_videos_{stamp}.csv"
        items_df.to_csv(items_path, index=False, encoding="utf-8-sig")
        videos_df.to_csv(videos_path, index=False, encoding="utf-8-sig")

        print(f"\n[저장] 아이템 랭킹: {items_path} ({len(items_df)}종)")
        print(f"[저장] 영상 리스트: {videos_path} ({len(videos_df)}건)")
        if not items_df.empty:
            print("\n=== 필수템 TOP 15 ===")
            print(items_df.head(15).to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="일본여행 필수템 인스타그램 수집기")
    parser.add_argument("--backend", choices=["apify", "instagrapi"], default="apify")
    parser.add_argument("--limit", type=int, default=80, help="해시태그당 수집 건수")
    parser.add_argument("--type", choices=["reels", "posts"], default="reels")
    parser.add_argument("--tags", nargs="*", help="해시태그 직접 지정 (미지정 시 사전의 seed_hashtags 사용)")
    parser.add_argument("--output", default="results/japan_haul")
    args = parser.parse_args()

    crawler = JapanHaulCrawler(
        backend=args.backend,
        limit_per_tag=args.limit,
        results_type=args.type,
        output_dir=args.output,
    )
    if args.tags:
        crawler.hashtags = args.tags
    crawler.run()


if __name__ == "__main__":
    main()
