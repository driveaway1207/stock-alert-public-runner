# -*- coding: utf-8 -*-
"""
US Stock Employee 1 V1.0 标准版
================================
定位：把 A股一号员工 V12.6 的“风险先行、结构种子、日线触发、同源合并、前5推送”体系
迁移到美股市场。Telegram 推送、JSON 输出、正式前5只、后台候选池、报告口径与 A股版保持一致。

美股适配核心：
1）先做 Universe Filter：排除 ETF/ETN/Fund/Warrant/Right/Unit/Preferred/SPAC/OTC，股价 < 1 美元直接剔除；
2）风险硬过滤前置：低价、低流动、非普通股、退市/OTC、手工重大雷区 flags 直接剔除正式候选；
3）美股无涨停，A股“涨停/涨停承接”替换为：gap up、强攻击阳线、earnings/news impulse、强攻击K实体承接；
4）量能用 relative volume / dollar volume，不只看昨比量；
5）只使用“上一个完整常规交易日”日线，不使用盘前/盘后/未完成K线；
6）全市场轻扫 520 日，候选深算 2200 日；复杂模型只跑深度候选，避免重复筛查。

运行依赖：
    pip install yfinance pandas numpy requests
可选文件：
    us_symbols.csv         手工股票池，列：symbol,name,exchange,security_type,etf
    us_risk_flags.json     手工/外部风险库，格式见 load_us_risk_flags()

常用环境变量：
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM
    US_MAX_STOCKS=0
    US_TOP_PUSH_LIMIT=5
    US_DEEP_SCORE_LIMIT=80
    US_MIN_PRICE=1
    US_MIN_AVG_DOLLAR_VOL=5000000
    US_BASE_LOOKBACK_DAYS=520
    US_DEEP_LOOKBACK_DAYS=2200
    US_SYMBOL_FILE=us_symbols.csv
    US_RISK_FLAGS_FILE=us_risk_flags.json
"""

import os
import re
import io
import csv
import json
import math
import time
import html
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import requests

try:
    import yfinance as yf
except Exception:  # 运行环境未安装时，主流程给出明确诊断
    yf = None

warnings.filterwarnings("ignore")

MODEL_VERSION = "US-V1.0标准版"
MARKET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ENABLE_TELEGRAM = os.environ.get("ENABLE_TELEGRAM", "0")

US_SYMBOL_FILE = os.environ.get("US_SYMBOL_FILE", "us_symbols.csv")
US_RISK_FLAGS_FILE = os.environ.get("US_RISK_FLAGS_FILE", "us_risk_flags.json")
US_SIGNAL_FILE = os.environ.get("US_SIGNAL_FILE", "us_signals_history.json")
US_CANDIDATE_FILE = os.environ.get("US_CANDIDATE_FILE", "us_stock_candidates.json")
US_SEED_POOL_FILE = os.environ.get("US_SEED_POOL_FILE", "us_stock_seed_pool.json")
US_CACHE_DIR = os.environ.get("US_CACHE_DIR", "us_kline_cache")

US_MAX_STOCKS = int(os.environ.get("US_MAX_STOCKS", "0"))
US_TOP_PUSH_LIMIT = int(os.environ.get("US_TOP_PUSH_LIMIT", "5"))
US_RESULT_LIMIT_RAW = int(os.environ.get("US_RESULT_LIMIT", "20"))
US_RESULT_LIMIT = min(US_RESULT_LIMIT_RAW, US_TOP_PUSH_LIMIT) if US_TOP_PUSH_LIMIT > 0 else US_RESULT_LIMIT_RAW
US_DEEP_SCORE_LIMIT = int(os.environ.get("US_DEEP_SCORE_LIMIT", "80"))
US_BASE_LOOKBACK_DAYS = int(os.environ.get("US_BASE_LOOKBACK_DAYS", "520"))
US_DEEP_LOOKBACK_DAYS = int(os.environ.get("US_DEEP_LOOKBACK_DAYS", "2200"))
US_MIN_PRICE = float(os.environ.get("US_MIN_PRICE", "1"))
US_MIN_AVG_DOLLAR_VOL = float(os.environ.get("US_MIN_AVG_DOLLAR_VOL", "5000000"))
US_MIN_AVG_VOLUME = float(os.environ.get("US_MIN_AVG_VOLUME", "300000"))
US_MAX_UNIVERSE_SYMBOLS = int(os.environ.get("US_MAX_UNIVERSE_SYMBOLS", "0"))
US_REQUEST_SLEEP = float(os.environ.get("US_REQUEST_SLEEP", "0.02"))
US_FINAL_SCORE_THRESHOLD = float(os.environ.get("US_FINAL_SCORE_THRESHOLD", "80"))
US_BASE_GATE_SCORE = float(os.environ.get("US_BASE_GATE_SCORE", "42"))
US_CHECK_DAYS = int(os.environ.get("US_CHECK_DAYS", "1"))
US_FETCH_RETRY = int(os.environ.get("US_FETCH_RETRY", "2"))
US_ENABLE_BENCHMARK_RS = os.environ.get("US_ENABLE_BENCHMARK_RS", "1")
US_BENCHMARKS = [x.strip().upper() for x in os.environ.get("US_BENCHMARKS", "SPY,QQQ").split(",") if x.strip()]

# 美股一号员工正式池不做低价垃圾股，不做非普通股，不用盘前盘后。
EXCLUDE_TYPE_PATTERNS = [
    r"ETF", r"ETN", r"FUND", r"TRUST", r"WARRANT", r"RIGHT", r"UNIT",
    r"PREFERRED", r"PREFERENCE", r"SPAC", r"ACQUISITION", r"NOTES", r"BOND",
    r"DEBENTURE", r"INDEX", r"CLOSED END", r"CEF", r"ADR WARRANT",
]
EXCLUDE_SYMBOL_SUFFIX_PATTERNS = [
    r"\$", r"\.W$", r"\.WS$", r"\.WT$", r"\.U$", r"\.R$", r"\.P$", r"\.PR", r"-W$", r"-U$", r"-R$",
]

@dataclass
class USSymbolInfo:
    symbol: str
    name: str = ""
    exchange: str = ""
    security_type: str = "Common Stock"
    is_etf: bool = False
    is_adr: bool = False

@dataclass
class RiskDecision:
    status: str                    # pass / watch / exclude
    score_penalty: float
    reasons: List[str]

@dataclass
class KeyLevel:
    timeframe: str                 # D/W/M/Q
    level_type: str                # max_vol_bull_high / max_vol_bull_floor / platform_high / gap_edge / impulse_mid 等
    price: float
    strength: float
    desc: str

@dataclass
class ScoreBlock:
    name: str
    score: float
    desc: str

@dataclass
class USCandidate:
    symbol: str
    name: str
    date: str
    close: float
    pct_chg: float
    dollar_volume: float
    final_score: float
    pool: str
    conclusion: str
    next_action: str
    give_up: str
    risk_status: str
    report: str
    blocks: List[Dict[str, Any]]
    diagnostics: Dict[str, Any]


def ensure_dirs() -> None:
    os.makedirs(US_CACHE_DIR, exist_ok=True)
    os.makedirs(os.path.join(US_CACHE_DIR, "base"), exist_ok=True)
    os.makedirs(os.path.join(US_CACHE_DIR, "deep"), exist_ok=True)


