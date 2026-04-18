"""
Investment OS — 메인 실행 파일
실행: python main.py [--session postmarket|premarket] [--dry-run]

--dry-run: X 포스팅 없이 분석 결과만 출력 (테스트용)
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path

from data_fetcher import build_raw_signals
from claude_caller import call_claude
from x_publisher import post_to_x
from ops_enhancer import run_quality_checks, export_operator_bundle

# ──────────────────────────────────────────
# 로그 설정
# ──────────────────────────────────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 발행 이력 저장
# ──────────────────────────────────────────
HISTORY_DIR = Path("data/post_history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

def save_history(session: str, core_data: dict, post_result: dict):
    filename = HISTORY_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M')}_{session}.json"
    record = {
        "timestamp": datetime.now().isoformat(),
        "session":   session,
        "regime":    core_data.get("data", {}).get("market_regime", ""),
        "signal":    core_data.get("data", {}).get("trading_signal", {}).get("signal", ""),
        "x_result":  post_result,
        "core_data": core_data,
    }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info(f"이력 저장: {filename}")


# ──────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────
def run(session: str = "postmarket", dry_run: bool = False,
        manual_overrides: dict = None):

    logger.info(f"=== Investment OS 자동 실행 시작 | 세션: {session} ===")

    # 환경변수에서 API 키 로드
    claude_key  = os.environ.get("ANTHROPIC_API_KEY")
    x_key       = os.environ.get("X_API_KEY")
    x_secret    = os.environ.get("X_API_SECRET")
    x_token     = os.environ.get("X_ACCESS_TOKEN")
    x_t_secret  = os.environ.get("X_ACCESS_TOKEN_SECRET")
    fred_key    = os.environ.get("FRED_API_KEY")  # 선택사항

    # 필수 키 확인
    if not claude_key:
        logger.error("ANTHROPIC_API_KEY 없음. 종료.")
        sys.exit(1)
    if not dry_run and not all([x_key, x_secret, x_token, x_t_secret]):
        logger.error("X API 키 불완전. .env 파일 확인.")
        sys.exit(1)

    # Step 1: 실시장 데이터 수집 + Signal 계산
    try:
        data = build_raw_signals(
            fred_api_key=fred_key,
            manual_overrides=manual_overrides
        )
        signals     = data["signals"]
        snapshot    = data["snapshot"]
        raw_returns = data["raw_returns"]
        logger.info(f"Signal 계산 완료: VIX={snapshot.get('vix')}, "
                    f"Oil 20d={raw_returns.get('oil_20d')}%")
    except Exception as e:
        logger.error(f"데이터 수집 실패: {e}")
        sys.exit(1)

    # Step 2: Claude API 호출
    try:
        core_data = call_claude(
            api_key=claude_key,
            signals=signals,
            snapshot=snapshot,
            raw_returns=raw_returns,
            session_type=session,
        )
        regime = core_data.get("data", {}).get("market_regime", "Unknown")
        signal = core_data.get("data", {}).get("trading_signal", {}).get("signal", "")
        logger.info(f"분석 완료: Regime={regime}, Signal={signal}")
    except Exception as e:
        logger.error(f"Claude API 실패: {e}")
        sys.exit(1)

    # Step 3: 품질 점검 + 운영자 번들 저장
    qa = run_quality_checks(core_data)
    if qa["is_passed"]:
        logger.info("Core Data 품질 점검 통과")
    else:
        logger.error(f"Core Data 품질 점검 실패: {qa['critical_failures']}")
    if qa["warnings"]:
        logger.warning(f"Core Data 경고: {qa['warnings']}")

    export_paths = export_operator_bundle(session=session, core_data=core_data, qa=qa)
    logger.info(
        f"운영자 번들 저장: json={export_paths['json_path']}, md={export_paths['md_path']}"
    )

    # Step 4: X 포스팅 (dry_run 아닌 경우)
    if dry_run:
        x_post = core_data.get("data", {}).get("sns", {}).get("x_post", "")
        logger.info(f"[DRY RUN] X 포스팅 미실행. 내용 미리보기:\n{x_post}")
        post_result = {"success": True, "dry_run": True, "text": x_post}
    elif not qa["is_passed"]:
        post_result = {
            "success": False,
            "dry_run": True,
            "text": "",
            "error": f"QA critical failure: {qa['critical_failures']}",
        }
        logger.error("품질 점검 Critical 실패로 X 포스팅 차단")
    else:
        post_result = post_to_x(
            api_key=x_key,
            api_secret=x_secret,
            access_token=x_token,
            access_token_secret=x_t_secret,
            core_data=core_data,
        )

        if not post_result["success"]:
            logger.error(f"X 포스팅 실패: {post_result['error']}")
            # Telegram 알림 (선택 — 구현 시 아래 주석 해제)
            # notify_telegram(os.environ.get("TELEGRAM_TOKEN"),
            #                 os.environ.get("TELEGRAM_CHAT_ID"),
            #                 f"❌ Investment OS X 포스팅 실패\n{post_result['error']}")
        else:
            logger.info(f"X 포스팅 성공: {post_result.get('tweet_id')}")

    # Step 5: 이력 저장
    save_history(session, core_data, post_result)
    logger.info("=== 실행 완료 ===")
    return core_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment OS 자동 실행")
    parser.add_argument("--session", default="postmarket",
                        choices=["premarket", "postmarket", "intraday", "crisis"])
    parser.add_argument("--dry-run", action="store_true",
                        help="X 포스팅 없이 분석만 실행")
    # 수동 오버라이드: --override geopolitical_shock_signal=80
    parser.add_argument("--override", nargs="*",
                        help="수동 Signal 오버라이드 (key=value 형식)")
    args = parser.parse_args()

    manual = {}
    if args.override:
        for item in args.override:
            k, v = item.split("=")
            manual[k] = int(v)

    run(session=args.session, dry_run=args.dry_run, manual_overrides=manual)
