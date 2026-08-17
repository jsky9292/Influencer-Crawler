"""
크롤링 결과 -> 섭외(DM) 우선순위 리스트 + 개인화 DM 초안 생성기.

JapanHaulCrawler가 뽑은 japan_haul_videos_*.csv를 읽어서 계정 단위로 집계하고,
섭외 점수로 정렬한 뒤 계정마다 개인화된 DM 초안을 만들어 CSV로 떨궈줍니다.

대상은 전부 '일본 다녀온 한국 인플루언서'입니다. 팔로워가 한국인이라
그대로 구매대행 사이트의 고객이 됩니다. 제안은 제휴/커미션 하나입니다.

소싱은 인플루언서에게 하는 게 아닙니다. 도매처(NETSEA, SUPER DELIVERY,
제조사 대리점)에서 합니다. 일본어 계정은 팔로워가 일본인이라 우리 고객이
아니므로 기본적으로 걸러냅니다.

예외로 '일본 거주'가 확인되는 한국 계정은 소싱 파트너를 겸할 수 있어
거주추정 컬럼으로 따로 표시합니다. 단 지역명(도쿄/오사카)은 여행 게시물이면
전부 나오므로 판정 신호로 쓰지 않습니다.

※ 이 도구는 발송하지 않습니다. 초안만 만듭니다.
   인스타그램은 자동 대량 DM을 약관으로 금지하며, 실제로 몇 시간 안에
   계정이 제한됩니다. 생성된 CSV를 보고 사람이 직접 보내세요.
   현실적인 상한은 신규 계정 하루 10~20건, 워밍업된 계정 30~50건입니다.

사용 예:
    python OutreachBuilder.py --videos results/japan_haul/japan_haul_videos_20260817_1530.csv
    python OutreachBuilder.py --videos ... --only-resident --top 50
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

# '일본에 살고 있다'는 신호. 이런 계정은 소싱 파트너를 겸할 수 있습니다.
# 지역명(도쿄/오사카 등)은 여행 게시물이면 전부 나오므로 넣으면 안 됩니다.
RESIDENT_SIGNALS = [
    "일본살이", "일본생활", "일본거주", "재팬라이프", "일본에서살", "일본산다",
    "워홀", "워킹홀리데이", "유학중", "일본유학", "주재원",
    "배대지", "직구대행", "구매대행", "현지배송",
]


def _norm(text: str) -> str:
    """아이템 매칭용 정규화. JapanHaulCrawler.normalize와 같은 규칙입니다."""
    return re.sub(r"[\s\-_·・.,!?~()\[\]#]+", "", (text or "").lower())


def detect_lang(text: str) -> str:
    """캡션의 가나/한글 비중으로 계정 언어를 추정합니다."""
    kana = len(KANA_RE.findall(text or ""))
    hangul = len(HANGUL_RE.findall(text or ""))
    if kana == 0 and hangul == 0:
        return "en"
    return "ja" if kana > hangul else "ko"


def is_resident(text: str) -> bool:
    """일본 거주로 보이는 한국 계정인지. 소싱 파트너 겸업 제안을 붙일 때 씁니다."""
    return any(sig in (text or "") for sig in RESIDENT_SIGNALS)


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
        self.alias_map = {}
        for items in config.get("categories", {}).values():
            for item in items:
                if item.get("type") == "brand":
                    continue
                name = item["name"]
                self.regulation[name] = item.get("regulation", "unknown")
                jp = next((a for a in item.get("aliases", []) if JP_RE.search(a)), None)
                self.jp_name[name] = jp or name
                for alias in item.get("aliases", []):
                    self.alias_map[_norm(alias)] = name

        self._ensure_columns()

    def _ensure_columns(self):
        """
        PostResolver 출력처럼 아이템 매칭이 안 된 CSV도 그대로 받도록 보강합니다.
        (JapanHaulCrawler 출력에는 이미 있으므로 건드리지 않습니다.)
        """
        for col, default in (("view_count", 0), ("like_count", 0), ("comment_count", 0), ("caption", "")):
            if col not in self.df.columns:
                self.df[col] = default

        if "매칭_아이템" in self.df.columns:
            return
        matched, sellable = [], []
        for caption in self.df["caption"].fillna(""):
            norm = _norm(str(caption))
            hits = {name for alias, name in self.alias_map.items() if alias and alias in norm}
            matched.append(", ".join(sorted(hits)))
            sellable.append(", ".join(sorted(n for n in hits if self.regulation.get(n) != "blocked")))
        self.df["매칭_아이템"] = matched
        self.df["판매가능_아이템"] = sellable
        self.df["아이템_개수"] = [len(m.split(", ")) if m else 0 for m in matched]

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

            # 조회수가 없는 경로(PostResolver)에서는 좋아요로 대표 게시물을 고릅니다.
            sort_key = "view_count" if views.sum() else "like_count"
            best = g.sort_values(sort_key, ascending=False).iloc[0]
            matched_posts = int((g.get("아이템_개수", pd.Series([0] * len(g))).fillna(0) > 0).sum())

            rows.append(
                {
                    "username": username,
                    "프로필": f"https://www.instagram.com/{username}/",
                    "추정언어": detect_lang(captions),
                    "거주추정": "일본거주" if is_resident(captions) else "",
                    "수집_게시물수": len(g),
                    "아이템_언급_게시물수": matched_posts,
                    "평균_조회수": round(avg_views),
                    "총_조회수": round(total_views),
                    "평균_좋아요": round(float(likes.mean())),
                    "평균_댓글": round(float(comments.mean())),
                    # 조회수 대비 참여율. 팔로워 수를 못 받으므로 이걸 대용치로 씁니다.
                    # PostResolver 경로는 조회수가 없어 0이 되고, 아래에서 댓글/좋아요로 대체합니다.
                    "참여율_%": round(total_engagement / total_views * 100, 2) if total_views else 0.0,
                    "댓글_좋아요_비_%": round(float(comments.sum()) / float(likes.sum()) * 100, 2)
                    if likes.sum()
                    else 0.0,
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

        # 조회수가 하나도 없으면(PostResolver 경로) 좋아요를 도달력 지표로,
        # 댓글/좋아요 비율을 참여도 지표로 대체합니다.
        if agg["총_조회수"].sum() == 0:
            reach, engage = agg["평균_좋아요"], agg["댓글_좋아요_비_%"]
            print("[INFO] 조회수 데이터 없음 → 좋아요/댓글 기준으로 점수 계산")
        else:
            reach, engage = agg["평균_조회수"], agg["참여율_%"]

        base = (
            0.35 * pct(reach)
            + 0.30 * pct(engage)
            + 0.20 * relevance
            + 0.15 * pct(agg["판매가능_아이템수"])
        ) * 100

        # 일본 거주가 확인되면 소싱 파트너를 겸할 수 있어 소폭 가산.
        bonus = (agg["거주추정"] == "일본거주").astype(float) * 8.0

        agg["섭외점수"] = (base + bonus).clip(0, 100).round(1)
        return agg.sort_values("섭외점수", ascending=False).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    def draft_dm(self, row):
        """계정 데이터로 채운 DM 초안. 그대로 보내지 말고 한 줄은 직접 고쳐 쓰세요."""
        items = row["판매가능_아이템"] or row["다루는_아이템"] or "일본 쇼핑 아이템"
        first_item = items.split(",")[0].strip()

        # 조회수가 없는 경로(PostResolver)에서 "조회수 0회"라고 쓰면 안 됩니다.
        if row["대표_영상_조회수"]:
            reaction = f"조회수 {row['대표_영상_조회수']:,}회 나온 거 보고 연락드려요."
        elif row["평균_좋아요"]:
            reaction = f"좋아요 {int(row['평균_좋아요']):,}개 나온 거 보고 연락드려요."
        else:
            reaction = "반응이 좋아 보여서 연락드려요."

        dm = (
            f"안녕하세요 {row['username']}님, 일본 상품 구매대행 사이트를 운영하는 {self.brand}입니다.\n"
            f"'{first_item}' 소개해주신 릴스({row['대표_영상']}) 잘 봤습니다. {reaction}\n"
            f"팔로워분들이 '이거 어디서 사요?' 물어보실 것 같은데, "
            f"저희가 현지에서 직접 소싱해서 {self.site} 에서 판매 중입니다.\n"
            f"・제휴 링크 커미션 (판매액의 ◯%)\n"
            f"・제품 무상 제공 후 콘텐츠 제작\n"
            f"둘 중 편한 방식으로 진행 가능합니다. 관심 있으시면 회신 부탁드려요!"
        )

        # 일본 거주로 보이면 소싱 파트너 겸업 제안을 한 줄 덧붙입니다.
        if row["거주추정"] == "일본거주":
            dm += (
                f"\n(덧) 일본에 계신 것 같은데, 원하시면 현지 매입 파트너도 함께 가능합니다. "
                f"품목당 매입 대행 수수료가 커미션과 별도로 붙습니다."
            )
        return dm

    # ------------------------------------------------------------------ #
    def run(self, only_resident=False, top=None, output_dir="results/japan_haul"):
        agg = self.score(self.aggregate())
        if agg.empty:
            print("[WARN] 집계할 계정이 없습니다.")
            return

        # 일본어 계정은 팔로워가 일본인이라 구매대행 고객이 아닙니다. 항상 제외.
        dropped = int((agg["추정언어"] == "ja").sum())
        agg = agg[agg["추정언어"] != "ja"]
        if dropped:
            print(f"[INFO] 일본어 계정 {dropped}개 제외 (팔로워가 우리 고객이 아님)")

        if only_resident:
            agg = agg[agg["거주추정"] == "일본거주"]
        if agg.empty:
            print("[WARN] 조건에 해당하는 계정이 없습니다.")
            return

        agg = agg.reset_index(drop=True)
        if top:
            agg = agg.head(top)

        agg["DM_초안"] = agg.apply(self.draft_dm, axis=1)
        # 발송 대장으로 그대로 쓰도록 상태 컬럼을 비워서 붙입니다.
        agg["발송일"] = ""
        agg["상태"] = "미발송"
        agg["메모"] = ""

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        suffix = "resident" if only_resident else "all"
        path = out_dir / f"outreach_list_{suffix}_{stamp}.csv"
        agg.to_csv(path, index=False, encoding="utf-8-sig")

        print(f"[저장] 섭외 리스트: {path} ({len(agg)}계정)")
        print(f"\n=== 섭외 우선순위 TOP {min(15, len(agg))} ===")
        cols = ["username", "거주추정", "평균_좋아요", "평균_댓글", "댓글_좋아요_비_%", "판매가능_아이템수", "섭외점수"] \
            if agg["총_조회수"].sum() == 0 else \
            ["username", "거주추정", "티어", "평균_조회수", "참여율_%", "판매가능_아이템수", "섭외점수"]
        print(agg.head(15)[cols].to_string(index=False))
        print(
            f"\n[주의] 자동 발송 기능은 없습니다. 하루 10~20건씩 직접 보내세요.\n"
            f"       대량 자동 DM은 인스타 약관 위반이며 계정이 제한됩니다."
        )


def main():
    parser = argparse.ArgumentParser(description="섭외 우선순위 리스트 + DM 초안 생성기")
    parser.add_argument("--videos", required=True, help="JapanHaulCrawler가 만든 japan_haul_videos_*.csv 경로")
    parser.add_argument("--only-resident", action="store_true",
                        help="일본 거주로 보이는 계정만 (소싱 파트너 겸업 제안용)")
    parser.add_argument("--top", type=int, help="상위 N개만 출력")
    parser.add_argument("--brand", default="(브랜드명)")
    parser.add_argument("--site", default="(구매대행 사이트 URL)")
    parser.add_argument("--output", default="results/japan_haul")
    args = parser.parse_args()

    OutreachBuilder(args.videos, brand=args.brand, site=args.site).run(
        only_resident=args.only_resident, top=args.top, output_dir=args.output
    )


if __name__ == "__main__":
    main()
