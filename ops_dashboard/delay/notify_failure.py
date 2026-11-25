"""예약 지연 감지 수집기 치명 실패 알림 — yml `if: failure()` step 전용 (DN2026_0128).

판정은 기존 ops_dashboard.notify_failure.should_notify 를 그대로 쓴다(status=fatal·결과 없음만).
"""

from __future__ import annotations

import os
import sys

from ops_dashboard.delay.settings import load_delay_settings
from ops_dashboard.notify_failure import should_notify
from ops_dashboard.tg_client import TelegramClient, TelegramError


def main() -> int:
    if not should_notify():
        print("[delay_notify_failure] partial/ok — 알림 생략")
        return 0
    settings = load_delay_settings(os.environ)
    if settings.mode != "live" or not (settings.tg_token and settings.tg_chat):
        print("[delay_notify_failure] live 아님 또는 설정 누락 — 알림 생략")
        return 0
    text = "[예약 지연 감지] ⚠️ 수집기 실행 실패"
    if settings.run_url:
        text += f"\n실행: {settings.run_url}"
    try:
        TelegramClient(settings.tg_token, settings.tg_chat).send(text)
    except TelegramError as exc:
        print(f"[delay_notify_failure] 발송 실패: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — 토큰 포함 가능한 예외 본문은 출력하지 않음
        print(f"[delay_notify_failure] 발송 실패: {type(exc).__name__}")
        return 1
    print("[delay_notify_failure] 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
