"""
Investment OS — 실시장 데이터 수집 + Signal 자동 계산
[2026-03-21 Fix v3]
- Root Cause: yfinance 0.2.x MultiIndex → iloc[-1]이 Series 반환
- Fix: .values[-1] 로 numpy 배열 경유 → 확실한 스칼라 추출
- Fix: FRED API Key 없으면 호출 건너뜀
"""

import yfinance as yf
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

TICKERS = {
    "sp500":        "^GSPC",
    "nasdaq":       "^IXIC",
    "dow":          "^DJI",
    "vix":          "^VIX",
    "us10y":        "^TNX",
    "oil":          "CL=F",
    "gold":         "GC=F",
    "dollar_index": "DX-Y.NYB",
    "xlf":          "XLF",
    "soxs":         "SOXX",
    "tlt":          "TLT",
}

# ──────────────────────────────────────────
# 1. 시장 데이터 수집
# ──────────────────────────────────────────

def _to_series(close) -> pd.Series:
    """
    yfinance Close 컬럼을 확실하게 1D pd.Series로 변환.
    MultiIndex DataFrame → 첫 번째 컬럼 추출
    """
    if isinstance(close, pd.DataFrame):
        # MultiIndex: 컬럼이 ('Close', 'AAPL') 형태인 경우
        close = close.iloc[:, 0]
    return close.dropna()


def _scalar(val) -> float:
    """
    pd.Series / np.ndarray / scalar 모두 float 스칼라로 변환.
    iloc[-1]이 Series를 반환하는 yfinance 버그 방어.
    """
    if isinstance(val, (pd.Series, np.ndarray)):
        return float(val.values.flat[0])
    return float(val)


def fetch_price_data(period_days: int = 35) -> dict:
    end   = datetime.today()
    start = end - timedelta(days=period_days + 10)
    result = {}
    for key, ticker in TICKERS.items():
        try:
            df = yf.download(ticker, start=start, end=end,
                             progress=False, auto_adjust=True)
            if df.empty:
                logger.warning(f"[{key}] 데이터 없음: {ticker}")
                result[key] = None
                continue
            result[key] = _to_series(df["Close"])
        except Exception as e:
            logger.error(f"[{key}] 수집 오류: {e}")
            result[key] = None
    return result


def calc_20d_return(series: pd.Series) -> float:
    """20일 수익률(%). _scalar()로 yfinance MultiIndex 버그 완전 방어."""
    if series is None or len(series) < 21:
        return None
    try:
        val_now  = _scalar(series.values[-1])
        val_prev = _scalar(series.values[-21])
        if val_prev == 0:
            return None
        return round((val_now - val_prev) / val_prev * 100, 2)
    except Exception as e:
        logger.error(f"calc_20d_return 오류: {e}")
        return None


def get_latest(series: pd.Series) -> float:
    if series is None or series.empty:
        return None
    try:
        return round(_scalar(series.values[-1]), 2)
    except Exception:
        return None


# ──────────────────────────────────────────
# 2. FRED API
# ──────────────────────────────────────────

FRED_BASE   = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = {
    "breakeven_5y": "T5YIE",
    "hy_spread":    "BAMLH0A0HYM2",
}

def fetch_fred(series_id: str, api_key: str) -> float:
    params = {
        "series_id":  series_id,
        "sort_order": "desc",
        "limit":      5,
        "file_type":  "json",
        "api_key":    api_key,
    }
    try:
        r = requests.get(FRED_BASE, params=params, timeout=10)
        r.raise_for_status()
        for o in r.json().get("observations", []):
            if o["value"] != ".":
                return float(o["value"])
    except Exception as e:
        logger.warning(f"FRED [{series_id}] 오류: {e}")
    return None


def fetch_all_fred(api_key: str = None) -> dict:
    if not api_key:
        logger.info("FRED API Key 미설정 → 관련 Signal 기본값 사용")
        return {k: None for k in FRED_SERIES}
    return {k: fetch_fred(v, api_key) for k, v in FRED_SERIES.items()}


# ──────────────────────────────────────────
# 3. Signal 계산 (0~100)
# ──────────────────────────────────────────

def calc_signal_1_volatility(vix):
    if vix is None: return 50
    if vix < 15: return 20
    if vix < 20: return 40
    if vix < 30: return 60
    if vix < 40: return 80
    return 100

def calc_signal_2_oil_shock(r):
    if r is None: return 20
    if r < 5:  return 20
    if r < 10: return 40
    if r < 15: return 60
    if r < 25: return 80
    return 100

def calc_signal_3_commodity(r):
    if r is None: return 25
    if r < 3:  return 25
    if r < 7:  return 50
    if r < 12: return 75
    return 100

def calc_signal_4_gold(r):
    if r is None: return 50
    if r < 0:  return 25
    if r < 5:  return 50
    if r < 10: return 75
    return 100

def calc_signal_5_nasdaq_relative(n, s):
    if n is None or s is None: return 50
    rel = n - s
    if rel < -3: return 20
    if rel < 0:  return 40
    if rel < 3:  return 60
    if rel < 6:  return 80
    return 100

