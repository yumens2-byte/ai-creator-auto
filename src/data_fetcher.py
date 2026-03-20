"""
Investment OS — 실시장 데이터 수집 + Signal 자동 계산
yfinance (무료) + FRED API (무료) 사용
"""

import yfinance as yf
import requests
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 1. 시장 데이터 수집 (yfinance)
# ──────────────────────────────────────────

TICKERS = {
    "sp500":        "^GSPC",
    "nasdaq":       "^IXIC",
    "dow":          "^DJI",
    "vix":          "^VIX",
    "us10y":        "^TNX",
    "oil":          "CL=F",    # WTI Crude
    "gold":         "GC=F",
    "dollar_index": "DX-Y.NYB",
    "xlf":          "XLF",     # Financial sector (금융 시스템 신호)
    "gld":          "GLD",     # Gold ETF
    "qqq":          "QQQ",     # Nasdaq 100 (Nasdaq 상대강도용)
    "soxs":         "SOXX",    # Semiconductor (AI 모멘텀)
    "hyg":          "HYG",     # High Yield Bond (Credit Stress 프록시)
    "tlt":          "TLT",     # Long-term Treasury
}

def fetch_price_data(period_days: int = 30) -> dict:
    """
    yfinance로 최근 period_days일 가격 데이터 수집.
    반환: {ticker_key: pd.Series(종가)}
    """
    end = datetime.today()
    start = end - timedelta(days=period_days + 10)  # 여유 있게 가져옴

    result = {}
    for key, ticker in TICKERS.items():
        try:
            df = yf.download(ticker, start=start, end=end,
                             progress=False, auto_adjust=True)
            if df.empty:
                logger.warning(f"[{key}] 데이터 없음: {ticker}")
                result[key] = None
            else:
                result[key] = df["Close"].dropna()
        except Exception as e:
            logger.error(f"[{key}] 수집 오류: {e}")
            result[key] = None

    return result


def calc_20d_return(series: pd.Series) -> float:
    """20일 수익률 계산 (%). 데이터 부족 시 None 반환."""
    if series is None or len(series) < 21:
        return None
    pct = (series.iloc[-1] - series.iloc[-21]) / series.iloc[-21] * 100
    return round(float(pct), 2)


def get_latest(series: pd.Series) -> float:
    """최신 종가 반환."""
    if series is None or series.empty:
        return None
    return round(float(series.iloc[-1]), 2)


# ──────────────────────────────────────────
# 2. FRED API — 크레딧 / 유동성 데이터 (무료)
# ──────────────────────────────────────────

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

FRED_SERIES = {
    "breakeven_5y":     "T5YIE",    # 5년 기대 인플레이션 (Breakeven)
    "fed_balance":      "WALCL",    # Fed 자산총계 (주간)
    "hy_spread":        "BAMLH0A0HYM2",   # HY-Treasury Spread (bps)
    "ig_spread":        "BAMLC0A0CM",      # IG-Treasury Spread
}

def fetch_fred(series_id: str, api_key: str = None) -> float:
    """
    FRED에서 최신 값 1개 가져오기.
    api_key 없어도 일부 시리즈는 동작하나 제한 있음.
    무료 API Key: https://fred.stlouisfed.org/docs/api/api_key.html
    """
    params = {
        "series_id": series_id,
        "sort_order": "desc",
        "limit": 5,
        "file_type": "json",
    }
    if api_key:
        params["api_key"] = api_key

    try:
        r = requests.get(FRED_BASE, params=params, timeout=10)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        # 유효한 값 찾기 (FRED는 "." 으로 결측치 표시)
        for o in obs:
            if o["value"] != ".":
                return float(o["value"])
    except Exception as e:
        logger.warning(f"FRED [{series_id}] 오류: {e}")
    return None


def fetch_all_fred(api_key: str = None) -> dict:
    return {k: fetch_fred(v, api_key) for k, v in FRED_SERIES.items()}


# ──────────────────────────────────────────
# 3. 15개 Signal 자동 계산
# (01_analysis_engine.md Section 5 기준)
# ──────────────────────────────────────────

def calc_signal_1_volatility(vix: float) -> int:
    """Signal 1: Volatility Pressure Signal"""
    if vix is None: return 50
    if vix < 15:    return 20
    if vix < 20:    return 40
    if vix < 30:    return 60
    if vix < 40:    return 80
    return 100

def calc_signal_2_oil_shock(oil_20d_return: float) -> int:
    """Signal 2: Oil Shock Signal"""
    if oil_20d_return is None: return 20
    r = oil_20d_return
    if r < 5:   return 20
    if r < 10:  return 40
    if r < 15:  return 60
    if r < 25:  return 80
    return 100

