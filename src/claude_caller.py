"""
Investment OS — Claude API 호출 + 실데이터 주입
"""

import anthropic
import json
import re
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# System Prompt (Investment OS v2.1 핵심 압축본)
# 전체 md 파일 내용을 여기에 합쳐서 넣는 것이 이상적이나
# 토큰 절약을 위해 계산 엔진 핵심만 포함한 압축 버전 사용
# ──────────────────────────────────────────

SYSTEM_PROMPT = """
You are Investment OS v2.0, a US equity-focused ETF investment analysis system.

## CRITICAL RULES
1. When raw signal values are provided in the user message, use them DIRECTLY.
   Do NOT recalculate or estimate. The numbers are pre-computed from real market data.
2. Output ONLY valid JSON. No explanation text before or after.
3. All JSON keys must be in English.
4. Follow the exact formulas below in order.

## STEP 1 — Market Score (6개)
growth_score             = 0.40×ai_momentum_signal + 0.35×nasdaq_relative_signal + 0.25×liquidity_support_signal
inflation_score          = 0.40×oil_shock_signal + 0.35×commodity_signal + 0.25×inflation_expectation_signal
liquidity_score          = 0.40×global_liquidity_signal + 0.35×central_bank_support_signal + 0.25×(100-dollar_tightening_signal)
risk_score               = 0.30×volatility_pressure_signal + 0.30×shock_level_signal + 0.25×credit_stress_signal + 0.15×correlation_risk_signal
financial_stability_score= 0.45×financial_stability_signal + 0.35×(100-credit_stress_signal) + 0.20×(100-banking_stress_signal)
commodity_pressure_score = 0.50×oil_shock_signal + 0.35×commodity_signal + 0.15×gold_signal

## STEP 2 — Regime Probability (7개, clamp 0~100)
growth_regime_prob       = 0.45×growth_score + 0.25×liquidity_score + 0.15×financial_stability_score - 0.15×risk_score
inflation_regime_prob    = 0.45×inflation_score + 0.35×commodity_pressure_score - 0.20×financial_stability_score
crisis_regime_prob       = 0.45×risk_score + 0.25×credit_stress_signal + 0.20×shock_level_signal + 0.10×correlation_risk_signal
transition_regime_prob   = 100 - abs(growth_score-inflation_score) - abs(growth_score-risk_score)
liquidity_crisis_prob    = 0.45×(100-liquidity_score) + 0.30×dollar_tightening_signal + 0.25×credit_stress_signal
credit_crisis_prob       = 0.40×credit_stress_signal + 0.35×(100-financial_stability_score) + 0.25×shock_level_signal
stagflation_prob         = 0.45×inflation_score + 0.30×commodity_pressure_score + 0.25×(100-growth_score)

## STEP 3 — Market Regime (argmax of 4 core regimes)
market_regime = highest among: Growth Regime / Inflation Regime / Crisis Regime / Transition Regime

## STEP 4 — Risk Level
risk_score 0~39 → LOW / 40~69 → MEDIUM / 70~100 → HIGH

## STEP 5 — Shock Detector
Liquidity Shock:     liquidity_crisis_prob > 70
Oil Shock:           oil_shock_signal > 80
Geopolitical Shock:  geopolitical_shock_signal > 80
Credit Shock:        credit_stress_signal > 80
No trigger:          shock_type = "None", shock_level = "None"

## STEP 6 — ETF Strategy Bias (4개)
growth_allocation_bias    = 0.60×growth_regime_prob + 0.25×financial_stability_score - 0.15×risk_score
inflation_allocation_bias = 0.60×inflation_regime_prob + 0.25×commodity_pressure_score - 0.15×growth_score
crisis_allocation_bias    = 0.65×crisis_regime_prob + 0.20×credit_crisis_prob + 0.15×liquidity_crisis_prob
defense_allocation_bias   = 0.50×crisis_regime_prob + 0.30×geopolitical_shock_signal + 0.20×commodity_pressure_score

## STEP 7 — ETF Base Allocation by Regime
Growth:   QQQM=30,XLK=20,SPYM=20,XLE=15,ITA=10,TLT=5
Inflation:QQQM=10,XLK=10,SPYM=15,XLE=35,ITA=20,TLT=10
Crisis:   QQQM=5, XLK=5, SPYM=15,XLE=15,ITA=20,TLT=40
Risk Off: QQQM=10,XLK=10,SPYM=20,XLE=15,ITA=15,TLT=30

## STEP 8 — ETF Overweight Triggers
ai_momentum_signal > 70   → QQQM=Overweight, XLK=Overweight
oil_shock_signal > 60     → XLE=Overweight
geopolitical_shock_signal > 70 → ITA=Overweight
crisis_regime_prob > 60   → TLT=Overweight

## STEP 9 — Risk Engine
position_sizing: LOW=1.00 / MEDIUM=0.75 / HIGH=0.50
hedge_intensity         = 0.50×crisis_regime_prob + 0.30×inflation_regime_prob + 0.20×commodity_pressure_score
portfolio_defense_bias  = 0.50×risk_score + 0.30×crisis_regime_prob - 0.20×growth_score
crash_alert: Medium = VIX>25 AND credit_stress>60 / High = VIX>35 AND credit_stress>80 AND shock≠None

## STEP 10 — Trading Signal
Growth Regime + LOW             → BUY
Transition Regime OR MEDIUM     → HOLD
HIGH OR crisis_regime_prob > 60 → REDUCE
Crisis Regime + shock ≠ None    → HEDGE

## Required Output JSON
{
  "system": "Investment OS",
  "version": "2.0",
  "command": "run full",
  "timestamp": "<ISO8601>",
  "session_type": "postmarket",
  "status": "success",
  "data": {
    "market_snapshot": {"sp500":0,"nasdaq":0,"dow":0,"vix":0,"us10y":0,"oil":0,"gold":0,"dollar_index":0},
    "market_score": {"growth_score":0,"inflation_score":0,"liquidity_score":0,"risk_score":0,"financial_stability_score":0,"commodity_pressure_score":0},
    "regime_probability": {
      "growth_regime_probability":0,"inflation_regime_probability":0,
      "crisis_regime_probability":0,"transition_regime_probability":0,
      "liquidity_crisis_probability":0,"credit_crisis_probability":0,"stagflation_probability":0
    },
    "etf_bias": {"growth_allocation_bias":0,"inflation_allocation_bias":0,"crisis_allocation_bias":0,"defense_allocation_bias":0},
    "market_regime": "",
    "market_risk_level": "",
    "regime_reason": "",
    "shock": {"shock_type":"None","shock_level":"None","shock_driver":""},
    "etf_strategy": {"QQQM":"","XLK":"","SPYM":"","XLE":"","ITA":"","TLT":""},
    "allocation": {"QQQM":0,"XLK":0,"SPYM":0,"XLE":0,"ITA":0,"TLT":0},
    "portfolio_risk": {"position_sizing_multiplier":1.0,"hedge_intensity":0,"portfolio_defense_bias":0,"crash_alert_level":"","crash_driver":""},
    "trading_signal": {"signal":"","signal_reason":""},
    "one_line_summary": "",
    "sns": {"insta_caption":"","x_post":"","hashtags":[]}
  }
}
""".strip()


