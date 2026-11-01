"""수집기 치명 실패 알림 — yml `if: failure()` step 전용 (상세설계 §4.4).

run_result.json 이 없거나 status=fatal 일 때만 보낸다. partial(일부 레포 수집 실패 등)은
본 실행이 상태 전환 알림으로 이미 처리하므로 보내지 않는다(반복 알림 방지).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ops_dashboard.config import load_settings
from ops_dashboard.tg_client import TelegramClient, TelegramError

RESULT = Path("out/run_result.json")


def should_notify(result_path: Path = RESULT) -> bool:
    if not result_path.exists():
        return True
    try:
        status = json.loads(result_path.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError, AttributeError):
        return True
    return status == "fatal"


def main() -> int:
    if not should_notify():
        print("[notify_failure] partial/ok — 알림 생략")
        return 0
    settings = load_settings(os.environ)
    if settings.mode != "live" or not (settings.tg_token and settings.tg_chat):
        print("[notify_failure] live 아님 또는 설정 누락 — 알림 생략")
        return 0
    text = "[운영 대시보드] ⚠️ 수집기 실행 실패"
    if settings.run_url:
        text += f"\n실행: {settings.run_url}"
    try:
        TelegramClient(settings.tg_token, settings.tg_chat).send(text)
    except TelegramError as exc:
        print(f"[notify_failure] 발송 실패: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — 토큰 포함 가능한 예외 본문은 출력하지 않음
        print(f"[notify_failure] 발송 실패: {type(exc).__name__}")
        return 1
    print("[notify_failure] 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