def calc_signal_3_commodity(commodity_20d: float) -> int:
    """
    Signal 3: Commodity Signal
    commodity_20d: 원자재 지수 20일 상승률
    프록시: WTI + Gold 20일 수익률 평균 사용
    """
    if commodity_20d is None: return 25
    r = commodity_20d
    if r < 3:   return 25
    if r < 7:   return 50
    if r < 12:  return 75
    return 100

def calc_signal_4_gold(gold_20d: float) -> int:
    """Signal 4: Gold Signal"""
    if gold_20d is None: return 50
    r = gold_20d
    if r < 0:   return 25
    if r < 5:   return 50
    if r < 10:  return 75
    return 100

def calc_signal_5_nasdaq_relative(nasdaq_20d: float, sp500_20d: float) -> int:
    """Signal 5: Nasdaq Relative Signal"""
    if nasdaq_20d is None or sp500_20d is None: return 50
    rel = nasdaq_20d - sp500_20d
    if rel < -3:    return 20
    if rel < 0:     return 40
    if rel < 3:     return 60
    if rel < 6:     return 80
    return 100

def calc_signal_6_ai_momentum(sox_20d: float) -> int:
    """
    Signal 6: AI Momentum Signal
    프록시: 반도체 ETF (SOXX) 20일 수익률
    """
    if sox_20d is None: return 50
    if sox_20d < -5:    return 25
    if sox_20d < 3:     return 50
    if sox_20d < 10:    return 75
    return 100

def calc_signal_7_global_liquidity(fed_balance_prev: float,
                                    fed_balance_curr: float) -> int:
    """
    Signal 7: Global Liquidity Signal
    Fed 자산총계 방향성 (FRED WALCL)
    """
    if fed_balance_curr is None or fed_balance_prev is None:
        return 50
    change_pct = (fed_balance_curr - fed_balance_prev) / fed_balance_prev * 100
    if change_pct < -1.0:   return 0
    if change_pct < -0.3:   return 25
    if change_pct < 0.3:    return 50
    if change_pct < 1.0:    return 75
    return 100

def calc_signal_8_central_bank() -> int:
    """
    Signal 8: Central Bank Support Signal
    현재 Fed 정책: QT 지속 중 (2024~2026)
    → 자동화 어려움: FOMC 텍스트 분석 필요
    → 보수적 기본값 25 (긴축적) 사용 + 수동 오버라이드 지원
    실제 구현 시 FOMC 이후 수동 업데이트 권장
    """
    return 25  # QT 환경 기본값 — FOMC 이후 수동 조정

def calc_signal_9_dollar_tightening(dxy_20d: float) -> int:
    """Signal 9: Dollar Tightening Signal"""
    if dxy_20d is None: return 50
    r = dxy_20d
    if r <= -3:     return 20
    if r < 0:       return 40
    if r < 3:       return 60
    if r < 6:       return 80
    return 100

def calc_signal_10_credit_stress(hy_spread: float) -> int:
    """
    Signal 10: Credit Stress Signal
    FRED BAMLH0A0HYM2 (HY Spread, bps)
    정상 범위: 300~400bps / 스트레스: 600+bps
    """
    if hy_spread is None: return 40
    if hy_spread < 350:   return 20
    if hy_spread < 450:   return 40
    if hy_spread < 550:   return 60
    if hy_spread < 700:   return 80
    return 100

def calc_signal_11_financial_stability(xlf_20d: float) -> int:
    """
    Signal 11: Financial Stability Signal
    프록시: XLF 20일 수익률
    """
    if xlf_20d is None: return 50
    if xlf_20d < -5:    return 25
    if xlf_20d < 0:     return 50
    if xlf_20d < 5:     return 75
    return 100

def calc_signal_12_banking_stress(xlf_20d: float) -> int:
    """
    Signal 12: Banking Stress Signal
    XLF 약세 → 은행 스트레스 상승 (역방향)
    """
    if xlf_20d is None: return 50
    if xlf_20d > 5:     return 20
    if xlf_20d > 0:     return 50
    if xlf_20d > -5:    return 80
    return 100

def calc_signal_13_correlation_risk(sp500_20d: float,
                                     tlt_20d: float) -> int:
    """
    Signal 13: Correlation Risk Signal
    프록시: S&P500과 TLT 동반 하락 여부
    둘 다 하락 = 상관관계 위험 상승
    """
    if sp500_20d is None or tlt_20d is None: return 50
    if sp500_20d < -3 and tlt_20d < -3:     return 80
    if sp500_20d < 0 and tlt_20d < 0:       return 60
    if sp500_20d > 0 and tlt_20d > 0:       return 20
    return 40

def calc_signal_14_geopolitical() -> int:
    """
    Signal 14: Geopolitical Shock Signal
    자동화 불가 — 뉴스/이벤트 기반
    기본값 50 (중립) 사용 + 수동 오버라이드 지원
    """
    return 50  # 수동 조정 필요

