"""
소싱 후보 리스트 생성기.

아이템 사전을 규제등급으로 필터링해 소싱 후보를 뽑습니다.
크롤링 결과(japan_haul_items_*.csv)를 같이 주면 수요 지표를 붙여 정렬합니다.

  --grade ok         : 일반 공산품만. 진입 장벽이 가장 낮아 초기 소싱용. (기본)
  --grade restricted : 화장품/식품 등 요건 있는 품목까지
  --grade all        : blocked 포함 전체 (판매 불가 품목 확인용)

매장/브랜드(무인양품, 다이소 등)는 상품이 아니므로 후보에서 제외합니다.
탐지 사전에는 남겨두되 type="brand"로 표시되어 있습니다.

사용 예:
    python SourcingList.py
    python SourcingList.py --items results/japan_haul/japan_haul_items_20260817.csv
    python SourcingList.py --grade restricted
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import json
import re

import pandas as pd

KEYWORD_FILE = Path(__file__).parent / "japan_haul_keywords.json"
# 가나 + 한자. 한글(U+AC00~)은 코드포인트가 커서 단순 ord 비교로는 걸러지지 않습니다.
JP_RE = re.compile(r"[぀-ゟ゠-ヿ㐀-䶵一-鿿]")
GRADE_FILTER = {
    "ok": {"ok"},
    "restricted": {"ok", "restricted"},
    "all": {"ok", "restricted", "blocked"},
}


def build(keyword_file=KEYWORD_FILE, grade="ok", items_csv=None):
    with open(keyword_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    allowed = GRADE_FILTER[grade]
    rows = []
    for category, items in config.get("categories", {}).items():
        for item in items:
            # 매장/브랜드는 소싱 "상품"이 아닙니다.
            if item.get("type") == "brand":
                continue
            if item.get("regulation") not in allowed:
                continue
            rows.append(
                {
                    "아이템": item["name"],
                    "카테고리": category,
                    "규제등급": item.get("regulation"),
                    "소싱채널": item.get("sourcing", "미조사"),
                    "일본어표기": next(
                        (a for a in item.get("aliases", []) if JP_RE.search(a)), ""
                    ),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 크롤링 결과가 있으면 수요 지표를 붙여 정렬합니다. 없으면 사전 순서 그대로.
    if items_csv:
        demand = pd.read_csv(items_csv)
        keep = [c for c in ["아이템", "언급_게시물수", "총_조회수", "총_좋아요+댓글", "대표_영상"] if c in demand.columns]
        df = df.merge(demand[keep], on="아이템", how="left")
        for col in ("언급_게시물수", "총_조회수", "총_좋아요+댓글"):
            if col in df.columns:
                df[col] = df[col].fillna(0).astype(int)
        df["수요확인"] = df["언급_게시물수"].map(lambda n: "확인됨" if n > 0 else "미확인")
        df = df.sort_values(["언급_게시물수", "총_조회수"], ascending=False)
    else:
        df["수요확인"] = "크롤링_전"

    return df.reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description="규제등급 기반 소싱 후보 리스트")
    parser.add_argument("--grade", choices=["ok", "restricted", "all"], default="ok")
    parser.add_argument("--items", help="japan_haul_items_*.csv 경로 (있으면 수요 지표 조인)")
    parser.add_argument("--output", default="results/japan_haul")
    args = parser.parse_args()

    df = build(grade=args.grade, items_csv=args.items)
    if df.empty:
        print(f"[WARN] grade={args.grade} 에 해당하는 아이템이 없습니다.")
        return

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = out_dir / f"sourcing_candidates_{args.grade}_{stamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")

    print(f"[저장] 소싱 후보: {path} ({len(df)}종)\n")
    cols = [c for c in ["아이템", "카테고리", "규제등급", "일본어표기", "소싱채널", "수요확인"] if c in df.columns]
    print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
