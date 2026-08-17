

import instaloader
import csv
import os
import time
import random

def fetch_posts(username, max_count=20):
    L = instaloader.Instaloader()
    # 로그인 정보는 환경변수로 주입합니다. (소스에 하드코딩 금지)
    #   export IG_USERNAME="..." IG_PASSWORD="..."
    L.login(os.environ["IG_USERNAME"], os.environ["IG_PASSWORD"])
    profile = instaloader.Profile.from_username(L.context, username)

    results = []
    for i, post in enumerate(profile.get_posts()):
        # 난수 기반 딜레이(1.3초 ~ 6.0초, 각 게시물마다)
        time.sleep(random.uniform(1.3, 6.0))
        if i >= max_count:
            break
        is_reel = post.is_video and (
            (post.typename == "GraphVideo" and "/reel/" in post.url) or (post.title == "Reels")
        )
        results.append({
            'shortcode': post.shortcode,
            'is_reel': is_reel,
            'post_type': 'reel' if is_reel else 'post',
            'likes': post.likes,
            'comments': post.comments,
            'view_count': getattr(post, "video_view_count", None),
            'caption': post.caption,
            'thumbnail_url': post.url,
            'taken_at': post.date_utc
        })
    return results

def save_to_csv(data, filename):
    if not data:
        print("No data to save.")
        return
    keys = data[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
        dict_writer = csv.DictWriter(f, keys)
        dict_writer.writeheader()
        dict_writer.writerows(data)
    print(f"Saved {len(data)} rows to {filename}")

if __name__ == "__main__":
    target_username = input("수집할 인스타그램 계정명 입력: ").strip()
    max_posts = int(input("가져올 게시물+릴스 개수 입력(예: 20): "))
    result = fetch_posts(target_username, max_posts)
    save_to_csv(result, f"{target_username}_posts.csv")
    print("샘플:")
    for row in result[:3]:
        print(row)