def calc_signal_15_inflation_expectation(breakeven_5y: float) -> int:
    """
    Signal 15: Inflation Expectation Signal
    FRED T5YIE (5년 기대 인플레이션 %)
    """
    if breakeven_5y is None: return 50
    # 최근 트렌드를 절대값으로 근사
    if breakeven_5y < 2.0:   return 20
    if breakeven_5y < 2.3:   return 50
    if breakeven_5y < 2.6:   return 75
    return 100


# ──────────────────────────────────────────
# 4. 전체 파이프라인 실행 함수
# ──────────────────────────────────────────

def build_raw_signals(fred_api_key: str = None,
                       manual_overrides: dict = None) -> dict:
    """
    시장 데이터 수집 → 15개 Signal 계산 → dict 반환.

    manual_overrides: {
        "central_bank_support_signal": 25,   # FOMC 이후 수동
        "geopolitical_shock_signal": 80,     # 지정학 이벤트 발생 시
    }
    """
    logger.info("시장 데이터 수집 시작...")
    prices = fetch_price_data(period_days=35)
    fred   = fetch_all_fred(fred_api_key)

    # 20일 수익률 계산
    sp500_20d  = calc_20d_return(prices.get("sp500"))
    nasdaq_20d = calc_20d_return(prices.get("nasdaq"))
    oil_20d    = calc_20d_return(prices.get("oil"))
    gold_20d   = calc_20d_return(prices.get("gold"))
    dxy_20d    = calc_20d_return(prices.get("dollar_index"))
    xlf_20d    = calc_20d_return(prices.get("xlf"))
    sox_20d    = calc_20d_return(prices.get("soxs"))
    tlt_20d    = calc_20d_return(prices.get("tlt"))

    # Commodity 프록시: WTI + Gold 평균
    commodity_20d = None
    if oil_20d is not None and gold_20d is not None:
        commodity_20d = (oil_20d + gold_20d) / 2
    elif oil_20d is not None:
        commodity_20d = oil_20d

    # 최신 절대값
    vix_now       = get_latest(prices.get("vix"))
    hy_spread_now = fred.get("hy_spread")
    breakeven_now = fred.get("breakeven_5y")

    logger.info(f"VIX={vix_now}, SP500 20d={sp500_20d}%, Oil 20d={oil_20d}%")
    logger.info(f"HY Spread={hy_spread_now}bps, Breakeven={breakeven_now}%")

    signals = {
        "volatility_pressure_signal":   calc_signal_1_volatility(vix_now),
        "oil_shock_signal":             calc_signal_2_oil_shock(oil_20d),
        "commodity_signal":             calc_signal_3_commodity(commodity_20d),
        "gold_signal":                  calc_signal_4_gold(gold_20d),
        "nasdaq_relative_signal":       calc_signal_5_nasdaq_relative(nasdaq_20d, sp500_20d),
        "ai_momentum_signal":           calc_signal_6_ai_momentum(sox_20d),
        "global_liquidity_signal":      50,  # Fed balance 주간 데이터 — 근사값
        "central_bank_support_signal":  calc_signal_8_central_bank(),
        "dollar_tightening_signal":     calc_signal_9_dollar_tightening(dxy_20d),
        "credit_stress_signal":         calc_signal_10_credit_stress(hy_spread_now),
        "financial_stability_signal":   calc_signal_11_financial_stability(xlf_20d),
        "banking_stress_signal":        calc_signal_12_banking_stress(xlf_20d),
        "correlation_risk_signal":      calc_signal_13_correlation_risk(sp500_20d, tlt_20d),
        "geopolitical_shock_signal":    calc_signal_14_geopolitical(),
        "inflation_expectation_signal": calc_signal_15_inflation_expectation(breakeven_now),
        "shock_level_signal":           20,  # 기본값 낮음
        "liquidity_support_signal":     50,  # 중립 기본값
    }

    # 수동 오버라이드 적용
    if manual_overrides:
        for k, v in manual_overrides.items():
            if k in signals:
                logger.info(f"수동 오버라이드: {k} = {v}")
                signals[k] = v

    # 시장 스냅샷도 같이 반환
    snapshot = {
        "sp500":         get_latest(prices.get("sp500")),
        "nasdaq":        get_latest(prices.get("nasdaq")),
        "dow":           get_latest(prices.get("dow")),
        "vix":           vix_now,
        "us10y":         get_latest(prices.get("us10y")),
        "oil":           get_latest(prices.get("oil")),
        "gold":          get_latest(prices.get("gold")),
        "dollar_index":  get_latest(prices.get("dollar_index")),
    }

    return {
        "signals":  signals,
        "snapshot": snapshot,
        "raw_returns": {
            "sp500_20d":    sp500_20d,
            "nasdaq_20d":   nasdaq_20d,
            "oil_20d":      oil_20d,
            "gold_20d":     gold_20d,
            "dxy_20d":      dxy_20d,
            "xlf_20d":      xlf_20d,
            "sox_20d":      sox_20d,
        }
    }
