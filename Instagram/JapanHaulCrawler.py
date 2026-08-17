"""
일본여행 필수템(하울) 인스타그램 수집기.

2026년 현재 인스타그램은 비로그인 상태에서 게시물 데이터를 전혀 내주지 않습니다.
(`?__a=1` -> 500, `/api/v1/tags/web_info/` -> 302, `/api/v1/users/web_profile_info/` -> 401)
따라서 이 크롤러는 "동작이 확인된 두 가지 경로"만 백엔드로 제공합니다.

  1. apify      : Apify 관리형 액터 호출. APIFY_TOKEN 필요. 계정 밴 리스크 없음. (권장)
  2. instagrapi : 로그인 세션 기반 비공식 API. IG 계정 필요. 밴 리스크 있음.

수집 축은 두 가지이며, 둘을 섞어 쓰는 것을 권장합니다.

  - hashtag 모드 : #일본여행필수템 처럼 태그 피드를 그대로 훑습니다.
                   태그를 성실히 다는 계정만 잡히지만 정확도가 높습니다.
  - keyword 모드 : "돈키호테 쇼핑리스트" 같은 자연어 검색어로 찾습니다.
                   해시태그를 안 달았거나 캡션에만 언급한 릴스까지 잡힙니다.

사용 예:
    export APIFY_TOKEN="apify_api_..."
    python JapanHaulCrawler.py --backend apify --mode both --limit 80 --type reels
    python JapanHaulCrawler.py --backend apify --mode keyword --queries "돈키호테 쇼핑리스트" "일본 약국 추천템"
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
        mode="both",
        limit_per_query=80,
        results_type="reels",
        output_dir="results/japan_haul",
        sleep_sec=2.0,
        keyword_file=KEYWORD_FILE,
    ):
        self.backend = backend
        self.mode = mode
        self.limit_per_query = limit_per_query
        self.results_type = results_type
        self.sleep_sec = sleep_sec
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        with open(keyword_file, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self.hashtags = self.config.get("seed_hashtags", [])
        self.keywords = self.config.get("seed_keywords", [])

        # {정규화 별칭: (아이템명, 카테고리, 규제등급)}
        self.alias_map = {}
        for category, items in self.config.get("categories", {}).items():
            for item in items:
                for alias in item.get("aliases", []):
                    self.alias_map[normalize(alias)] = (
                        item["name"],
                        category,
                        item.get("regulation", "unknown"),
                    )

    # ------------------------------------------------------------------ #
    # 쿼리 구성
    # ------------------------------------------------------------------ #
    def build_queries(self):
        """(mode, query) 튜플 리스트를 만듭니다."""
        queries = []
        if self.mode in ("hashtag", "both"):
            queries += [("hashtag", t.lstrip("#")) for t in self.hashtags]
        if self.mode in ("keyword", "both"):
            queries += [("keyword", k) for k in self.keywords]
        if not queries:
            raise ValueError(f"mode={self.mode} 에 해당하는 쿼리가 사전에 없습니다.")
        return queries

    # ------------------------------------------------------------------ #
    # 수집 백엔드
    # ------------------------------------------------------------------ #
    def collect(self, queries=None):
        queries = queries or self.build_queries()
        print(f"[INFO] backend={self.backend} mode={self.mode} 쿼리 {len(queries)}개 x {self.limit_per_query}건")

        if self.backend == "apify":
            raw = self._collect_apify(queries)
        elif self.backend == "instagrapi":
            raw = self._collect_instagrapi(queries)
        else:
            raise ValueError(f"알 수 없는 backend: {self.backend}")

        with open(self.output_dir / "raw_posts.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2, default=str)

        posts = self._dedupe([p for p in (self._normalize_post(r) for r in raw) if p])
        print(f"[INFO] 정규화/중복제거 후 {len(posts)}건")
        return posts

    def _collect_apify(self, queries):
        token = os.environ.get("APIFY_TOKEN")
        if not token:
            raise RuntimeError("APIFY_TOKEN 환경변수가 필요합니다. https://console.apify.com/settings/integrations")

        results = []
        # 쿼리를 한 번에 묶어 보내면 결과에 출처가 안 남습니다.
        # "어떤 검색어가 잘 먹혔는지"를 봐야 하므로 쿼리별로 호출합니다. (건당 비용은 동일)
        for i, (qmode, query) in enumerate(queries, 1):
            payload = {
                "hashtags": [query],
                "keywordSearch": qmode == "keyword",
                "resultsType": "posts" if self.results_type == "posts" else "reels",
                "resultsLimit": self.limit_per_query,
            }
            try:
                resp = requests.post(APIFY_ENDPOINT, params={"token": token}, json=payload, timeout=900)
                if resp.status_code >= 400:
                    print(f"[FAIL {i}/{len(queries)}] {qmode}:{query} ({resp.status_code}) {resp.text[:200]}")
                    continue
                items = resp.json()
                for item in items:
                    results.append({"_mode": qmode, "_query": query, "_item": item})
                print(f"[OK {i}/{len(queries)}] {qmode}:{query} -> {len(items)}건")
            except requests.exceptions.RequestException as e:
                print(f"[FAIL {i}/{len(queries)}] {qmode}:{query} 요청 실패: {e}")
        return results

    def _collect_instagrapi(self, queries):
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

        # 비공식 API에는 게시물 전문 검색이 없습니다.
        # 그래서 keyword 모드는 "검색어 -> 연관 해시태그 확장 -> 태그 수집"으로 처리합니다.
        expanded = []
        for qmode, query in queries:
            if qmode == "hashtag":
                expanded.append((query, query))
                continue
            try:
                found = cl.search_hashtags(query)[:5]
                for h in found:
                    expanded.append((query, h.name))
                print(f"[EXPAND] '{query}' -> {[h.name for h in found]}")
            except Exception as e:
                print(f"[EXPAND FAIL] '{query}': {e}")
            time.sleep(random.uniform(self.sleep_sec, self.sleep_sec * 2))

        results, seen_tags = [], set()
        for query, tag in expanded:
            if tag in seen_tags:
                continue
            seen_tags.add(tag)
            try:
                medias = cl.hashtag_medias_top(tag, amount=self.limit_per_query)
                for m in medias:
                    results.append(
                        {
                            "_mode": "hashtag" if query == tag else "keyword",
                            "_query": query,
                            "_resolved_tag": tag,
                            "_media": m.dict(),
                        }
                    )
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
        base = {
            "source_mode": raw.get("_mode"),
            "source_query": raw.get("_query"),
            "resolved_tag": raw.get("_resolved_tag") or raw.get("_query"),
        }

        if "_media" in raw:  # instagrapi
            m = raw["_media"]
            code = m.get("code")
            if not code:
                return None
            is_reel = m.get("product_type") == "clips" or m.get("media_type") == 2
            return {
                **base,
                "shortcode": code,
                "url": f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{code}/",
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
        item = raw.get("_item", raw)
        code = item.get("shortCode") or item.get("shortcode")
        if not code:
            return None
        is_reel = (item.get("productType") == "clips") or (item.get("type") == "Video")
        return {
            **base,
            "shortcode": code,
            "url": item.get("url") or f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{code}/",
            "post_type": "reel" if is_reel else "post",
            "username": item.get("ownerUsername"),
            "caption": item.get("caption") or "",
            "like_count": item.get("likesCount") or 0,
            "comment_count": item.get("commentsCount") or 0,
            "view_count": item.get("videoPlayCount") or item.get("igPlayCount") or item.get("videoViewCount") or 0,
            "taken_at": item.get("timestamp") or "",
            "thumbnail_url": item.get("displayUrl") or "",
            "video_url": item.get("videoUrl") or "",
        }

    @staticmethod
    def _dedupe(posts):
        """같은 게시물이 여러 쿼리에서 나오면 첫 출처만 남기되, 몇 개 쿼리에서 걸렸는지 세어둡니다."""
        by_code = {}
        for p in posts:
            code = p["shortcode"]
            if code in by_code:
                by_code[code]["hit_queries"].add(p["source_query"])
                continue
            p["hit_queries"] = {p["source_query"]}
            by_code[code] = p
        out = []
        for p in by_code.values():
            p["중복_쿼리수"] = len(p.pop("hit_queries"))
            out.append(p)
        return out

    # ------------------------------------------------------------------ #
    # 아이템 추출 / 랭킹
    # ------------------------------------------------------------------ #
    def match_items(self, caption):
        """캡션에서 사전에 등록된 아이템을 찾아 (아이템명, 카테고리, 규제등급) 리스트로 반환."""
        norm = normalize(caption)
        hits = {}
        for alias, (name, category, regulation) in self.alias_map.items():
            if alias and alias in norm:
                hits[name] = (category, regulation)
        return sorted((name, cat, reg) for name, (cat, reg) in hits.items())

    def build_item_ranking(self, posts):
        stats = {}
        for p in posts:
            engagement = (p["like_count"] or 0) + (p["comment_count"] or 0)
            for name, category, regulation in self.match_items(p["caption"]):
                s = stats.setdefault(
                    name,
                    {
                        "아이템": name,
                        "카테고리": category,
                        "규제등급": regulation,
                        "언급_게시물수": 0,
                        "총_좋아요+댓글": 0,
                        "총_조회수": 0,
                        "_queries": set(),
                        "_videos": [],
                    },
                )
                s["언급_게시물수"] += 1
                s["총_좋아요+댓글"] += engagement
                s["총_조회수"] += p["view_count"] or 0
                s["_queries"].add(f"{p['source_mode']}:{p['source_query']}")
                if p["post_type"] == "reel":
                    s["_videos"].append((p["view_count"] or 0, p["url"]))

        rows = []
        for s in stats.values():
            top = sorted(s.pop("_videos"), reverse=True)[:3]
            s["대표_영상"] = " | ".join(url for _, url in top)
            s["발견_쿼리수"] = len(s["_queries"])
            s["발견_쿼리"] = ", ".join(sorted(s.pop("_queries"))[:5])
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
            matched = self.match_items(p["caption"])
            items = [name for name, _, _ in matched]
            sellable = [name for name, _, reg in matched if reg in ("ok", "restricted")]
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
                    "판매가능_아이템": ", ".join(sellable),
                    "판매가능_개수": len(sellable),
                    "source_mode": p["source_mode"],
                    "source_query": p["source_query"],
                    "resolved_tag": p["resolved_tag"],
                    "중복_쿼리수": p["중복_쿼리수"],
                    "caption": (p["caption"] or "").replace("\n", " ")[:300],
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values(["view_count", "like_count"], ascending=False).reset_index(drop=True)

    def build_query_report(self, posts):
        """어떤 검색어/해시태그가 실제로 쓸모 있었는지. 다음 수집 때 시드를 다듬는 근거."""
        stats = {}
        for p in posts:
            key = (p["source_mode"], p["source_query"])
            s = stats.setdefault(
                key,
                {
                    "모드": p["source_mode"],
                    "쿼리": p["source_query"],
                    "수집_게시물수": 0,
                    "아이템_매칭_게시물수": 0,
                    "총_조회수": 0,
                    "_items": set(),
                },
            )
            s["수집_게시물수"] += 1
            s["총_조회수"] += p["view_count"] or 0
            items = self.match_items(p["caption"])
            if items:
                s["아이템_매칭_게시물수"] += 1
            s["_items"].update(name for name, _, _ in items)

        rows = []
        for s in stats.values():
            s["발굴_아이템수"] = len(s.pop("_items"))
            s["매칭률"] = (
                round(s["아이템_매칭_게시물수"] / s["수집_게시물수"] * 100, 1) if s["수집_게시물수"] else 0.0
            )
            rows.append(s)

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values(["발굴_아이템수", "수집_게시물수"], ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    def run(self):
        posts = self.collect()
        if not posts:
            print("[WARN] 수집된 게시물이 없습니다.")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        outputs = {
            "items": (self.build_item_ranking(posts), "아이템 랭킹"),
            "videos": (self.build_video_list(posts), "영상 리스트"),
            "queries": (self.build_query_report(posts), "쿼리 성과"),
        }
        for name, (df, label) in outputs.items():
            path = self.output_dir / f"japan_haul_{name}_{stamp}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            print(f"[저장] {label}: {path} ({len(df)}행)")

        items_df = outputs["items"][0]
        if not items_df.empty:
            print("\n=== 필수템 TOP 15 ===")
            cols = ["아이템", "카테고리", "규제등급", "언급_게시물수", "총_조회수", "발견_쿼리수"]
            print(items_df.head(15)[cols].to_string(index=False))

        query_df = outputs["queries"][0]
        if not query_df.empty:
            print("\n=== 쿼리 성과 TOP 10 (다음 수집 시드 조정용) ===")
            cols = ["모드", "쿼리", "수집_게시물수", "발굴_아이템수", "매칭률"]
            print(query_df.head(10)[cols].to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="일본여행 필수템 인스타그램 수집기")
    parser.add_argument("--backend", choices=["apify", "instagrapi"], default="apify")
    parser.add_argument(
        "--mode",
        choices=["hashtag", "keyword", "both"],
        default="both",
        help="hashtag=태그 피드, keyword=자연어 검색, both=둘 다(권장)",
    )
    parser.add_argument("--limit", type=int, default=80, help="쿼리당 수집 건수")
    parser.add_argument("--type", choices=["reels", "posts"], default="reels")
    parser.add_argument("--tags", nargs="*", help="해시태그 직접 지정 (사전의 seed_hashtags 대체)")
    parser.add_argument("--queries", nargs="*", help="검색어 직접 지정 (사전의 seed_keywords 대체)")
    parser.add_argument("--output", default="results/japan_haul")
    args = parser.parse_args()

    crawler = JapanHaulCrawler(
        backend=args.backend,
        mode=args.mode,
        limit_per_query=args.limit,
        results_type=args.type,
        output_dir=args.output,
    )
    if args.tags:
        crawler.hashtags = args.tags
    if args.queries:
        crawler.keywords = args.queries
    crawler.run()


if __name__ == "__main__":
    main()
