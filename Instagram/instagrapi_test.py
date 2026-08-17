


from instagrapi import Client
import json
import os

# 로그인 정보는 환경변수로 주입합니다. (소스에 하드코딩 금지)
#   export IG_USERNAME="..." IG_PASSWORD="..."
INSTAGRAM_USERNAME = os.environ["IG_USERNAME"]
INSTAGRAM_PASSWORD = os.environ["IG_PASSWORD"]

TARGET_USERNAME = "2_jaemyung"  # 수집 대상 계정명 (예시)
POST_LIMIT = 100  # 가져올 게시물 수

def main():
    cl = Client()
    cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    print(f"Logged in as: {INSTAGRAM_USERNAME}")

    user_id = cl.user_id_from_username(TARGET_USERNAME)
    medias = cl.user_medias(user_id, amount=POST_LIMIT)

    # 필요한 데이터만 추출
    posts_data = []
    for media in medias:
        post = {
            "id": media.pk,
            "code": media.code,
            "like_count": media.like_count,
            "comment_count": media.comment_count,
            "caption": media.caption_text,
            "taken_at": media.taken_at.isoformat() if media.taken_at else None,
            "media_type": media.media_type,
            "thumbnail_url": str(media.thumbnail_url) if media.thumbnail_url else None,
            "permalink": f"https://www.instagram.com/p/{media.code}/"
        }
        posts_data.append(post) 
        print(post)

    # JSON 파일로 저장
    with open(f"insta_{TARGET_USERNAME}_posts.json", "w", encoding="utf-8") as f:
        json.dump(posts_data, f, ensure_ascii=False, indent=2)
    print(f"{len(posts_data)}개 게시물을 insta_{TARGET_USERNAME}_posts.json 파일로 저장 완료.")

if __name__ == "__main__":
    main()