def safe_float(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, float) and math.isnan(x):
            return default
        return float(x)
    except Exception:
        return default


def pct(a, b, default=0.0) -> float:
    b = safe_float(b, 0.0)
    if abs(b) < 1e-9:
        return default
    return (safe_float(a) - b) / b * 100.0


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def html_escape(s: Any) -> str:
    return html.escape(str(s), quote=False)


def send_telegram(text: str) -> bool:
    if ENABLE_TELEGRAM != "1" or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] disabled")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks = split_text(text, 3600)
    ok = True
    for i, chunk in enumerate(chunks, 1):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            r = requests.post(url, json=payload, timeout=20)
            if not r.ok:
                ok = False
                print(f"[telegram] failed status={r.status_code} body={r.text[:200]}")
            else:
                try:
                    msg_id = r.json().get("result", {}).get("message_id")
                except Exception:
                    msg_id = "?"
                print(f"[telegram] sent chunk={i}/{len(chunks)} message_id={msg_id}")
        except Exception as e:
            ok = False
            print(f"[telegram] error {e}")
        time.sleep(0.4)
    return ok


def split_text(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.splitlines(True):
        if len(buf) + len(line) > limit and buf:
            parts.append(buf)
            buf = ""
        if len(line) > limit:
            for i in range(0, len(line), limit):
                parts.append(line[i:i+limit])
        else:
            buf += line
    if buf:
        parts.append(buf)
    return parts


def now_ny() -> datetime:
    return datetime.now(tz=MARKET_TZ)


def previous_weekday(d: date) -> date:
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


def get_last_complete_us_trade_date() -> date:
    """
    不使用盘前/盘后/未完成K线。
    无交易日历依赖时，用 NY 时间 16:30 后允许今天，否则回退到前一工作日；
    数据实际拉取后还会按 yfinance 返回的最后一根日K二次确认。
    """
    n = now_ny()
    today = n.date()
    if today.weekday() >= 5:
        return previous_weekday(today)
    close_cutoff = n.replace(hour=16, minute=30, second=0, microsecond=0)
    if n >= close_cutoff:
        return today
    return previous_weekday(today)


def normalize_symbol(sym: str) -> str:
    sym = str(sym).strip().upper()
    # Yahoo Finance 对 BRK.B 类常用 BRK-B；NASDAQ 原表可能用 .
    sym = sym.replace("/", "-")
    return sym


def is_excluded_type(info: USSymbolInfo) -> Tuple[bool, str]:
    sym = normalize_symbol(info.symbol)
    text = f"{info.name} {info.security_type}".upper()
    if info.is_etf:
        return True, "ETF/基金类"
    for pat in EXCLUDE_TYPE_PATTERNS:
        if re.search(pat, text, flags=re.I):
            return True, f"非普通股/高风险类型:{pat}"
    for pat in EXCLUDE_SYMBOL_SUFFIX_PATTERNS:
        if re.search(pat, sym, flags=re.I):
            return True, f"权证/Unit/Preferred等后缀:{pat}"
    # 许多 preferred 用 -P 或 -PR；class share 如 BRK-B 不能误杀，此处只结合名称/类型。
    return False, ""


def load_symbols_from_csv(path: str) -> List[USSymbolInfo]:
    if not os.path.exists(path):
        return []
    out = []
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    for _, r in df.iterrows():
        sym = normalize_symbol(r.get(cols.get("symbol", "symbol"), ""))
        if not sym:
            continue
        name = str(r.get(cols.get("name", "name"), ""))
        exch = str(r.get(cols.get("exchange", "exchange"), ""))
        stype = str(r.get(cols.get("security_type", "security_type"), r.get(cols.get("type", "type"), "Common Stock")))
        is_etf = str(r.get(cols.get("etf", "etf"), "N")).strip().upper() in ("Y", "TRUE", "1")
        out.append(USSymbolInfo(sym, name, exch, stype, is_etf, "ADR" in name.upper()))
    return out


def download_nasdaqtrader_symbols() -> List[USSymbolInfo]:
    """
    轻量下载 Nasdaq Trader 官方符号列表。
    nasdaqlisted.txt: Nasdaq 标的；otherlisted.txt: NYSE/AMEX 等。
    只作为股票池来源，不作为风险/基本面真相。
    """
    urls = [
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    ]
    out: List[USSymbolInfo] = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            txt = resp.text
            lines = [ln for ln in txt.splitlines() if "|" in ln and not ln.startswith("File Creation")]
            if not lines:
                continue
            reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|")
            for row in reader:
                if "Symbol" in row:
                    sym = normalize_symbol(row.get("Symbol", ""))
                    name = row.get("Security Name", "") or row.get("Company Name", "") or ""
                    exch = "NASDAQ"
                    etf = str(row.get("ETF", "N")).strip().upper() == "Y"
                    test_issue = str(row.get("Test Issue", "N")).strip().upper() == "Y"
                else:
                    sym = normalize_symbol(row.get("ACT Symbol", ""))
                    name = row.get("Security Name", "") or ""
                    exch = row.get("Exchange", "") or "OTHER"
                    etf = str(row.get("ETF", "N")).strip().upper() == "Y"
                    test_issue = str(row.get("Test Issue", "N")).strip().upper() == "Y"
                if not sym or test_issue:
                    continue
                out.append(USSymbolInfo(sym, name, exch, "Common Stock", etf, "ADR" in name.upper()))
        except Exception as e:
            print(f"[symbols] download failed url={url} err={e}")
    return out


def fallback_symbol_list() -> List[USSymbolInfo]:
    # 兜底列表，只用于无网络/无文件时让脚本可运行。
    syms = "AAPL MSFT NVDA AMZN META GOOGL TSLA AVGO AMD NFLX COST CRM ORCL JPM V MA BAC PLTR SMCI MU LLY UNH XOM CVX".split()
    return [USSymbolInfo(s, s, "", "Common Stock", False, False) for s in syms]


def load_us_universe() -> Tuple[List[USSymbolInfo], Dict[str, Any]]:
    source = "csv"
    symbols = load_symbols_from_csv(US_SYMBOL_FILE)
    if not symbols:
        source = "nasdaqtrader"
        symbols = download_nasdaqtrader_symbols()
    if not symbols:
        source = "fallback"
        symbols = fallback_symbol_list()

    filtered = []
    excluded_type = 0
    seen = set()
    for info in symbols:
        info.symbol = normalize_symbol(info.symbol)
        if not info.symbol or info.symbol in seen:
            continue
        seen.add(info.symbol)
        bad, _ = is_excluded_type(info)
        if bad:
            excluded_type += 1
            continue
        filtered.append(info)
    if US_MAX_UNIVERSE_SYMBOLS > 0:
        filtered = filtered[:US_MAX_UNIVERSE_SYMBOLS]
    if US_MAX_STOCKS > 0:
        filtered = filtered[:US_MAX_STOCKS]
    meta = {"source": source, "raw_count": len(symbols), "after_type_filter": len(filtered), "excluded_type": excluded_type}
    return filtered, meta


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def load_us_risk_flags(path: str) -> Dict[str, Any]:
    """
    手工/外部风险库。建议由 SEC/FMP/人工排雷脚本生成。
    示例：
    {
      "XYZ": {
        "major": ["going_concern", "nasdaq_delisting_notice"],
        "medium": ["dilution_risk", "class_action"],
        "note": "2026-xx 10-K going concern"
      }
    }
    """
    raw = load_json(path, {})
    return {normalize_symbol(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}


def cache_path(symbol: str, mode: str) -> str:
    safe = symbol.replace("/", "-").replace(".", "-")
    return os.path.join(US_CACHE_DIR, mode, f"{safe}.csv")


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    rename = {c: c.title() for c in df.columns}
    df = df.rename(columns=rename)
    required = ["Open", "High", "Low", "Close", "Volume"]
    for c in required:
        if c not in df.columns:
            return pd.DataFrame()
    df = df[required]
    df = df.dropna()
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            return pd.DataFrame()
    df.index = df.index.tz_localize(None)
    for c in required:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna()
    df = df[df["Volume"] >= 0]
    return df


def fetch_us_kline(symbol: str, lookback_days: int, mode: str, last_complete_day: date) -> pd.DataFrame:
    ensure_dirs()
    path = cache_path(symbol, mode)
    today_tag = datetime.now(UTC_TZ).strftime("%Y%m%d")
    if os.path.exists(path):
        try:
            dfc = pd.read_csv(path, index_col=0, parse_dates=True)
            if not dfc.empty:
                last_idx = pd.to_datetime(dfc.index[-1]).date()
                if last_idx >= last_complete_day:
                    return clean_ohlcv(dfc)
        except Exception:
            pass
    if yf is None:
        raise RuntimeError("缺少 yfinance：请在 workflow 中 pip install yfinance")
    start = last_complete_day - timedelta(days=int(lookback_days * 1.55) + 30)
    end = last_complete_day + timedelta(days=1)
    last_err = None
    for retry in range(US_FETCH_RETRY + 1):
        try:
            df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=True, prepost=False, progress=False, threads=False)
            df = clean_ohlcv(df)
            if not df.empty:
                # 确保不包含未来/未完成日线。
                df = df[df.index.date <= last_complete_day]
                if len(df) > 0:
                    df.tail(max(lookback_days + 10, 600)).to_csv(path)
                    return df
        except Exception as e:
            last_err = e
            time.sleep(0.5 + retry * 0.5)
    raise RuntimeError(f"fetch failed {symbol}: {last_err}")


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]
    for n in [5, 10, 20, 50, 120, 150, 200, 250]:
        df[f"MA{n}"] = close.rolling(n).mean()
    df["BBI"] = (df["MA3"] if "MA3" in df else close.rolling(3).mean()) if False else (close.rolling(3).mean() + close.rolling(6).mean() + close.rolling(12).mean() + close.rolling(24).mean()) / 4
    bbi_mid = df["BBI"]
    bbi_std = close.rolling(20).std()
    df["BBI_UP"] = bbi_mid + 2 * bbi_std
    df["BBI_DN"] = bbi_mid - 2 * bbi_std
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14).mean()
    df["VOL20"] = vol.rolling(20).mean()
    df["VOL50"] = vol.rolling(50).mean()
    df["DOLLAR_VOL"] = close * vol
    df["ADV20_DOLLAR"] = df["DOLLAR_VOL"].rolling(20).mean()
    df["RET1"] = close.pct_change() * 100
    df["RANGE_PCT"] = (high - low) / close.replace(0, np.nan) * 100
    df["BODY_PCT"] = (close - df["Open"]) / df["Open"].replace(0, np.nan) * 100
    df["BODY_ABS_PCT"] = (close - df["Open"]).abs() / df["Open"].replace(0, np.nan) * 100
    df["CLOSE_POS"] = (close - low) / (high - low).replace(0, np.nan)
    df["UPPER_SHADOW_RATIO"] = (high - pd.concat([df["Open"], close], axis=1).max(axis=1)) / (high - low).replace(0, np.nan)
    df["REL_VOL20"] = vol / df["VOL20"].replace(0, np.nan)
    df["REL_VOL50"] = vol / df["VOL50"].replace(0, np.nan)
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = df.resample(rule).agg(agg).dropna()
    return add_indicators(out) if not out.empty else out