def calc_signal_6_ai_momentum(r):
    if r is None: return 50
    if r < -5: return 25
    if r < 3:  return 50
    if r < 10: return 75
    return 100

def calc_signal_9_dollar(r):
    if r is None:  return 50
    if r <= -3:    return 20
    if r < 0:      return 40
    if r < 3:      return 60
    if r < 6:      return 80
    return 100

def calc_signal_10_credit(hy):
    if hy is None:  return 40
    if hy < 350:    return 20
    if hy < 450:    return 40
    if hy < 550:    return 60
    if hy < 700:    return 80
    return 100

def calc_signal_11_financial(r):
    if r is None: return 50
    if r < -5:    return 25
    if r < 0:     return 50
    if r < 5:     return 75
    return 100

def calc_signal_12_banking(r):
    if r is None: return 50
    if r > 5:     return 20
    if r > 0:     return 50
    if r > -5:    return 80
    return 100

def calc_signal_13_correlation(s, t):
    if s is None or t is None: return 50
    if s < -3 and t < -3:      return 80
    if s < 0  and t < 0:       return 60
    if s > 0  and t > 0:       return 20
    return 40

def calc_signal_15_inflation(b):
    if b is None:  return 50
    if b < 2.0:    return 20
    if b < 2.3:    return 50
    if b < 2.6:    return 75
    return 100


# ──────────────────────────────────────────
# 4. 메인 함수
# ──────────────────────────────────────────

def build_raw_signals(fred_api_key: str = None,
                      manual_overrides: dict = None) -> dict:
    logger.info("시장 데이터 수집 시작...")
    prices = fetch_price_data()
    fred   = fetch_all_fred(fred_api_key)

    sp500_20d  = calc_20d_return(prices.get("sp500"))
    nasdaq_20d = calc_20d_return(prices.get("nasdaq"))
    oil_20d    = calc_20d_return(prices.get("oil"))
    gold_20d   = calc_20d_return(prices.get("gold"))
    dxy_20d    = calc_20d_return(prices.get("dollar_index"))
    xlf_20d    = calc_20d_return(prices.get("xlf"))
    sox_20d    = calc_20d_return(prices.get("soxs"))
    tlt_20d    = calc_20d_return(prices.get("tlt"))

    commodity_20d = None
    if oil_20d is not None and gold_20d is not None:
        commodity_20d = round((oil_20d + gold_20d) / 2, 2)
    elif oil_20d is not None:
        commodity_20d = oil_20d

    vix_now       = get_latest(prices.get("vix"))
    hy_spread_now = fred.get("hy_spread")
    breakeven_now = fred.get("breakeven_5y")

    logger.info(f"VIX={vix_now}, SP500_20d={sp500_20d}%, Oil_20d={oil_20d}%")
    logger.info(f"HY_Spread={hy_spread_now}, Breakeven={breakeven_now}")

    signals = {
        "volatility_pressure_signal":   calc_signal_1_volatility(vix_now),
        "oil_shock_signal":             calc_signal_2_oil_shock(oil_20d),
        "commodity_signal":             calc_signal_3_commodity(commodity_20d),
        "gold_signal":                  calc_signal_4_gold(gold_20d),
        "nasdaq_relative_signal":       calc_signal_5_nasdaq_relative(nasdaq_20d, sp500_20d),
        "ai_momentum_signal":           calc_signal_6_ai_momentum(sox_20d),
        "global_liquidity_signal":      50,
        "central_bank_support_signal":  25,
        "dollar_tightening_signal":     calc_signal_9_dollar(dxy_20d),
        "credit_stress_signal":         calc_signal_10_credit(hy_spread_now),
        "financial_stability_signal":   calc_signal_11_financial(xlf_20d),
        "banking_stress_signal":        calc_signal_12_banking(xlf_20d),
        "correlation_risk_signal":      calc_signal_13_correlation(sp500_20d, tlt_20d),
        "geopolitical_shock_signal":    50,
        "inflation_expectation_signal": calc_signal_15_inflation(breakeven_now),
        "shock_level_signal":           20,
        "liquidity_support_signal":     50,
    }

    if manual_overrides:
        for k, v in manual_overrides.items():
            if k in signals:
                logger.info(f"수동 오버라이드: {k} = {v}")
                signals[k] = v

    snapshot = {
        "sp500":        get_latest(prices.get("sp500")),
        "nasdaq":       get_latest(prices.get("nasdaq")),
        "dow":          get_latest(prices.get("dow")),
        "vix":          vix_now,
        "us10y":        get_latest(prices.get("us10y")),
        "oil":          get_latest(prices.get("oil")),
        "gold":         get_latest(prices.get("gold")),
        "dollar_index": get_latest(prices.get("dollar_index")),
    }

    return {
        "signals":  signals,
        "snapshot": snapshot,
        "raw_returns": {
            "sp500_20d":  sp500_20d,
            "nasdaq_20d": nasdaq_20d,
            "oil_20d":    oil_20d,
            "gold_20d":   gold_20d,
            "dxy_20d":    dxy_20d,
            "xlf_20d":    xlf_20d,
            "sox_20d":    sox_20d,
        }
    }