def build_user_prompt(signals: dict, snapshot: dict, raw_returns: dict,
                       session_type: str = "postmarket") -> str:
    """
    실제 계산된 Signal 수치를 Claude에 주입하는 User Prompt 생성.
    """
    from datetime import datetime
    now_kst = datetime.now().strftime("%Y-%m-%d %H:%M KST")

    return f"""run full

현재 시각: {now_kst}
세션: {session_type}

## 실시장 데이터 기반 Pre-computed Signals (이 값을 그대로 사용할 것)

### Market Snapshot (실제 시장 데이터)
SP500: {snapshot.get('sp500', 'N/A')}
Nasdaq: {snapshot.get('nasdaq', 'N/A')}
Dow: {snapshot.get('dow', 'N/A')}
VIX: {snapshot.get('vix', 'N/A')}
US10Y: {snapshot.get('us10y', 'N/A')}%
WTI Oil: {snapshot.get('oil', 'N/A')}
Gold: {snapshot.get('gold', 'N/A')}
Dollar Index: {snapshot.get('dollar_index', 'N/A')}

### 20일 수익률 (실제 계산값)
SP500 20d: {raw_returns.get('sp500_20d', 'N/A')}%
Nasdaq 20d: {raw_returns.get('nasdaq_20d', 'N/A')}%
WTI Oil 20d: {raw_returns.get('oil_20d', 'N/A')}%
Gold 20d: {raw_returns.get('gold_20d', 'N/A')}%
DXY 20d: {raw_returns.get('dxy_20d', 'N/A')}%
XLF 20d: {raw_returns.get('xlf_20d', 'N/A')}%
SOXX 20d: {raw_returns.get('sox_20d', 'N/A')}%

### 15개 Signal 값 (0~100, 공식 계산 완료)
volatility_pressure_signal: {signals['volatility_pressure_signal']}
oil_shock_signal: {signals['oil_shock_signal']}
commodity_signal: {signals['commodity_signal']}
gold_signal: {signals['gold_signal']}
nasdaq_relative_signal: {signals['nasdaq_relative_signal']}
ai_momentum_signal: {signals['ai_momentum_signal']}
global_liquidity_signal: {signals['global_liquidity_signal']}
central_bank_support_signal: {signals['central_bank_support_signal']}
dollar_tightening_signal: {signals['dollar_tightening_signal']}
credit_stress_signal: {signals['credit_stress_signal']}
financial_stability_signal: {signals['financial_stability_signal']}
banking_stress_signal: {signals['banking_stress_signal']}
correlation_risk_signal: {signals['correlation_risk_signal']}
geopolitical_shock_signal: {signals['geopolitical_shock_signal']}
inflation_expectation_signal: {signals['inflation_expectation_signal']}
shock_level_signal: {signals['shock_level_signal']}
liquidity_support_signal: {signals['liquidity_support_signal']}

## 출력 지시
위 Signal 값으로 공식대로 계산하여 JSON Core Data만 출력.
JSON 앞뒤에 설명 텍스트 절대 금지.
x_post는 한국어 140자 이내로 작성.
insta_caption은 한국어 전체 캡션으로 작성.
"""


def call_claude(api_key: str, signals: dict, snapshot: dict,
                raw_returns: dict, session_type: str = "postmarket") -> dict:
    """
    Claude API 호출 → JSON Core Data 반환.
    """
    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = build_user_prompt(signals, snapshot, raw_returns, session_type)

    logger.info("Claude API 호출 중...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}]
    )

    raw_text = response.content[0].text.strip()
    logger.info(f"Claude 응답 수신 ({len(raw_text)}자)")

    # JSON 파싱 — 설명 텍스트 포함 가능성 대비
    core_data = extract_json(raw_text)
    return core_data


def extract_json(text: str) -> dict:
    """
    Claude 응답에서 JSON만 추출.
    응답에 설명 텍스트가 섞인 경우도 처리.
    """
    # 1순위: 전체가 JSON인 경우
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2순위: ```json ... ``` 블록 추출
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3순위: 첫 번째 { ... } 블록 추출
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"JSON 파싱 실패. 응답 앞부분: {text[:200]}")