def latest_row(df: pd.DataFrame) -> pd.Series:
    return df.iloc[-1]


def is_bull(row) -> bool:
    return safe_float(row.get("Close")) > safe_float(row.get("Open"))


def close_position(row) -> float:
    return safe_float(row.get("CLOSE_POS"), 0.5)


def major_risk_filter(symbol: str, info: USSymbolInfo, latest: pd.Series, df: pd.DataFrame, flags: Dict[str, Any]) -> RiskDecision:
    reasons: List[str] = []
    status = "pass"
    penalty = 0.0

    bad_type, type_reason = is_excluded_type(info)
    if bad_type:
        reasons.append(type_reason)
        return RiskDecision("exclude", 999, reasons)

    close = safe_float(latest.get("Close"))
    adv20_dollar = safe_float(latest.get("ADV20_DOLLAR"))
    vol20 = safe_float(latest.get("VOL20"))
    if close < US_MIN_PRICE:
        reasons.append(f"股价低于{US_MIN_PRICE:.2f}美元")
        return RiskDecision("exclude", 999, reasons)
    if adv20_dollar < US_MIN_AVG_DOLLAR_VOL:
        reasons.append(f"20日平均成交额不足({adv20_dollar/1e6:.1f}M)")
        status = "watch"
        penalty += 15
    if vol20 < US_MIN_AVG_VOLUME:
        reasons.append(f"20日均量不足({vol20:.0f})")
        status = "watch"
        penalty += 8

    # 手工/外部重大雷区：正式池一律剔除。
    f = flags.get(symbol, {}) if isinstance(flags, dict) else {}
    major = f.get("major", []) if isinstance(f, dict) else []
    medium = f.get("medium", []) if isinstance(f, dict) else []
    note = f.get("note", "") if isinstance(f, dict) else ""
    if major:
        reasons.extend([f"重大风险:{x}" for x in major])
        if note:
            reasons.append(str(note)[:80])
        return RiskDecision("exclude", 999, reasons)
    if medium:
        reasons.extend([f"高风险:{x}" for x in medium])
        status = "watch"
        penalty += 30

    # 技术性风险代理：长期低价+异常爆量+大跌，不能直接排除，但不进正式优先。
    if len(df) >= 80:
        recent = df.tail(60)
        low_price_days = int((recent["Close"] < 2.0).sum())
        big_down_high_vol = int(((recent["RET1"] < -8) & (recent["REL_VOL20"] > 2.5)).sum())
        if low_price_days > 20:
            reasons.append("长期低价股风险")
            status = "watch"
            penalty += 12
        if big_down_high_vol >= 2:
            reasons.append("近期多次放量大跌风险")
            status = "watch"
            penalty += 12

    if not reasons:
        reasons.append("未命中价格/流动性/非普通股/手工重大雷区")
    return RiskDecision(status, penalty, reasons)


