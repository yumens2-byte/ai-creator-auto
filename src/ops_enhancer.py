"""
운영 고도화 유틸
- Core Data 품질 점검
- 운영자 브리프(마크다운) 생성
- Notion/Telegram 연계를 위한 번들 파일 저장
"""

import json
from datetime import datetime
from pathlib import Path


EXPORT_DIR = Path("data/exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def run_quality_checks(core_data: dict) -> dict:
    """
    Core Data의 최소 품질을 점검한다.
    반환:
      {
        "critical_failures": [...],
        "warnings": [...],
        "is_passed": bool
      }
    """
    failures = []
    warnings = []

    data = core_data.get("data", {})

    market_regime = data.get("market_regime")
    if not market_regime:
        failures.append("market_regime 누락")

    trading_signal = data.get("trading_signal", {}).get("signal")
    if not trading_signal:
        failures.append("trading_signal.signal 누락")

    allocation = data.get("allocation", {})
    if allocation:
        alloc_sum = sum(
            v for v in allocation.values() if isinstance(v, (int, float))
        )
        if alloc_sum < 80 or alloc_sum > 120:
            warnings.append(f"allocation 합계 비정상({alloc_sum})")
    else:
        warnings.append("allocation 비어있음")

    x_post = data.get("sns", {}).get("x_post", "")
    if not x_post:
        warnings.append("sns.x_post 비어있음")
    elif len(x_post) > 220:
        warnings.append(f"sns.x_post 길이 큼({len(x_post)}자)")

    return {
        "critical_failures": failures,
        "warnings": warnings,
        "is_passed": len(failures) == 0,
    }


def build_operator_brief(core_data: dict, session: str, qa: dict) -> str:
    """
    운영자가 즉시 확인 가능한 요약 문서를 생성한다.
    """
    data = core_data.get("data", {})
    snapshot = data.get("market_snapshot", {})
    signal = data.get("trading_signal", {}).get("signal", "N/A")
    reason = data.get("trading_signal", {}).get("signal_reason", "")
    regime = data.get("market_regime", "N/A")
    risk = data.get("market_risk_level", "N/A")
    one_liner = data.get("one_line_summary", "")

    warnings_md = "\n".join([f"- ⚠️ {w}" for w in qa.get("warnings", [])]) or "- 없음"
    failures_md = (
        "\n".join([f"- ❌ {f}" for f in qa.get("critical_failures", [])]) or "- 없음"
    )

    return f"""# Investment OS 운영 브리프

- 생성시각(UTC): {datetime.utcnow().isoformat()}Z
- 세션: {session}
- 체제(Regime): **{regime}**
- 리스크 레벨: **{risk}**
- 트레이딩 시그널: **{signal}**

## 한 줄 요약
{one_liner}

## 시그널 근거
{reason}

## 스냅샷
- SP500: {snapshot.get("sp500", "N/A")}
- Nasdaq: {snapshot.get("nasdaq", "N/A")}
- Dow: {snapshot.get("dow", "N/A")}
- VIX: {snapshot.get("vix", "N/A")}
- US10Y: {snapshot.get("us10y", "N/A")}
- Oil: {snapshot.get("oil", "N/A")}
- Gold: {snapshot.get("gold", "N/A")}
- Dollar Index: {snapshot.get("dollar_index", "N/A")}

## QA 결과
### Critical
{failures_md}

### Warnings
{warnings_md}
"""


def export_operator_bundle(session: str, core_data: dict, qa: dict) -> dict:
    """
    자동화 파이프라인 후속 연결을 위한 산출물(JSON + MD) 저장.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    base = EXPORT_DIR / f"{ts}_{session}"

    json_path = base.with_suffix(".bundle.json")
    md_path = base.with_suffix(".brief.md")

    bundle = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "session": session,
        "qa": qa,
        "core_data": core_data,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    brief_text = build_operator_brief(core_data, session, qa)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(brief_text)

    return {"json_path": str(json_path), "md_path": str(md_path)}
