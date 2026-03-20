"""
Investment OS — X (Twitter) API 포스팅
tweepy v4 사용 (X API v2)
"""

import tweepy
import logging

logger = logging.getLogger(__name__)

MAX_X_LENGTH = 280  # X 문자 제한 (한국어는 2바이트 카운트 — 실제 140자 기준)


def build_x_text(core_data: dict) -> str:
    """
    JSON Core Data에서 X 포스트 텍스트 조합.
    x_post + 해시태그 (140자 이내 검증 포함)
    """
    sns = core_data.get("data", {}).get("sns", {})
    x_post   = sns.get("x_post", "")
    hashtags = sns.get("hashtags", [])

    # 해시태그는 최대 4개 (140자 맞추기 위해)
    selected_tags = hashtags[:4]
    tag_str = " ".join(selected_tags)

    full_text = f"{x_post}\n\n{tag_str}".strip()

    # 길이 검증 (한국어 기준 140자 — tweepy는 유니코드 기준 처리)
    if len(full_text) > 270:
        logger.warning(f"X 포스트 길이 초과 ({len(full_text)}자). 해시태그 줄임.")
        selected_tags = hashtags[:2]
        tag_str = " ".join(selected_tags)
        full_text = f"{x_post}\n\n{tag_str}".strip()

    if len(full_text) > 280:
        logger.warning("x_post 자체가 길어 뒷부분 자름.")
        full_text = full_text[:277] + "..."

    return full_text


def post_to_x(api_key: str, api_secret: str,
               access_token: str, access_token_secret: str,
               core_data: dict) -> dict:
    """
    X API v2로 포스팅.
    반환: {"success": bool, "tweet_id": str, "text": str, "error": str}
    """
    text = build_x_text(core_data)
    logger.info(f"X 포스팅 시도: {text[:60]}...")

    try:
        client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )

        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        logger.info(f"X 포스팅 성공. Tweet ID: {tweet_id}")
        return {"success": True, "tweet_id": tweet_id, "text": text, "error": None}

    except tweepy.TweepyException as e:
        logger.error(f"X API 오류: {e}")
        return {"success": False, "tweet_id": None, "text": text, "error": str(e)}