def score_activity(df: pd.DataFrame) -> ScoreBlock:
    if len(df) < 120:
        return ScoreBlock("活跃度", 0, "样本不足")
    recent = df.tail(100)
    strong8 = int(((recent["RET1"] >= 8) & (recent["Close"] > recent["Open"])).sum())
    strong10 = int(((recent["RET1"] >= 10) & (recent["Close"] > recent["Open"])).sum())
    big_down = int((recent["RET1"] <= -8).sum())
    gaps = int(((recent["Open"] / recent["Close"].shift(1) - 1) * 100 >= 3).sum())
    atr_pct = safe_float((recent["ATR14"] / recent["Close"] * 100).median())
    small_body_ratio = safe_float((recent["BODY_ABS_PCT"] < 1.0).mean())
    score = 0.0
    score += min(6, strong8 * 1.2 + strong10 * 0.8)
    score += min(3, gaps * 0.35)
    if atr_pct >= 3:
        score += 2
    elif atr_pct >= 2:
        score += 1
    if small_body_ratio > 0.65 and strong8 <= 1:
        score -= 3
    if big_down >= 8:
        score -= 4
    elif big_down >= 4:
        score -= 2
    score = clip(score, -5, 10)
    desc = f"100日≥8%强阳{strong8}次、≥10%强阳{strong10}次、跳空{gaps}次、ATR约{atr_pct:.1f}%，黏密度{small_body_ratio:.0%}"
    if score < 0:
        desc = "活跃度偏低/风险偏高：" + desc
    elif score >= 6:
        desc = "活跃度较好：" + desc
    else:
        desc = "活跃度一般：" + desc
    return ScoreBlock("活跃度", score, desc)


def score_relative_strength(df: pd.DataFrame, benchmarks: Dict[str, pd.DataFrame]) -> ScoreBlock:
    if US_ENABLE_BENCHMARK_RS != "1" or len(df) < 80 or not benchmarks:
        return ScoreBlock("相对强弱", 0, "未启用或样本不足")
    descs = []
    scores = []
    for b, bdf in benchmarks.items():
        if bdf is None or len(bdf) < 80:
            continue
        try:
            s20 = pct(df["Close"].iloc[-1], df["Close"].iloc[-21])
            s60 = pct(df["Close"].iloc[-1], df["Close"].iloc[-61])
            b20 = pct(bdf["Close"].iloc[-1], bdf["Close"].iloc[-21])
            b60 = pct(bdf["Close"].iloc[-1], bdf["Close"].iloc[-61])
            rs20, rs60 = s20 - b20, s60 - b60
            sc = 0
            if rs20 > 5:
                sc += 2
            elif rs20 > 0:
                sc += 1
            if rs60 > 8:
                sc += 3
            elif rs60 > 0:
                sc += 1
            scores.append(sc)
            descs.append(f"相对{b}:20日{rs20:+.1f}%,60日{rs60:+.1f}%")
        except Exception:
            continue
    if not scores:
        return ScoreBlock("相对强弱", 0, "基准数据不足")
    score = min(6, max(scores))
    return ScoreBlock("相对强弱", score, "；".join(descs[:2]))


def valid_max_volume_bull_candle(row: pd.Series) -> bool:
    op, hi, lo, cl = [safe_float(row.get(x)) for x in ["Open", "High", "Low", "Close"]]
    if cl <= op:
        return False
    body = cl - op
    shadows = max(0, hi - cl) + max(0, op - lo)
    return body > 0 and body >= 0.5 * max(shadows, 1e-9)


def build_key_levels(tf: str, df: pd.DataFrame, lookback: int) -> List[KeyLevel]:
    levels: List[KeyLevel] = []
    if df is None or len(df) < 30:
        return levels
    sub = df.tail(min(lookback, len(df))).copy()
    # 最大量有效阳K：实底/高点。
    valid = sub[sub.apply(valid_max_volume_bull_candle, axis=1)]
    if not valid.empty:
        idx = valid["Volume"].idxmax()
        row = valid.loc[idx]
        floor = min(safe_float(row["Open"]), safe_float(row["Close"]))
        high = safe_float(row["High"])
        levels.append(KeyLevel(tf, "max_vol_bull_floor", floor, 8.0 if tf in ("M", "Q") else 5.0, f"{tf}有效最大量阳K实底"))
        levels.append(KeyLevel(tf, "max_vol_bull_high", high, 9.0 if tf in ("M", "Q") else 6.0, f"{tf}有效最大量阳K高点"))
    # 平台/前高：近期局部高点。
    for window, strength in [(20, 3.0), (60, 4.0), (120, 5.0)]:
        if len(sub) >= window:
            high = safe_float(sub["High"].tail(window).max())
            levels.append(KeyLevel(tf, f"platform_high_{window}", high, strength, f"{tf}{window}周期平台/前高"))
    # BBI/MA 动态位。
    last = sub.iloc[-1]
    for col, strength in [("BBI", 3.0), ("MA10", 2.2), ("MA20", 2.5), ("MA50", 3.0)]:
        v = safe_float(last.get(col), 0)
        if v > 0:
            levels.append(KeyLevel(tf, col.lower(), v, strength, f"{tf}{col}"))
    return levels


def body_above_ratio(row: pd.Series, level: float) -> float:
    op, cl = safe_float(row["Open"]), safe_float(row["Close"])
    lo_body, hi_body = min(op, cl), max(op, cl)
    body = hi_body - lo_body
    if body <= 1e-9:
        return 0.0
    above = max(0.0, hi_body - max(lo_body, level))
    return above / body


def evaluate_breakout(row: pd.Series, prev_close: float, level: KeyLevel) -> ScoreBlock:
    close, high, low, op = [safe_float(row.get(x)) for x in ["Close", "High", "Low", "Open"]]
    lv = level.price
    if lv <= 0:
        return ScoreBlock("突破质量", 0, "无有效关键位")
    touched = high >= lv
    close_up = close > lv
    gap_over = op > lv and low >= lv * 0.995
    body_ratio = body_above_ratio(row, lv)
    cp = close_position(row)
    upper = safe_float(row.get("UPPER_SHADOW_RATIO"), 0.5)
    relv = max(safe_float(row.get("REL_VOL20")), safe_float(row.get("REL_VOL50")))
    body_pct = safe_float(row.get("BODY_PCT"))
    if not touched:
        dist = (lv / close - 1) * 100 if close > 0 else 999
        return ScoreBlock("突破质量", 0, f"未触及{level.desc}，距离约{dist:.1f}%")
    score = 0.0
    if close_up and (body_ratio >= 0.55 or gap_over):
        score += 5
    elif close_up:
        score += 2
    else:
        return ScoreBlock("突破质量", -3, f"仅影线试探{level.desc}，收盘未站稳")
    if gap_over:
        score += 2
    if cp >= 0.85:
        score += 2
    elif cp >= 0.72:
        score += 1
    if upper <= 0.18:
        score += 1.5
    elif upper > 0.35:
        score -= 2
    if body_pct >= 5:
        score += 2
    elif body_pct >= 2.5:
        score += 1
    if 1.5 <= relv <= 4.0:
        score += 3
    elif relv > 4.0:
        score += 1
    elif relv < 1.2:
        score -= 2
    too_far = close / lv - 1
    if too_far > 0.08:
        score -= 2
    score = clip(score + min(3, level.strength * 0.35), -5, 16)
    desc = f"{level.desc}突破：实体在线上{body_ratio:.0%}，收盘位置{cp:.0%}，相对量{relv:.2f}，上影{upper:.0%}"
    return ScoreBlock("突破质量", score, desc)


