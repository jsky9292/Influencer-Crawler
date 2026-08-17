"""
크롤링 결과 -> 섭외(DM) 우선순위 리스트 + 개인화 DM 초안 생성기.

JapanHaulCrawler가 뽑은 japan_haul_videos_*.csv를 읽어서 계정 단위로 집계하고,
섭외 점수로 정렬한 뒤 계정마다 개인화된 DM 초안을 만들어 CSV로 떨궈줍니다.

  일본어 캡션 비중이 높은 계정 -> 현지 소싱/협업 트랙 (일본어 DM)
  한국어 캡션 계정            -> 제휴/커미션 트랙 (한국어 DM)

※ 이 도구는 발송하지 않습니다. 초안만 만듭니다.
   인스타그램은 자동 대량 DM을 약관으로 금지하며, 실제로 몇 시간 안에
   계정이 제한됩니다. 생성된 CSV를 보고 사람이 직접 보내세요.
   현실적인 상한은 신규 계정 하루 10~20건, 워밍업된 계정 30~50건입니다.

사용 예:
    python OutreachBuilder.py --videos results/japan_haul/japan_haul_videos_20260817_1530.csv
    python OutreachBuilder.py --videos ... --track jp --top 50
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import json
import re

import pandas as pd

KEYWORD_FILE = Path(__file__).parent / "japan_haul_keywords.json"

# 히라가나 / 가타카나. 한자는 한국어 캡션에도 섞이므로 언어 판정에서 제외합니다.
KANA_RE = re.compile(r"[぀-ゟ゠-ヿ]")
HANGUL_RE = re.compile(r"[가-힣]")
# 일본어 표기 후보를 고를 때는 한자도 포함해야 합니다 (예: 休足時間, 白い恋人).
JP_RE = re.compile(r"[぀-ゟ゠-ヿ㐀-䶵一-鿿]")

TIERS = [
    (300_000, "메가"),
    (50_000, "미들"),
    (5_000, "마이크로"),
    (0, "나노"),
]


def detect_lang(text: str) -> str:
    """캡션의 가나/한글 비중으로 계정 언어를 추정합니다."""
    kana = len(KANA_RE.findall(text or ""))
    hangul = len(HANGUL_RE.findall(text or ""))
    if kana == 0 and hangul == 0:
        return "en"
    return "ja" if kana > hangul else "ko"


def tier_of(avg_views: float) -> str:
    for threshold, label in TIERS:
        if avg_views >= threshold:
            return label
    return "나노"


class OutreachBuilder:
    def __init__(self, videos_csv, keyword_file=KEYWORD_FILE, brand="(브랜드명)", site="(구매대행 사이트 URL)"):
        self.df = pd.read_csv(videos_csv)
        self.brand = brand
        self.site = site
        with open(keyword_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.regulation = {}
        # 일본어 DM에 한글 제품명을 넣으면 안 읽힙니다. 별칭에서 일본어 표기를 뽑아둡니다.
        self.jp_name = {}
        for items in config.get("categories", {}).values():
            for item in items:
                name = item["name"]
                self.regulation[name] = item.get("regulation", "unknown")
                jp = next((a for a in item.get("aliases", []) if JP_RE.search(a)), None)
                self.jp_name[name] = jp or name

    # ------------------------------------------------------------------ #
    @staticmethod
    def _split_items(cell):
        if not isinstance(cell, str) or not cell.strip():
            return []
        return [x.strip() for x in cell.split(",") if x.strip()]

    def aggregate(self):
        """계정 단위 집계."""
        df = self.df[self.df["username"].notna()].copy()
        if df.empty:
            return pd.DataFrame()

        rows = []
        for username, g in df.groupby("username"):
            captions = " ".join(str(c) for c in g["caption"].fillna(""))
            items, sellable = set(), set()
            for cell in g.get("매칭_아이템", pd.Series(dtype=str)).fillna(""):
                items.update(self._split_items(cell))
            for cell in g.get("판매가능_아이템", pd.Series(dtype=str)).fillna(""):
                sellable.update(self._split_items(cell))

            views = g["view_count"].fillna(0)
            likes = g["like_count"].fillna(0)
            comments = g["comment_count"].fillna(0)
            avg_views = float(views.mean())
            total_engagement = float((likes + comments).sum())
            total_views = float(views.sum())

            best = g.sort_values("view_count", ascending=False).iloc[0]
            matched_posts = int((g.get("아이템_개수", pd.Series([0] * len(g))).fillna(0) > 0).sum())

            rows.append(
                {
                    "username": username,
                    "프로필": f"https://www.instagram.com/{username}/",
                    "추정언어": detect_lang(captions),
                    "수집_게시물수": len(g),
                    "아이템_언급_게시물수": matched_posts,
                    "평균_조회수": round(avg_views),
                    "총_조회수": round(total_views),
                    "평균_좋아요": round(float(likes.mean())),
                    "평균_댓글": round(float(comments.mean())),
                    # 조회수 대비 참여율. 팔로워 수를 못 받으므로 이걸 대용치로 씁니다.
                    "참여율_%": round(total_engagement / total_views * 100, 2) if total_views else 0.0,
                    "티어": tier_of(avg_views),
                    "다루는_아이템": ", ".join(sorted(items)),
                    "판매가능_아이템": ", ".join(sorted(sellable)),
                    "판매가능_아이템수": len(sellable),
                    "대표_영상": best["url"],
                    "대표_영상_조회수": int(best["view_count"] or 0),
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    def score(self, agg):
        """섭외 점수 0~100. 순위(percentile) 기반이라 단위가 달라도 섞어 쓸 수 있습니다."""
        if agg.empty:
            return agg

        def pct(series):
            # 값이 전부 같으면 rank(pct=True)가 1.0으로 몰리므로 중립값 0.5로 둡니다.
            return series.rank(pct=True) if series.nunique() > 1 else pd.Series(0.5, index=series.index)

        relevance = agg["아이템_언급_게시물수"] / agg["수집_게시물수"].replace(0, 1)

        agg["섭외점수"] = (
            0.35 * pct(agg["평균_조회수"])
            + 0.30 * pct(agg["참여율_%"])
            + 0.20 * relevance
            + 0.15 * pct(agg["판매가능_아이템수"])
        ) * 100
        agg["섭외점수"] = agg["섭외점수"].round(1)
        return agg.sort_values("섭외점수", ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    def draft_dm(self, row):
        """계정 데이터로 채운 DM 초안. 그대로 보내지 말고 한 줄은 직접 고쳐 쓰세요."""
        items = row["판매가능_아이템"] or row["다루는_아이템"] or "일본 쇼핑 아이템"
        first_item = items.split(",")[0].strip()

        if row["추정언어"] == "ja":
            # 현지 소싱/협업 트랙 — 제품명을 일본어 표기로 바꿔서 넣습니다.
            jp_item = self.jp_name.get(first_item, first_item)
            return (
                f"はじめまして、韓国で日本商品の購買代行サイトを運営している{self.brand}と申します。\n"
                f"「{jp_item}」を紹介されていたリール（{row['대표_영상']}）を拝見してご連絡しました。\n"
                f"韓国では{jp_item}のような日本の生活雑貨の需要が非常に高く、"
                f"現地での商品ソーシングとコンテンツ制作でご一緒できないかと考えています。\n"
                f"・現地買い付けのサポート\n"
                f"・成果報酬型のアフィリエイト（売上の◯%）\n"
                f"ご興味があればお返事いただけると嬉しいです。よろしくお願いいたします。"
            )

        # 제휴/커미션 트랙 (한국 계정)
        return (
            f"안녕하세요 {row['username']}님, 일본 상품 구매대행 사이트를 운영하는 {self.brand}입니다.\n"
            f"'{first_item}' 소개해주신 릴스({row['대표_영상']}) 잘 봤습니다. "
            f"조회수 {row['대표_영상_조회수']:,}회 나온 거 보고 연락드려요.\n"
            f"저희가 현지에서 직접 소싱해서 {self.site} 에서 판매 중인데, "
            f"{row['username']}님 콘텐츠와 결이 맞을 것 같습니다.\n"
            f"・제휴 링크 커미션 (판매액의 ◯%)\n"
            f"・제품 무상 제공 후 콘텐츠 제작\n"
            f"둘 중 편한 방식으로 진행 가능합니다. 관심 있으시면 회신 부탁드려요!"
        )

    # ------------------------------------------------------------------ #
    def run(self, track="all", top=None, output_dir="results/japan_haul"):
        agg = self.score(self.aggregate())
        if agg.empty:
            print("[WARN] 집계할 계정이 없습니다.")
            return

        if track == "jp":
            agg = agg[agg["추정언어"] == "ja"]
        elif track == "kr":
            agg = agg[agg["추정언어"] == "ko"]
        if agg.empty:
            print(f"[WARN] track={track} 에 해당하는 계정이 없습니다.")
            return

        agg = agg.reset_index(drop=True)
        if top:
            agg = agg.head(top)

        agg["트랙"] = agg["추정언어"].map({"ja": "현지소싱/협업", "ko": "제휴/커미션"}).fillna("검토필요")
        agg["DM_초안"] = agg.apply(self.draft_dm, axis=1)
        # 발송 대장으로 그대로 쓰도록 상태 컬럼을 비워서 붙입니다.
        agg["발송일"] = ""
        agg["상태"] = "미발송"
        agg["메모"] = ""

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        path = out_dir / f"outreach_list_{track}_{stamp}.csv"
        agg.to_csv(path, index=False, encoding="utf-8-sig")

        print(f"[저장] 섭외 리스트: {path} ({len(agg)}계정)")
        print(f"\n=== 섭외 우선순위 TOP {min(15, len(agg))} ===")
        cols = ["username", "트랙", "티어", "평균_조회수", "참여율_%", "판매가능_아이템수", "섭외점수"]
        print(agg.head(15)[cols].to_string(index=False))
        print(
            f"\n[주의] 자동 발송 기능은 없습니다. 하루 10~20건씩 직접 보내세요.\n"
            f"       대량 자동 DM은 인스타 약관 위반이며 계정이 제한됩니다."
        )


def main():
    parser = argparse.ArgumentParser(description="섭외 우선순위 리스트 + DM 초안 생성기")
    parser.add_argument("--videos", required=True, help="JapanHaulCrawler가 만든 japan_haul_videos_*.csv 경로")
    parser.add_argument("--track", choices=["all", "jp", "kr"], default="all", help="jp=일본 현지, kr=한국 계정")
    parser.add_argument("--top", type=int, help="상위 N개만 출력")
    parser.add_argument("--brand", default="(브랜드명)")
    parser.add_argument("--site", default="(구매대행 사이트 URL)")
    parser.add_argument("--output", default="results/japan_haul")
    args = parser.parse_args()

    OutreachBuilder(args.videos, brand=args.brand, site=args.site).run(
        track=args.track, top=args.top, output_dir=args.output
    )


if __name__ == "__main__":
    main()