def evaluate_pullback(df: pd.DataFrame, levels: List[KeyLevel]) -> ScoreBlock:
    if len(df) < 10 or not levels:
        return ScoreBlock("回踩承接", 0, "样本/关键位不足")
    row = latest_row(df)
    close, low, high = safe_float(row["Close"]), safe_float(row["Low"]), safe_float(row["High"])
    relv = max(safe_float(row.get("REL_VOL20")), safe_float(row.get("REL_VOL50")))
    cp = close_position(row)
    best = (0.0, "暂无有效回踩")
    for lv in levels:
        if lv.price <= 0:
            continue
        dist_low = abs(low / lv.price - 1)
        dist_close = abs(close / lv.price - 1)
        # 只看当前价格附近的关键位。
        if min(dist_low, dist_close) > 0.035:
            continue
        hold = close >= lv.price * 0.99
        pierce_reclaim = low < lv.price and close >= lv.price
        score = 0.0
        if hold:
            score += 4
        if pierce_reclaim:
            score += 2
        if relv <= 1.2:
            score += 2
        elif relv <= 1.8:
            score += 1
        elif relv > 2.5 and close < row["Open"]:
            score -= 3
        if cp >= 0.6:
            score += 1
        score += min(3, lv.strength * 0.25)
        if score > best[0]:
            best = (score, f"回踩/贴近{lv.desc}({lv.price:.2f})，收盘守住={hold}，相对量{relv:.2f}")
    return ScoreBlock("回踩承接", clip(best[0], 0, 12), best[1])


def volume_stability_model(df: pd.DataFrame) -> ScoreBlock:
    if len(df) < 80:
        return ScoreBlock("平量压缩", 0, "样本不足")
    pre = df.iloc[-50:-15]
    cur = df.iloc[-15:]
    if len(pre) < 20 or len(cur) < 8:
        return ScoreBlock("平量压缩", 0, "窗口不足")
    pre_cv = safe_float(pre["Volume"].std() / max(pre["Volume"].mean(), 1))
    cur_cv = safe_float(cur["Volume"].std() / max(cur["Volume"].mean(), 1))
    cur_mean = safe_float(cur["Volume"].mean())
    flat_ratio = safe_float(((cur["Volume"] >= cur_mean * 0.8) & (cur["Volume"] <= cur_mean * 1.2)).mean())
    pre_extreme = int((pre["Volume"] > pre["Volume"].rolling(10).mean() * 2.0).sum())
    cur_extreme = int((cur["Volume"] > cur_mean * 2.0).sum())
    range_pre = safe_float(pre["RANGE_PCT"].mean())
    range_cur = safe_float(cur["RANGE_PCT"].mean())
    score = 0.0
    if pre_cv > 0.55 and cur_cv < pre_cv * 0.72:
        score += 4
    elif cur_cv < pre_cv * 0.85:
        score += 2
    if flat_ratio >= 0.70:
        score += 3
    elif flat_ratio >= 0.55:
        score += 1.5
    if cur_extreme <= max(1, pre_extreme // 4):
        score += 1
    if range_cur < range_pre * 0.8:
        score += 2
    score = clip(score, 0, 10)
    desc = f"量能CV前段{pre_cv:.2f}→近15日{cur_cv:.2f}，平量比例{flat_ratio:.0%}，振幅{range_pre:.1f}%→{range_cur:.1f}%"
    return ScoreBlock("平量压缩", score, desc)


def typical_price_slope_model(tf: str, df: pd.DataFrame) -> ScoreBlock:
    if df is None or len(df) < 8:
        return ScoreBlock(f"{tf}重心", 0, "样本不足")
    n = 8 if tf == "Q" else 12 if tf == "M" else 13 if tf == "W" else 20
    sub = df.tail(min(n, len(df))).copy()
    tp = (sub["High"] + sub["Low"] + sub["Close"]) / 3
    x = np.arange(len(tp))
    if len(tp) < 5:
        return ScoreBlock(f"{tf}重心", 0, "窗口不足")
    slope = safe_float(np.polyfit(x, tp.values, 1)[0] / max(tp.mean(), 1e-9) * 100)
    lows_up = safe_float(sub["Low"].tail(len(sub)//2).median() > sub["Low"].head(len(sub)//2).median())
    vol_cv = safe_float(sub["Volume"].std() / max(sub["Volume"].mean(), 1))
    score = 0.0
    if slope > 0.8:
        score += 3
    elif slope > 0.2:
        score += 1.5
    if lows_up:
        score += 1.5
    if vol_cv < 0.35:
        score += 2
    elif vol_cv < 0.55:
        score += 1
    desc = f"{tf}重心斜率{slope:.2f}%/bar，低点抬高={bool(lows_up)}，量能CV{vol_cv:.2f}"
    return ScoreBlock(f"{tf}重心平量", clip(score, 0, 7), desc)


def step_volume_base_model(df: pd.DataFrame) -> ScoreBlock:
    if len(df) < 120:
        return ScoreBlock("台阶量能", 0, "样本不足")
    recent = df.tail(120).copy()
    # 粗切三段整理区，不追求完美分段，V1.0 先做稳定可用的台阶均量判断。
    segs = np.array_split(recent, 3)
    if any(len(s) < 20 for s in segs):
        return ScoreBlock("台阶量能", 0, "分段不足")
    centers = [safe_float(s["Close"].median()) for s in segs]
    vols = [safe_float(s["Volume"].mean()) for s in segs]
    cvs = [safe_float(s["Volume"].std() / max(s["Volume"].mean(), 1)) for s in segs]
    price_up = centers[-1] > centers[0] * 1.05
    vol_up = vols[1] > vols[0] * 1.10 and vols[2] > vols[1] * 1.05
    stable_last = cvs[-1] < 0.60
    bad_down = int(((recent["RET1"] < -5) & (recent["REL_VOL20"] > 1.8)).sum())
    score = 0.0
    if price_up:
        score += 2
    if vol_up:
        score += 4
    elif vols[-1] > vols[0] * 1.25:
        score += 2
    if stable_last:
        score += 2
    if bad_down <= 2:
        score += 1
    else:
        score -= 2
    desc = f"三段中枢{centers[0]:.2f}->{centers[1]:.2f}->{centers[2]:.2f}，均量比{vols[1]/max(vols[0],1):.2f}/{vols[2]/max(vols[1],1):.2f}，末段CV{cvs[-1]:.2f}"
    return ScoreBlock("台阶量能", clip(score, 0, 10), desc)


def thousand_day_window_model(df: pd.DataFrame) -> ScoreBlock:
    if len(df) < 1100:
        return ScoreBlock("1000日窗口", 0, "样本不足")
    # 过去约2000个交易日内重大高点，不用未来函数；当前只看已经发生的高点。
    sub = df.tail(min(2000, len(df))).copy()
    # 排除最近60日高点，避免刚创高也算周期起点。
    hist = sub.iloc[:-60] if len(sub) > 1100 else sub
    if hist.empty:
        return ScoreBlock("1000日窗口", 0, "无历史高点")
    idx = hist["High"].idxmax()
    days = int((df.index[-1] - idx).days / 1.45)  # 粗略交易日估计 fallback
    # 更准确：位置差。
    try:
        loc_high = df.index.get_loc(idx)
        if isinstance(loc_high, slice):
            loc_high = loc_high.start
        days = len(df) - 1 - int(loc_high)
    except Exception:
        pass
    score = 0.0
    desc = f"距离周期性高点约{days}个交易日"
    if 980 <= days <= 1020:
        score = 2.0
        desc += "，进入1000日前后窄口观察区"
    return ScoreBlock("1000日窗口", score, desc)


def long_range_probe_model(monthly: pd.DataFrame, daily: pd.DataFrame) -> ScoreBlock:
    """
    远期最大量阳K绿线 + 9号试盘高点 + 日线二次确认（美股版适用）。
    V1.0 不做过重扫描，只在深度候选上跑。
    """
    if len(monthly) < 40 or len(daily) < 180:
        return ScoreBlock("远期绿线9号", 0, "样本不足")
    m = monthly.tail(min(100, len(monthly))).copy()
    valid = m[m.apply(valid_max_volume_bull_candle, axis=1)]
    if valid.empty:
        return ScoreBlock("远期绿线9号", 0, "无有效最大量阳K")
    green_idx = valid["Volume"].idxmax()
    green = valid.loc[green_idx]
    green_high = safe_float(green["High"])
    after = m[m.index > green_idx]
    if len(after) < 6:
        return ScoreBlock("远期绿线9号", 0, "绿线后结构不足")
    probe = after[after["High"] > green_high]
    if probe.empty:
        return ScoreBlock("远期绿线9号", 0, f"未出现高于绿线{green_high:.2f}的试盘高点")
    # 找质量相对最好的试盘K。
    best_score, best_idx, best_row = -999, None, None
    for idx, row in probe.iterrows():
        vol_ratio = safe_float(row["Volume"] / max(green["Volume"], 1))
        body_pct = abs(safe_float(row["Close"] - row["Open"])) / max(safe_float(row["Open"]), 1e-9)
        upper_ratio = safe_float((row["High"] - max(row["Open"], row["Close"])) / max(row["High"] - row["Low"], 1e-9))
        q = 0
        if vol_ratio >= 0.45:
            q += 2
        if body_pct >= 0.08:
            q += 2
        elif body_pct >= 0.04:
            q += 1
        if upper_ratio < 0.45:
            q += 1
        if row["Close"] < green_high:
            q -= 0.5  # 月线未确认，不是一刀切坏，但降低确认度
        if q > best_score:
            best_score, best_idx, best_row = q, idx, row
    if best_row is None:
        return ScoreBlock("远期绿线9号", 0, "无有效试盘K")
    red_high = safe_float(best_row["High"])
    # 时间倍数关系
    try:
        n = monthly.index.get_loc(best_idx) - monthly.index.get_loc(green_idx)
        m_after = len(monthly) - 1 - monthly.index.get_loc(best_idx)
    except Exception:
        n, m_after = 0, 0
    ratio = m_after / n if n and n > 0 else 0
    time_bonus = 0
    if 0.85 <= ratio <= 1.15:
        time_bonus = 1.0
    elif 1.75 <= ratio <= 2.25:
        time_bonus = 2.0
    elif 2.70 <= ratio <= 3.30:
        time_bonus = 1.2
    # 日线是否漂亮突破红线
    drow = latest_row(daily)
    fake_level = KeyLevel("M", "probe_high", red_high, 8, "高质量试盘高点/9号线")
    br = evaluate_breakout(drow, safe_float(daily["Close"].iloc[-2]), fake_level)
    score = max(0, best_score) + time_bonus + max(0, br.score * 0.55)
    desc = f"绿线{green_high:.2f}，9号线{red_high:.2f}，试盘质量{best_score:.1f}，时间M/N={ratio:.2f}；{br.desc}"
    return ScoreBlock("远期绿线9号", clip(score, 0, 14), desc)


def choose_nearby_levels(df: pd.DataFrame, levels: List[KeyLevel], current_close: float) -> List[KeyLevel]:
    out = []
    for lv in levels:
        if lv.price <= 0:
            continue
        dist = abs(current_close / lv.price - 1)
        if dist <= 0.08:
            out.append(lv)
    out.sort(key=lambda x: (abs(current_close / x.price - 1), -x.strength))
    return out[:12]


def score_base_candidate(df: pd.DataFrame, info: USSymbolInfo, risk: RiskDecision) -> Tuple[float, List[ScoreBlock], Dict[str, Any]]:
    latest = latest_row(df)
    close = safe_float(latest["Close"])
    adv = safe_float(latest.get("ADV20_DOLLAR"))
    blocks: List[ScoreBlock] = []
    diagnostics: Dict[str, Any] = {}
    base = 0.0

    # 基础趋势/位置：美股主池不鼓励长期弱势垃圾股。
    ma50, ma200 = safe_float(latest.get("MA50")), safe_float(latest.get("MA200"))
    trend_score = 0.0
    trend_desc = []
    if close > ma50 > 0:
        trend_score += 3; trend_desc.append("站上MA50")
    if close > ma200 > 0:
        trend_score += 3; trend_desc.append("站上MA200")
    if ma50 > ma200 > 0:
        trend_score += 2; trend_desc.append("MA50在MA200上方")
    if len(df) >= 260:
        pos = (close - df["Low"].tail(260).min()) / max(df["High"].tail(260).max() - df["Low"].tail(260).min(), 1e-9)
        if 0.2 <= pos <= 0.75:
            trend_score += 2; trend_desc.append(f"年内位置{pos:.0%}适中")
        elif pos > 0.90:
            trend_score -= 2; trend_desc.append(f"年内位置{pos:.0%}偏高")
    blocks.append(ScoreBlock("趋势位置", clip(trend_score, -4, 10), "，".join(trend_desc) or "趋势一般"))

    blocks.append(score_activity(df))
    blocks.append(volume_stability_model(df))
    blocks.append(step_volume_base_model(df))
    blocks.append(thousand_day_window_model(df))

    # 近期触发：强阳/gap/相对量。
    relv = max(safe_float(latest.get("REL_VOL20")), safe_float(latest.get("REL_VOL50")))
    ret1 = safe_float(latest.get("RET1"))
    cp = close_position(latest)
    gap_pct = pct(latest.get("Open"), df["Close"].iloc[-2]) if len(df) >= 2 else 0
    trigger = 0.0
    tdesc = []
    if ret1 >= 4 and cp >= 0.75 and relv >= 1.5:
        trigger += 8; tdesc.append(f"强攻击日：涨{ret1:.1f}%、相对量{relv:.2f}、收盘强")
    elif ret1 >= 2 and relv >= 1.3:
        trigger += 4; tdesc.append(f"温和放量转强：涨{ret1:.1f}%、相对量{relv:.2f}")
    if gap_pct >= 3 and cp >= 0.65:
        trigger += 3; tdesc.append(f"跳空{gap_pct:.1f}%且收盘尚可")
    if ret1 < -6 and relv > 2:
        trigger -= 6; tdesc.append("放量大跌风险")
    blocks.append(ScoreBlock("日线触发", clip(trigger, -8, 12), "；".join(tdesc) or "暂无强触发"))

    raw = sum(b.score for b in blocks)
    # 美股主池流动性基础加分/扣分。
    if adv >= 20_000_000:
        raw += 4
    elif adv >= US_MIN_AVG_DOLLAR_VOL:
        raw += 2
    raw -= risk.score_penalty * 0.25
    diagnostics.update({"close": close, "adv20_dollar": adv, "risk_penalty": risk.score_penalty})
    return raw, blocks, diagnostics


def deep_score_candidate(df: pd.DataFrame, info: USSymbolInfo, risk: RiskDecision, benchmarks: Dict[str, pd.DataFrame]) -> USCandidate:
    df = add_indicators(df)
    weekly = resample_ohlcv(df, "W-FRI")
    monthly = resample_ohlcv(df, "M")
    quarterly = resample_ohlcv(df, "Q")
    latest = latest_row(df)
    close = safe_float(latest["Close"])
    prev_close = safe_float(df["Close"].iloc[-2]) if len(df) >= 2 else close
    pct_chg = pct(close, prev_close)
    dollar_volume = safe_float(latest.get("DOLLAR_VOL"))

    blocks: List[ScoreBlock] = []
    base_raw, base_blocks, diagnostics = score_base_candidate(df, info, risk)
    blocks.extend(base_blocks)
    blocks.append(score_relative_strength(df, benchmarks))

    levels: List[KeyLevel] = []
    levels.extend(build_key_levels("D", df, 260))
    levels.extend(build_key_levels("W", weekly, 160))
    levels.extend(build_key_levels("M", monthly, 100))
    levels.extend(build_key_levels("Q", quarterly, 40))
    nearby = choose_nearby_levels(df, levels, close)

    if nearby:
        breakout_blocks = [evaluate_breakout(latest, prev_close, lv) for lv in nearby[:8]]
        best_breakout = max(breakout_blocks, key=lambda b: b.score)
    else:
        best_breakout = ScoreBlock("突破质量", 0, "附近无高价值关键位")
    blocks.append(best_breakout)
    blocks.append(evaluate_pullback(df, nearby))

    # 多周期重心平量择优，不重复加分。
    mtf_blocks = [
        typical_price_slope_model("W", weekly),
        typical_price_slope_model("M", monthly),
        typical_price_slope_model("Q", quarterly),
    ]
    best_mtf = max(mtf_blocks, key=lambda b: b.score)
    blocks.append(ScoreBlock("多周期重心平量", best_mtf.score, best_mtf.desc))

    blocks.append(long_range_probe_model(monthly, df))

    # 同源合并/封顶：结构、承接、量能、活跃、时间、风险分组，防止重复堆分。
    group_caps = {
        "结构触发": 22,
        "承接量能": 24,
        "活跃趋势": 22,
        "时间窗口": 8,
        "相对强弱": 6,
    }
    structure_score = sum(b.score for b in blocks if b.name in ("突破质量", "远期绿线9号"))
    carry_score = sum(b.score for b in blocks if b.name in ("回踩承接", "平量压缩", "台阶量能"))
    active_score = sum(b.score for b in blocks if b.name in ("趋势位置", "活跃度", "日线触发", "多周期重心平量"))
    time_score = sum(b.score for b in blocks if b.name in ("1000日窗口",))
    rs_score = sum(b.score for b in blocks if b.name in ("相对强弱",))

    final = 50
    final += min(group_caps["结构触发"], structure_score)
    final += min(group_caps["承接量能"], carry_score)
    final += min(group_caps["活跃趋势"], active_score)
    final += min(group_caps["时间窗口"], time_score)
    final += min(group_caps["相对强弱"], rs_score)
    final -= risk.score_penalty

    # 风险池处理。
    pool = "优先候选池"
    if risk.status == "exclude":
        pool = "风险剔除池"
        final = min(final, 30)
    elif risk.status == "watch":
        pool = "高风险观察池"
        final = min(final, 76)

    # 正式候选必须有日线触发或回踩承接，避免只因大周期好被推送。
    trigger_ok = best_breakout.score >= 8 or any(b.name == "回踩承接" and b.score >= 6 for b in blocks) or any(b.name == "日线触发" and b.score >= 7 for b in blocks)
    if not trigger_ok and pool == "优先候选池":
        pool = "后台跟踪池"
        final = min(final, 79)

    if final >= US_FINAL_SCORE_THRESHOLD and pool == "优先候选池":
        conclusion = "可正式关注：日线触发/承接已接近买点"
    elif pool == "后台跟踪池":
        conclusion = "结构可跟踪，但正式买点未到"
    elif pool == "高风险观察池":
        conclusion = "有风险项，禁止进入正式Top5"
    else:
        conclusion = "不进入正式候选"

    # 人话报告：保持 A股版“为什么能看/问题/确认/放弃”。
    risk_text = "；".join(risk.reasons[:4])
    why = pick_good_reasons(blocks)
    problem = pick_problem_reasons(blocks, risk)
    confirm = build_confirm_text(nearby, best_breakout)
    give_up = build_give_up_text(nearby, close, risk)
    report = format_stock_report(info, latest, final, pool, conclusion, why, problem, confirm, give_up, risk_text, blocks)

    return USCandidate(
        symbol=info.symbol,
        name=info.name or info.symbol,
        date=str(df.index[-1].date()),
        close=close,
        pct_chg=pct_chg,
        dollar_volume=dollar_volume,
        final_score=round(final, 2),
        pool=pool,
        conclusion=conclusion,
        next_action=confirm,
        give_up=give_up,
        risk_status=risk.status,
        report=report,
        blocks=[asdict(b) for b in blocks],
        diagnostics={**diagnostics, "nearby_levels": [asdict(x) for x in nearby[:8]]},
    )


def pick_good_reasons(blocks: List[ScoreBlock]) -> List[str]:
    good = []
    for b in sorted(blocks, key=lambda x: x.score, reverse=True):
        if b.score >= 5:
            good.append(b.desc)
        if len(good) >= 3:
            break
    return good or ["暂无特别强的独立优势，主要作为后台跟踪。"]


def pick_problem_reasons(blocks: List[ScoreBlock], risk: RiskDecision) -> List[str]:
    probs = []
    if risk.status != "pass":
        probs.append("风险过滤未完全通过：" + "；".join(risk.reasons[:3]))
    for b in sorted(blocks, key=lambda x: x.score):
        if b.score < 0:
            probs.append(b.desc)
        if len(probs) >= 3:
            break
    return probs or ["主要问题是仍需等待日线确认，不能因为大周期结构好就追高。"]


def build_confirm_text(levels: List[KeyLevel], breakout: ScoreBlock) -> str:
    if levels:
        lv = levels[0]
        return f"确认条件：放量/健康相对量站稳 {lv.price:.2f} 附近关键位，或突破后回踩该位/强攻击K实体中部不破，再交给三号员工确认。"
    return "确认条件：必须出现实体强、收盘强、相对量健康的突破，或突破后回踩不破。"


def build_give_up_text(levels: List[KeyLevel], close: float, risk: RiskDecision) -> str:
    if risk.status != "pass":
        return "放弃条件：风险项未解除前，不进入正式交易候选。"
    if levels:
        lv = levels[0]
        return f"放弃条件：跌回关键位 {lv.price:.2f} 下方且收不回，或放量长阴破位/高开低走长上影。"
    return "放弃条件：放量长阴、突破失败、冲高回落或跌破近端平台。"


def format_stock_report(info: USSymbolInfo, latest: pd.Series, score: float, pool: str, conclusion: str,
                        why: List[str], problems: List[str], confirm: str, give_up: str,
                        risk_text: str, blocks: List[ScoreBlock]) -> str:
    close = safe_float(latest["Close"])
    ret1 = safe_float(latest.get("RET1"))
    adv = safe_float(latest.get("ADV20_DOLLAR")) / 1e6
    lines = []
    lines.append(f"<b>{html_escape(info.symbol)} {html_escape(info.name or '')}</b>")
    lines.append(f"收盘：{close:.2f} | 涨幅：{ret1:+.2f}% | 20日均成交额：{adv:.1f}M")
    lines.append(f"结论：{html_escape(conclusion)} | 综合分：{score:.2f} | 池：{html_escape(pool)}")
    lines.append("为什么能看：")
    for x in why[:3]:
        lines.append(f"- {html_escape(x)}")
    lines.append("问题/风险：")
    for x in problems[:3]:
        lines.append(f"- {html_escape(x)}")
    lines.append(html_escape(confirm))
    lines.append(html_escape(give_up))
    lines.append(f"风险过滤：{html_escape(risk_text)}")
    # 后台摘要保留核心块，不堆全量指标。
    summary_blocks = [b for b in blocks if b.name in ("突破质量", "回踩承接", "平量压缩", "台阶量能", "远期绿线9号", "1000日窗口", "相对强弱")]
    if summary_blocks:
        lines.append("后台摘要：" + "；".join([f"{b.name}{b.score:.1f}" for b in summary_blocks[:7]]))
    return "\n".join(lines)


def make_summary_message(candidates: List[USCandidate], meta: Dict[str, Any]) -> str:
    data_day = meta.get("data_day", "")
    lines = []
    lines.append(f"<b>美股一号员工 {MODEL_VERSION}</b>")
    lines.append(f"数据日期：{html_escape(data_day)}（仅完整常规交易日，不含盘前/盘后）")
    lines.append(f"股票池：source={html_escape(meta.get('source',''))} raw={meta.get('raw_count')} after_filter={meta.get('after_type_filter')} scanned={meta.get('scanned')} deep={meta.get('deep_scored')}")
    lines.append(f"正式推送：前{US_RESULT_LIMIT}只；Universe已排除ETF/权证/Unit/Preferred/SPAC/OTC/1美元以下等")
    if not candidates:
        lines.append("本次没有达到正式阈值的美股候选。")
        return "\n".join(lines)
    for i, c in enumerate(candidates[:US_RESULT_LIMIT], 1):
        lines.append("\n" + "=" * 18)
        lines.append(f"{i}. " + c.report)
    return "\n".join(lines)


def load_benchmarks(last_complete_day: date) -> Dict[str, pd.DataFrame]:
    out = {}
    if US_ENABLE_BENCHMARK_RS != "1":
        return out
    for b in US_BENCHMARKS:
        try:
            df = fetch_us_kline(b, US_BASE_LOOKBACK_DAYS, "base", last_complete_day)
            out[b] = add_indicators(df)
        except Exception as e:
            print(f"[benchmark] {b} failed: {e}")
    return out


def scan_us_stocks() -> Tuple[List[USCandidate], Dict[str, Any]]:
    ensure_dirs()
    last_complete_day = get_last_complete_us_trade_date()
    symbols, meta = load_us_universe()
    meta["data_day"] = str(last_complete_day)
    risk_flags = load_us_risk_flags(US_RISK_FLAGS_FILE)
    benchmarks = load_benchmarks(last_complete_day)

    base_candidates = []
    scanned = success = failed = risk_excluded = 0
    t0 = time.time()
    print(f"[start] {MODEL_VERSION} data_day={last_complete_day} universe={len(symbols)} source={meta.get('source')}")

    for idx, info in enumerate(symbols, 1):
        scanned += 1
        if US_MAX_STOCKS > 0 and scanned > US_MAX_STOCKS:
            break
        try:
            df = fetch_us_kline(info.symbol, US_BASE_LOOKBACK_DAYS, "base", last_complete_day)
            df = add_indicators(df)
            if len(df) < 120:
                continue
            latest = latest_row(df)
            risk = major_risk_filter(info.symbol, info, latest, df, risk_flags)
            if risk.status == "exclude":
                risk_excluded += 1
                continue
            base_score, blocks, diag = score_base_candidate(df, info, risk)
            # 基础闸门：保留强触发、强活跃、时间/平量种子，避免只因分数低错过。
            has_trigger = any(b.name == "日线触发" and b.score >= 4 for b in blocks)
            has_seed = any(b.name in ("平量压缩", "台阶量能", "1000日窗口") and b.score >= 2 for b in blocks)
            if base_score >= US_BASE_GATE_SCORE or has_trigger or has_seed:
                base_candidates.append({"info": info, "base_score": base_score, "risk": risk})
            success += 1
        except Exception as e:
            failed += 1
            if failed <= 10:
                print(f"[fetch/base] {info.symbol} failed: {e}")
        if idx % 100 == 0:
            elapsed = time.time() - t0
            print(f"[progress] {idx}/{len(symbols)} success={success} base_candidates={len(base_candidates)} risk_excluded={risk_excluded} elapsed={elapsed/60:.1f}m")
        time.sleep(US_REQUEST_SLEEP)

    # 基础候选排序后深算。
    base_candidates.sort(key=lambda x: x["base_score"], reverse=True)
    deep_targets = base_candidates[:US_DEEP_SCORE_LIMIT]
    deep_candidates: List[USCandidate] = []
    for j, item in enumerate(deep_targets, 1):
        info = item["info"]
        try:
            df = fetch_us_kline(info.symbol, US_DEEP_LOOKBACK_DAYS, "deep", last_complete_day)
            if len(df) < 180:
                continue
            cand = deep_score_candidate(df, info, item["risk"], benchmarks)
            deep_candidates.append(cand)
        except Exception as e:
            print(f"[deep] {info.symbol} failed: {e}")
        if j % 20 == 0:
            print(f"[deep_progress] {j}/{len(deep_targets)} candidates={len(deep_candidates)}")
        time.sleep(US_REQUEST_SLEEP)

    formal = [c for c in deep_candidates if c.pool == "优先候选池" and c.final_score >= US_FINAL_SCORE_THRESHOLD]
    formal.sort(key=lambda c: c.final_score, reverse=True)
    meta.update({
        "scanned": scanned,
        "success": success,
        "failed": failed,
        "risk_excluded": risk_excluded,
        "base_candidates": len(base_candidates),
        "deep_scored": len(deep_candidates),
        "formal": len(formal),
        "elapsed_seconds": round(time.time() - t0, 1),
    })
    save_json(US_CANDIDATE_FILE, {"meta": meta, "formal": [asdict(c) for c in formal], "all_deep": [asdict(c) for c in deep_candidates[:200]]})
    # 种子池保存后台跟踪票。
    seeds = [asdict(c) for c in deep_candidates if c.pool in ("后台跟踪池", "优先候选池")]
    save_json(US_SEED_POOL_FILE, {"updated_at": datetime.now(UTC_TZ).isoformat(), "data_day": str(last_complete_day), "seeds": seeds[:300]})
    return formal[:US_RESULT_LIMIT], meta


def main():
    try:
        formal, meta = scan_us_stocks()
        msg = make_summary_message(formal, meta)
        print(msg)
        send_telegram(msg)
        print(f"[done] formal={len(formal)} elapsed={meta.get('elapsed_seconds')}s file={US_CANDIDATE_FILE}")
    except Exception as e:
        err = f"美股一号员工 {MODEL_VERSION} 运行失败：{e}"
        print(err)
        send_telegram(html_escape(err))
        raise


if __name__ == "__main__":
    main()
