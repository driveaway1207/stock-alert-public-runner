# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys
import time
import traceback
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

warnings.filterwarnings(
    "ignore",
    message="Downcasting object dtype arrays on .fillna, .ffill, .bfill is deprecated.*",
    category=FutureWarning,
)
try:
    pd.set_option("future.no_silent_downcasting", True)
except Exception:
    pass

try:
    import baostock as bs
except Exception:
    bs = None

VERSION = "灵动-v10-telegram-top5-cache-target-day-refresh-only"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "artifacts"
OUTPUT_CSV = REPORT_DIR / "lingdong_latest.csv"
OUTPUT_JSON = REPORT_DIR / "lingdong_latest.json"
OUTPUT_MD = REPORT_DIR / "lingdong_report.md"
SELF_CHECK_JSON = REPORT_DIR / "lingdong_self_check.json"

# 公共日K缓存池：对齐员工三号思路。谁先生成日K缓存，灵动就直接复用。
MAIN_CACHE_DIR = ROOT / "kline_cache"
CACHE_DIRS = [
    MAIN_CACHE_DIR,
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]

MAX_STOCKS = int(os.getenv("LINGDONG_MAX_STOCKS", os.getenv("MAX_STOCKS", "0")) or "0")
PROGRESS_EVERY = int(os.getenv("LINGDONG_PROGRESS_EVERY", "200"))
CACHE_SCAN_PROGRESS_EVERY = int(os.getenv("LINGDONG_CACHE_SCAN_PROGRESS_EVERY", "800"))
REPORT_TOP_N = int(os.getenv("LINGDONG_REPORT_TOP_N", "5"))
REPORT_MAX_CHARS = int(os.getenv("LINGDONG_REPORT_MAX_CHARS", "3500"))

LOOKBACK_DAYS = int(os.getenv("LINGDONG_LOOKBACK_DAYS", "100"))
RECENT_DAYS = int(os.getenv("LINGDONG_RECENT_DAYS", "20"))
MID_DAYS = int(os.getenv("LINGDONG_MID_DAYS", "60"))
MIN_HISTORY_DAYS = int(os.getenv("LINGDONG_MIN_HISTORY_DAYS", "120"))
MIN_CACHE_ROWS = int(os.getenv("LINGDONG_MIN_CACHE_ROWS", str(MIN_HISTORY_DAYS)))

# 默认禁止全市场逐票BaoStock扫。只允许公共缓存；需要补今天时显式打开。
ALLOW_BAOSTOCK_FALLBACK = os.getenv("LINGDONG_ALLOW_BAOSTOCK_FALLBACK", "0") == "1"
# 关键修正：补数据只补目标交易日这一根，不从2020或最近10天重拉。
REFRESH_TARGET_DAY_ONLY = os.getenv("LINGDONG_REFRESH_TARGET_DAY_ONLY", "1") != "0"
QFQ_ADJUSTFLAG = "2"

AMOUNT20_LOW = float(os.getenv("LINGDONG_AMOUNT20_LOW", "30000000"))
AMOUNT20_BASIC = float(os.getenv("LINGDONG_AMOUNT20_BASIC", "50000000"))
AMOUNT20_GOOD = float(os.getenv("LINGDONG_AMOUNT20_GOOD", "100000000"))

BIG_BULL7_PCT = float(os.getenv("LINGDONG_BIG_BULL7_PCT", "7.0"))
BIG_YANG5_PCT = float(os.getenv("LINGDONG_BIG_YANG5_PCT", "5.0"))
BIG_YIN5_PCT = float(os.getenv("LINGDONG_BIG_YIN5_PCT", "-5.0"))
GAP_PCT = float(os.getenv("LINGDONG_GAP_PCT", "1.0"))
DEAD_RANGE20_MAX = float(os.getenv("LINGDONG_DEAD_RANGE20_MAX", "2.5"))
DEAD_SMALL_BODY_RATIO_MIN = float(os.getenv("LINGDONG_DEAD_SMALL_BODY_RATIO_MIN", "0.55"))
BAD_BIG_YIN_EXCESS = int(os.getenv("LINGDONG_BAD_BIG_YIN_EXCESS", "2"))
BAD_VOL_LONG_BEAR_20 = int(os.getenv("LINGDONG_BAD_VOL_LONG_BEAR_20", "2"))

TARGET_KEYS = [
    "LINGDONG_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "TARGET_TRADE_DATE",
    "LAST_TRADE_DAY_OVERRIDE",
    "REQUIRED_CACHE_DATE",
]

BLOCK_NAME = (
    "指数", "B股指数", "A股指数", "综合指数", "成份指数",
    "基金", "ETF", "LOF", "REIT",
    "债", "转债", "国债", "企债", "可转债",
    "期货", "期权", "认购", "认沽", "CWB",
)

GOOD_ACTIVE = "灵动充沛"
NORMAL_ACTIVE = "灵动尚可"
DEAD_ACTIVE = "死水无灵"
BAD_ACTIVE = "邪动乱流"
LOW_LIQUIDITY = "灵气枯竭"
DATA_SHORT = "样本不足"

STATUS_ORDER = {
    GOOD_ACTIVE: 0,
    NORMAL_ACTIVE: 1,
    BAD_ACTIVE: 2,
    DEAD_ACTIVE: 3,
    LOW_LIQUIDITY: 4,
    DATA_SHORT: 5,
}

@dataclass
class StockItem:
    code: str
    bs_code: str
    name: str


@dataclass
class LingdongHit:
    code: str
    name: str
    status: str
    latest_trade_day: str
    amount20: float
    amount60: float
    amount_ratio_20_60: float
    limitup_count_100: int
    big_bull7_count_100: int
    big_yang5_count_100: int
    big_yin5_count_100: int
    gap_up_count_100: int
    gap_down_count_100: int
    range20_pct: float
    small_body_ratio_60: float
    volume_long_bear_20: int
    long_upper_count_100: int
    trend_efficiency_20: float
    attack_memory: bool
    bad_activity: bool
    dead_activity: bool
    detail: str


@dataclass
class ScanStat:
    version: str
    target_date: str
    stock_pool_count: int
    scanned_count: int
    daily_success_count: int
    failed_count: int
    signal_count: int
    data_source: str
    cache_files: int = 0
    cache_hit_count: int = 0
    cache_bad_count: int = 0
    cache_short_count: int = 0
    stale_count: int = 0
    refreshed_count: int = 0
    refresh_failed_count: int = 0
    baostock_fallback_enabled: bool = False


def bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return default


def code6(value: Any) -> str:
    raw = value.stem if isinstance(value, Path) else s(value)
    match = re.search(r"(\d{6})", raw)
    return match.group(1) if match else ""


def valid_code(code: str) -> bool:
    c = code6(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def norm_date(value: Any) -> str:
    raw = (
        s(value)
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
        .replace("_", "-")
    )
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def prev_workday(day: datetime) -> datetime:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def target_date() -> str:
    for key in TARGET_KEYS:
        value = os.getenv(key)
        if value:
            parsed = norm_date(value)
            if parsed:
                return parsed

    now = bj_now()
    if now.weekday() >= 5 or now.hour < 20 or (now.hour == 20 and now.minute < 35):
        now = prev_workday(now - timedelta(days=1))
    return now.strftime("%Y-%m-%d")


TARGET_DASH = target_date()


def bs_code_of(code: str) -> str:
    c = code6(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("8", "4", "920")):
        return "bj." + c
    return ""


def exchange_stock_ok(bs_code: str, code: str) -> bool:
    raw = s(bs_code).lower()
    c = code6(code or bs_code)
    if not c:
        return False

    if raw.startswith("sh."):
        return c.startswith(("600", "601", "603", "605", "688", "689"))
    if raw.startswith("sz."):
        return c.startswith(("000", "001", "002", "003", "300", "301"))
    if raw.startswith("bj."):
        return c.startswith(("8", "4", "920"))

    return bool(bs_code_of(c))


def name_stock_ok(name: str) -> bool:
    upper = s(name).upper()
    return not any(word in upper for word in BLOCK_NAME)


def common_stock_ok(bs_code: str, code: str, name: str) -> bool:
    return exchange_stock_ok(bs_code, code) and name_stock_ok(name)


def ensure_report_dir() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def bs_rows(rs: Any) -> List[List[str]]:
    rows: List[List[str]] = []
    while rs is not None and getattr(rs, "error_code", "0") == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rows


def normalize_hist(df: pd.DataFrame, default_code: str = "") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    mp = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "股票代码": "code", "证券代码": "code", "code": "code", "symbol": "code",
        "名称": "name", "股票名称": "name", "股票简称": "name", "证券简称": "name", "name": "name", "code_name": "name",
        "开盘": "open", "开盘价": "open", "open": "open",
        "最高": "high", "最高价": "high", "high": "high",
        "最低": "low", "最低价": "low", "low": "low",
        "收盘": "close", "收盘价": "close", "close": "close",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "涨幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg",
        "换手率": "turnover", "turn": "turnover", "turnover": "turnover",
    }
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()

    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()

    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col].map(f), errors="coerce").fillna(0.0).astype(float)

    if "volume" not in d.columns:
        d["volume"] = 0.0
    if "amount" not in d.columns:
        d["amount"] = 0.0
    if "pct_chg" not in d.columns:
        d["pct_chg"] = 0.0
    if "turnover" not in d.columns:
        d["turnover"] = 0.0
    if "code" not in d.columns:
        d["code"] = default_code
    if "name" not in d.columns:
        d["name"] = ""

    d["date"] = d["date"].map(norm_date)
    d["code"] = d["code"].map(lambda x: code6(x) or code6(default_code))
    d.loc[d["code"].astype(str).str.len() == 0, "code"] = code6(default_code)
    d["name"] = d["name"].map(s)

    d = d[
        (d["date"] != "")
        & (d["open"] > 0)
        & (d["high"] > 0)
        & (d["low"] > 0)
        & (d["close"] > 0)
    ].copy()

    if TARGET_DASH:
        d = d[d["date"] <= TARGET_DASH].copy()

    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)

    if d.empty:
        return pd.DataFrame()

    if float(d["pct_chg"].abs().sum()) == 0:
        prev = d["close"].shift(1)
        d["pct_chg"] = (d["close"] / prev.replace(0, pd.NA) - 1.0) * 100.0
        d["pct_chg"] = pd.to_numeric(d["pct_chg"], errors="coerce").fillna(0.0).astype(float)

    return d[["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]].reset_index(drop=True)


def read_cache_file(path: Path) -> pd.DataFrame:
    code = code6(path)
    try:
        return normalize_hist(pd.read_csv(path), code)
    except Exception:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            rows = obj.get("rows") or obj.get("data") or obj.get("klines") or obj.get("records") or []
            return normalize_hist(pd.DataFrame(rows), code)
        except Exception:
            return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    seen: Dict[str, Path] = {}
    for directory in CACHE_DIRS:
        if not directory.exists():
            continue
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            code = code6(p)
            if valid_code(code) and code not in seen:
                seen[code] = p
    files = list(seen.values())
    if MAX_STOCKS > 0:
        files = files[:MAX_STOCKS]
    return files


def valid_stock_display_name(code: Any, name: Any) -> bool:
    c = code6(code)
    n = s(name)
    if not n:
        return False
    low = n.lower()
    bad = {"nan", "none", "null", "名称待补", "名称缺失", "--", "-"}
    if n in bad or low in bad:
        return False
    digits = code6(n)
    if c and digits == c and re.sub(r"\D", "", n) in {c, "0" + c, "1" + c}:
        return False
    if c and n in {c, f"sh.{c}", f"sz.{c}", f"bj.{c}", f"SH.{c}", f"SZ.{c}", f"BJ.{c}"}:
        return False
    return True


def stock_display_name(code: Any, name: Any) -> str:
    c = code6(code)
    n = s(name)
    return n if valid_stock_display_name(c, n) else "名称待补"


def save_cache(code: str, df: pd.DataFrame) -> bool:
    d = normalize_hist(df, code)
    if d.empty or len(d) < MIN_CACHE_ROWS:
        return False
    MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    d.loc[d["code"].astype(str).str.len() == 0, "code"] = code
    cols = ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]
    out = MAIN_CACHE_DIR / f"{code}.csv"
    tmp = out.with_suffix(".csv.tmp")
    d[[c for c in cols if c in d.columns]].to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, out)
    return True


def load_public_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, int]]:
    files = iter_cache_files()
    hist: Dict[str, pd.DataFrame] = {}
    names: Dict[str, str] = {}
    stat = {"cache_files": len(files), "cache_hit": 0, "bad": 0, "short": 0}
    start = time.time()

    for idx, path in enumerate(files, 1):
        code = code6(path)
        df = read_cache_file(path)
        if df.empty:
            stat["bad"] += 1
        elif len(df) < MIN_CACHE_ROWS:
            stat["short"] += 1
        else:
            if code and "code" in df.columns:
                df.loc[df["code"].astype(str).str.len() == 0, "code"] = code
            if code and "name" in df.columns:
                names_found = [s(x) for x in df["name"].tolist() if valid_stock_display_name(code, x)]
                if names_found:
                    names[code] = names_found[-1]
            hist[code] = df
            stat["cache_hit"] += 1

        if idx == 1 or idx % max(1, CACHE_SCAN_PROGRESS_EVERY) == 0 or idx == len(files):
            elapsed = max(time.time() - start, 0.001)
            print(
                f"灵动公共日K缓存读取 {idx}/{len(files)}"
                f"｜命中{stat['cache_hit']}"
                f"｜坏{stat['bad']}"
                f"｜短{stat['short']}"
                f"｜速度{idx / elapsed:.2f}个/秒"
                f"｜当前{code}",
                flush=True,
            )
    return hist, names, stat


def fetch_target_day_only(code: str, existing: pd.DataFrame, target: str) -> Tuple[pd.DataFrame, bool]:
    """只补目标交易日这一根日K。绝不从2020或全历史重拉。"""
    if bs is None or not target:
        return existing, False

    fields = "date,code,open,high,low,close,volume,amount,pctChg,turn,tradestatus"
    rs = bs.query_history_k_data_plus(
        bs_code_of(code),
        fields,
        start_date=target,
        end_date=target,
        frequency="d",
        adjustflag=QFQ_ADJUSTFLAG,
    )
    rows = bs_rows(rs)
    if not rows:
        return existing, False

    fresh = pd.DataFrame(rows, columns=fields.split(","))
    if "tradestatus" in fresh.columns:
        fresh = fresh[fresh["tradestatus"].map(s).isin({"", "1"})].copy()
    fresh = fresh.rename(columns={"pctChg": "pct_chg", "turn": "turnover"})
    fresh = normalize_hist(fresh, code)
    if fresh.empty:
        return existing, False

    merged = normalize_hist(pd.concat([existing, fresh], ignore_index=True), code)
    return merged, bool(not merged.empty and s(merged.iloc[-1].get("date")) == target)


def get_limit_threshold(code: str) -> float:
    c = code6(code)
    if c.startswith(("300", "301", "688", "689")):
        return 19.3
    if c.startswith(("8", "4", "920")):
        return 29.0
    return 9.3


def add_lingdong_indicators(df: pd.DataFrame, code: str) -> pd.DataFrame:
    d = normalize_hist(df, code).copy().reset_index(drop=True)
    if d.empty:
        return d

    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col not in d.columns:
            d[col] = 0.0
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0.0).astype(float)

    d["prev_close"] = d["close"].shift(1)
    d.loc[d["prev_close"] <= 0, "prev_close"] = pd.NA
    d["ret_pct"] = d["pct_chg"].astype(float)
    missing_ret = d["ret_pct"].abs() <= 1e-12
    d.loc[missing_ret, "ret_pct"] = (d.loc[missing_ret, "close"] / d.loc[missing_ret, "prev_close"] - 1.0) * 100.0
    d["ret_pct"] = pd.to_numeric(d["ret_pct"], errors="coerce").fillna(0.0).astype(float)

    denom = d["prev_close"].replace(0, pd.NA)
    d["range_pct"] = pd.to_numeric((d["high"] - d["low"]) / denom * 100.0, errors="coerce").fillna(0.0).astype(float)
    d["body_pct_prev"] = pd.to_numeric((d["close"] - d["open"]) / denom * 100.0, errors="coerce").fillna(0.0).astype(float)
    d["abs_body_pct"] = d["body_pct_prev"].abs()
    rng = (d["high"] - d["low"]).replace(0, pd.NA)
    d["close_pos"] = pd.to_numeric((d["close"] - d["low"]) / rng, errors="coerce").fillna(0.5).astype(float)
    d["body_ratio"] = pd.to_numeric((d["close"] - d["open"]).abs() / rng, errors="coerce").fillna(0.0).astype(float)
    d["upper_shadow_ratio"] = pd.to_numeric((d["high"] - d[["open", "close"]].max(axis=1)) / rng, errors="coerce").fillna(0.0).astype(float)
    d["lower_shadow_ratio"] = pd.to_numeric((d[["open", "close"]].min(axis=1) - d["low"]) / rng, errors="coerce").fillna(0.0).astype(float)
    d["is_yang"] = d["close"] > d["open"]
    d["is_yin"] = d["close"] < d["open"]
    d["vol_ma20"] = d["volume"].rolling(20, min_periods=5).mean()
    d["amount_ma20"] = d["amount"].rolling(20, min_periods=5).mean()
    d["limit_threshold"] = get_limit_threshold(code)
    d["limit_up"] = d["ret_pct"] >= d["limit_threshold"]
    d["big_bull7"] = (
        (d["ret_pct"] >= BIG_BULL7_PCT)
        & d["is_yang"]
        & (d["close_pos"] >= 0.60)
        & (d["upper_shadow_ratio"] <= 0.45)
    )
    d["big_yang5"] = (
        (d["ret_pct"] >= BIG_YANG5_PCT)
        & d["is_yang"]
        & (d["close_pos"] >= 0.55)
    )
    d["big_yin5"] = (
        (d["ret_pct"] <= BIG_YIN5_PCT)
        | ((d["body_pct_prev"] <= BIG_YIN5_PCT) & d["is_yin"] & (d["close_pos"] <= 0.45))
    )
    d["gap_up"] = d["open"] >= d["high"].shift(1) * (1.0 + GAP_PCT / 100.0)
    d["gap_down"] = d["open"] <= d["low"].shift(1) * (1.0 - GAP_PCT / 100.0)
    vol_ref = d["vol_ma20"].fillna(float(d["volume"].median()) if len(d) else 0.0)
    d["volume_long_bear"] = (
        d["is_yin"]
        & (d["abs_body_pct"] >= 4.0)
        & (d["close_pos"] <= 0.35)
        & (d["volume"] >= vol_ref * 1.20)
    )
    d["long_upper_reversal"] = (
        pd.to_numeric(d["high"] / d["prev_close"].replace(0, pd.NA) - 1.0, errors="coerce").fillna(0.0) >= 0.05
    ) & (d["close_pos"] <= 0.45) & (d["upper_shadow_ratio"] >= 0.45)
    d["small_body_narrow"] = (d["abs_body_pct"] <= 1.2) & (d["range_pct"] <= 3.0)
    return d


def trend_efficiency(d: pd.DataFrame, days: int = 20) -> float:
    if d is None or len(d) < days + 1:
        return 0.0
    seg = d.tail(days + 1).copy().reset_index(drop=True)
    start = f(seg.iloc[0].get("close"))
    end = f(seg.iloc[-1].get("close"))
    if start <= 0 or end <= 0:
        return 0.0
    net = abs(end / start - 1.0)
    rets = pd.to_numeric(seg["close"].pct_change(), errors="coerce").abs().dropna()
    path = float(rets.sum()) if len(rets) else 0.0
    if path <= 0:
        return 0.0
    return round(max(0.0, min(1.0, net / path)), 3)


def evaluate_lingdong(df: pd.DataFrame, item: StockItem, target: str = TARGET_DASH) -> LingdongHit:
    d0 = normalize_hist(df, item.code)
    if d0.empty or len(d0) < MIN_HISTORY_DAYS:
        return LingdongHit(
            code=item.code, name=item.name, status=DATA_SHORT,
            latest_trade_day=s(d0.iloc[-1].get("date")) if not d0.empty else "",
            amount20=0.0, amount60=0.0, amount_ratio_20_60=0.0,
            limitup_count_100=0, big_bull7_count_100=0, big_yang5_count_100=0, big_yin5_count_100=0,
            gap_up_count_100=0, gap_down_count_100=0, range20_pct=0.0, small_body_ratio_60=0.0,
            volume_long_bear_20=0, long_upper_count_100=0, trend_efficiency_20=0.0,
            attack_memory=False, bad_activity=False, dead_activity=False, detail="日K样本不足",
        )

    d = add_lingdong_indicators(d0, item.code)
    if d.empty or len(d) < MIN_HISTORY_DAYS:
        return LingdongHit(
            code=item.code, name=item.name, status=DATA_SHORT,
            latest_trade_day=s(d.iloc[-1].get("date")) if not d.empty else "",
            amount20=0.0, amount60=0.0, amount_ratio_20_60=0.0,
            limitup_count_100=0, big_bull7_count_100=0, big_yang5_count_100=0, big_yin5_count_100=0,
            gap_up_count_100=0, gap_down_count_100=0, range20_pct=0.0, small_body_ratio_60=0.0,
            volume_long_bear_20=0, long_upper_count_100=0, trend_efficiency_20=0.0,
            attack_memory=False, bad_activity=False, dead_activity=False, detail="日K有效样本不足",
        )

    w100 = d.tail(LOOKBACK_DAYS).copy()
    w60 = d.tail(MID_DAYS).copy()
    w20 = d.tail(RECENT_DAYS).copy()

    amount20 = float(w20["amount"].replace(0, pd.NA).dropna().mean()) if len(w20) else 0.0
    amount60 = float(w60["amount"].replace(0, pd.NA).dropna().mean()) if len(w60) else 0.0
    amount_ratio = amount20 / amount60 if amount60 > 0 and amount20 > 0 else 0.0

    limitups = int(w100["limit_up"].sum())
    big_bull7 = int(w100["big_bull7"].sum())
    big_yang5 = int(w100["big_yang5"].sum())
    big_yin5 = int(w100["big_yin5"].sum())
    gap_up = int(w100["gap_up"].sum())
    gap_down = int(w100["gap_down"].sum())
    range20 = float(w20["range_pct"].median()) if len(w20) else 0.0
    small_body_ratio = float(w60["small_body_narrow"].mean()) if len(w60) else 0.0
    vol_long_bear20 = int(w20["volume_long_bear"].sum())
    long_upper100 = int(w100["long_upper_reversal"].sum())
    eff20 = trend_efficiency(d, RECENT_DAYS)

    attack_memory = bool(limitups >= 1 or big_bull7 >= 2 or big_yang5 >= 4 or gap_up >= 2)
    bad_activity = bool(
        big_yin5 > big_yang5 + BAD_BIG_YIN_EXCESS
        or vol_long_bear20 >= BAD_VOL_LONG_BEAR_20
        or (long_upper100 >= 6 and big_yang5 <= big_yin5)
    )
    dead_activity = bool(
        limitups == 0
        and big_bull7 < 2
        and big_yang5 < 3
        and gap_up < 2
        and range20 < DEAD_RANGE20_MAX
        and small_body_ratio >= DEAD_SMALL_BODY_RATIO_MIN
    )

    reasons: List[str] = []
    if amount20 < AMOUNT20_LOW:
        status = LOW_LIQUIDITY
        reasons.append(f"20日均成交额{amount20/1e8:.2f}亿低于底线")
    elif bad_activity:
        status = BAD_ACTIVE
        if big_yin5 > big_yang5 + BAD_BIG_YIN_EXCESS:
            reasons.append(f"100日大阴{big_yin5}次明显多于大阳{big_yang5}次")
        if vol_long_bear20 >= BAD_VOL_LONG_BEAR_20:
            reasons.append(f"20日放量长阴{vol_long_bear20}次")
        if long_upper100 >= 6 and big_yang5 <= big_yin5:
            reasons.append(f"100日长上影冲高回落{long_upper100}次")
    elif attack_memory and amount20 >= AMOUNT20_BASIC:
        status = GOOD_ACTIVE
        reasons.append("具备攻击记忆")
    elif dead_activity:
        status = DEAD_ACTIVE
        reasons.append("缺少攻击记忆且近期振幅/实体偏窄")
    else:
        status = NORMAL_ACTIVE
        reasons.append("普通可交易活性")

    if attack_memory:
        reasons.append(f"涨停{limitups}次/7%大阳{big_bull7}次/5%大阳{big_yang5}次/向上缺口{gap_up}次")
    if amount_ratio > 0:
        reasons.append(f"20/60日成交额比{amount_ratio:.2f}")
    reasons.append(f"20日中位振幅{range20:.2f}%")
    reasons.append(f"60日小实体窄振幅比例{small_body_ratio:.0%}")
    reasons.append(f"方向效率20日{eff20:.2f}")
    latest_day = s(d.iloc[-1].get("date"))
    if target and latest_day != target:
        reasons.append(f"缓存未覆盖目标日，当前按{latest_day}股性参考")

    return LingdongHit(
        code=item.code,
        name=item.name,
        status=status,
        latest_trade_day=latest_day,
        amount20=round(amount20, 2),
        amount60=round(amount60, 2),
        amount_ratio_20_60=round(amount_ratio, 3),
        limitup_count_100=limitups,
        big_bull7_count_100=big_bull7,
        big_yang5_count_100=big_yang5,
        big_yin5_count_100=big_yin5,
        gap_up_count_100=gap_up,
        gap_down_count_100=gap_down,
        range20_pct=round(range20, 3),
        small_body_ratio_60=round(small_body_ratio, 3),
        volume_long_bear_20=vol_long_bear20,
        long_upper_count_100=long_upper100,
        trend_efficiency_20=eff20,
        attack_memory=attack_memory,
        bad_activity=bad_activity,
        dead_activity=dead_activity,
        detail="；".join(reasons),
    )


def is_report_signal(hit: LingdongHit) -> bool:
    # Telegram正式报告只推真正有攻击记忆的“灵动充沛”。
    # “灵动尚可”只保留在JSON统计/全量结果里，避免消息过长和普通票刷屏。
    return hit.status == GOOD_ACTIVE


def sort_hits(hits: List[LingdongHit]) -> List[LingdongHit]:
    return sorted(
        hits,
        key=lambda x: (
            STATUS_ORDER.get(x.status, 99),
            -x.big_bull7_count_100,
            -x.limitup_count_100,
            -x.big_yang5_count_100,
            -x.amount_ratio_20_60,
            -x.amount20,
            x.code,
        ),
    )


def _telegram_safe_text(lines: List[str], max_chars: int = REPORT_MAX_CHARS) -> str:
    limit = max(1200, min(int(max_chars), 3900))
    out: List[str] = []
    total = 0
    for line in lines:
        item = s(line)
        add_len = len(item) + (1 if out else 0)
        if out and total + add_len > limit:
            out.append("……消息已截断，完整明细见 CSV/JSON artifact")
            break
        out.append(item)
        total += add_len
    return "\n".join(out).strip()


def build_report_text(
    hits: List[LingdongHit],
    all_results: Optional[List[LingdongHit]] = None,
    stat: Optional[ScanStat] = None,
) -> str:
    # Telegram有4096字符限制。主报告只做“可读摘要 + 灵动充沛Top 5”，
    # 全字段明细继续放在 CSV/JSON，避免 send telegram result 报 message is too long。
    rows = all_results if all_results is not None else hits
    counts = status_counts(rows) if rows else {}
    good_hits = [x for x in hits if x.status == GOOD_ACTIVE]

    if not good_hits and all_results is None and stat is None:
        return "无符合灵动充沛条件股票"

    lines: List[str] = []
    lines.append("【灵动｜日K股性活跃度】")
    if stat is not None:
        stale_note = f"｜过期{stat.stale_count}" if stat.stale_count else ""
        refresh_note = f"｜补今{stat.refreshed_count}" if stat.refreshed_count else ""
        lines.append(
            f"目标日{stat.target_date}｜缓存命中{stat.cache_hit_count}/{stat.cache_files}"
            f"｜扫描{stat.daily_success_count}{stale_note}{refresh_note}"
        )

    lines.append(
        f"灵动充沛{counts.get(GOOD_ACTIVE, 0)}｜灵动尚可{counts.get(NORMAL_ACTIVE, 0)}｜"
        f"邪动乱流{counts.get(BAD_ACTIVE, 0)}｜死水无灵{counts.get(DEAD_ACTIVE, 0)}｜"
        f"灵气枯竭{counts.get(LOW_LIQUIDITY, 0)}｜样本不足{counts.get(DATA_SHORT, 0)}"
    )

    if not good_hits:
        lines.append("无符合灵动充沛条件股票；普通活性票详见 CSV/JSON")
        return _telegram_safe_text(lines)

    top_n = max(1, int(REPORT_TOP_N))
    top = good_hits[:top_n]
    lines.append(f"【灵动充沛 Top {len(top)}/{len(good_hits)}】")
    for i, x in enumerate(top, 1):
        lines.append(
            f"{i}. {x.code} {x.name}｜7%阳{x.big_bull7_count_100}｜涨停{x.limitup_count_100}｜"
            f"5%阳{x.big_yang5_count_100}｜5%阴{x.big_yin5_count_100}｜20日额{x.amount20/1e8:.2f}亿"
        )

    if len(good_hits) > len(top):
        lines.append(f"其余{len(good_hits) - len(top)}只灵动充沛详见 CSV/JSON artifact")

    return _telegram_safe_text(lines)


def status_counts(results: List[LingdongHit]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in results:
        out[x.status] = out.get(x.status, 0) + 1
    return out


def write_outputs(all_results: List[LingdongHit], stat: ScanStat, failures: List[Dict[str, str]]) -> None:
    ensure_report_dir()
    all_rows = [asdict(x) for x in all_results]
    signal_rows = [asdict(x) for x in all_results if is_report_signal(x)]

    columns = [
        "code", "name", "status", "latest_trade_day",
        "amount20", "amount60", "amount_ratio_20_60",
        "limitup_count_100", "big_bull7_count_100", "big_yang5_count_100", "big_yin5_count_100",
        "gap_up_count_100", "gap_down_count_100", "range20_pct", "small_body_ratio_60",
        "volume_long_bear_20", "long_upper_count_100", "trend_efficiency_20",
        "attack_memory", "bad_activity", "dead_activity", "detail",
    ]
    pd.DataFrame(signal_rows, columns=columns).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    payload = {
        "summary": asdict(stat),
        "signals": signal_rows,
        "all_status_count": status_counts(all_results),
        "all_results": all_rows[:1000],
        "failures": failures[:300],
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(build_report_text([x for x in all_results if is_report_signal(x)], all_results, stat).rstrip() + "\n", encoding="utf-8")


def run_scan(limit: int = 0) -> int:
    ensure_report_dir()

    hist, names, cache_stat = load_public_cache()
    if limit and limit > 0:
        hist = dict(list(hist.items())[:limit])

    if not hist:
        raise RuntimeError("公共日K缓存为空，灵动禁止全市场慢拉；请先运行任一日K员工生成kline_cache")

    refresh_enabled = bool(ALLOW_BAOSTOCK_FALLBACK and bs is not None)
    logged_in = False
    if refresh_enabled:
        login_result = bs.login()
        logged_in = getattr(login_result, "error_code", "") == "0"
        if not logged_in:
            print(
                f"baostock登录失败，跳过目标日补拉: {getattr(login_result, 'error_code', '')} {getattr(login_result, 'error_msg', '')}",
                flush=True,
            )
    try:
        results: List[LingdongHit] = []
        failures: List[Dict[str, str]] = []
        daily_success = 0
        stale_count = 0
        refreshed_count = 0
        refresh_failed_count = 0
        start = time.time()
        items = list(hist.items())

        for idx, (code, cached) in enumerate(items, 1):
            try:
                daily = normalize_hist(cached, code)
                if daily.empty:
                    failures.append({"code": code, "name": names.get(code, "名称待补"), "error": "缓存日K为空"})
                    continue

                latest = s(daily.iloc[-1].get("date"))
                if TARGET_DASH and latest != TARGET_DASH:
                    stale_count += 1
                    # 只补目标日这一根。补不到就继续用缓存旧日作为股性参考，绝不全市场全历史慢拉。
                    if refresh_enabled and logged_in:
                        try:
                            merged, ok = fetch_target_day_only(code, daily, TARGET_DASH)
                            if ok:
                                daily = merged
                                latest = s(daily.iloc[-1].get("date"))
                                save_cache(code, daily)
                                refreshed_count += 1
                            else:
                                refresh_failed_count += 1
                        except Exception:
                            refresh_failed_count += 1

                name = stock_display_name(code, names.get(code, ""))
                if name == "名称待补" and "name" in daily.columns:
                    vals = [s(x) for x in daily["name"].tolist() if valid_stock_display_name(code, x)]
                    if vals:
                        name = vals[-1]

                item = StockItem(code=code, bs_code=bs_code_of(code), name=name)
                daily_success += 1
                hit = evaluate_lingdong(daily, item, TARGET_DASH)
                results.append(hit)

            except Exception as exc:
                failures.append({"code": code, "name": names.get(code, "名称待补"), "error": str(exc)[:180]})

            if idx == 1 or idx % max(1, PROGRESS_EVERY) == 0 or idx == len(items):
                elapsed = max(time.time() - start, 0.001)
                print(
                    f"灵动日K缓存扫描 {idx}/{len(items)}"
                    f"｜日K成功{daily_success}"
                    f"｜信号{sum(1 for x in results if is_report_signal(x))}"
                    f"｜过期{stale_count}"
                    f"｜补今{refreshed_count}"
                    f"｜补失败{refresh_failed_count}"
                    f"｜失败{len(failures)}"
                    f"｜速度{idx / elapsed:.2f}只/秒"
                    f"｜当前{code} {names.get(code, '名称待补')}",
                    flush=True,
                )

        if daily_success == 0:
            raise RuntimeError("公共缓存可用日K数量为0，不能伪装成无符合灵动股票")

        results = sort_hits(results)
        signal_count = sum(1 for x in results if is_report_signal(x))

        stat = ScanStat(
            version=VERSION,
            target_date=TARGET_DASH,
            stock_pool_count=len(items),
            scanned_count=len(items),
            daily_success_count=daily_success,
            failed_count=len(failures),
            signal_count=signal_count,
            data_source="public_kline_cache_first_target_day_only_refresh_d_qfq_activity_label",
            cache_files=int(cache_stat.get("cache_files", 0)),
            cache_hit_count=int(cache_stat.get("cache_hit", 0)),
            cache_bad_count=int(cache_stat.get("bad", 0)),
            cache_short_count=int(cache_stat.get("short", 0)),
            stale_count=stale_count,
            refreshed_count=refreshed_count,
            refresh_failed_count=refresh_failed_count,
            baostock_fallback_enabled=bool(refresh_enabled),
        )

        write_outputs(results, stat, failures)
        print(json.dumps(asdict(stat), ensure_ascii=False, indent=2), flush=True)
        print(json.dumps(status_counts(results), ensure_ascii=False, indent=2), flush=True)
        return 0
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:
                pass


def synthetic_daily(rows: List[Tuple[Any, ...]], code: str = "000001") -> pd.DataFrame:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if len(row) == 5:
            d, o, h, l, c = row
            volume = 1000000.0
            amount = c * volume
            pct = 0.0
        elif len(row) == 7:
            d, o, h, l, c, volume, amount = row
            pct = 0.0
        elif len(row) == 8:
            d, o, h, l, c, volume, amount, pct = row
        else:
            raise ValueError("synthetic row must have 5, 7 or 8 fields")
        normalized.append({"date": d, "code": code, "open": o, "high": h, "low": l, "close": c, "volume": volume, "amount": amount, "pct_chg": pct, "turnover": 0.0})
    df = pd.DataFrame(normalized)
    df["date"] = df["date"].map(norm_date)
    return normalize_hist(df, code)


def base_daily_rows(days: int = 130, start: str = "2025-01-01", price: float = 10.0, amount: float = 80000000.0) -> List[Tuple[Any, ...]]:
    base = pd.Timestamp(start)
    rows: List[Tuple[Any, ...]] = []
    cur = price
    trade_i = 0
    calendar_i = 0
    while trade_i < days:
        day = base + pd.Timedelta(days=calendar_i)
        calendar_i += 1
        if day.weekday() >= 5:
            continue
        open_ = cur * 0.998
        close = cur * 1.002
        high = max(open_, close) * 1.008
        low = min(open_, close) * 0.992
        volume = amount / max(close, 1e-9)
        rows.append((day.strftime("%Y-%m-%d"), round(open_, 3), round(high, 3), round(low, 3), round(close, 3), volume, amount))
        cur = close
        trade_i += 1
    return rows


def make_good_active_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=120000000.0)
    for idx, pct in [(80, 8.2), (105, 7.5), (115, 5.8), (122, 5.2)]:
        d, o, h, l, c, v, a = rows[idx]
        new_o = c
        new_c = c * (1 + pct / 100.0)
        rows[idx] = (d, round(new_o, 3), round(new_c * 1.01, 3), round(new_o * 0.995, 3), round(new_c, 3), v * 2.0, a * 2.0, pct)
    return synthetic_daily(rows)


def make_low_liquidity_rows() -> pd.DataFrame:
    return synthetic_daily(base_daily_rows(amount=12000000.0))


def make_dead_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=70000000.0)
    new_rows = []
    for d, o, h, l, c, v, a in rows:
        mid = c
        new_rows.append((d, mid * 0.999, mid * 1.006, mid * 0.994, mid * 1.001, v, a))
    return synthetic_daily(new_rows)


def make_bad_active_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=100000000.0)
    for idx in [111, 114, 118, 122, 126]:
        d, o, h, l, c, v, a = rows[idx]
        prev = c
        new_o = prev * 1.01
        new_c = prev * 0.94
        rows[idx] = (d, round(new_o, 3), round(new_o * 1.005, 3), round(new_c * 0.990, 3), round(new_c, 3), v * 2.4, a * 2.4, -6.0)
    return synthetic_daily(rows)


def make_normal_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=90000000.0)
    new_rows = []
    for idx, (d, o, h, l, c, v, a) in enumerate(rows):
        if idx % 4 == 0:
            new_rows.append((d, round(c * 0.995, 3), round(c * 1.028, 3), round(c * 0.982, 3), round(c * 1.006, 3), v, a))
        else:
            new_rows.append((d, round(c * 0.998, 3), round(c * 1.022, 3), round(c * 0.980, 3), round(c * 1.002, 3), v, a))
    return synthetic_daily(new_rows)


def self_check_once() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        items.append({"name": name, "ok": bool(ok), "detail": detail})

    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    add("source::public_cache_pool", "load_public_cache" in function_names and "MAIN_CACHE_DIR" in src, "必须优先读取公共日K缓存池")
    add("source::no_full_market_slow_sweep", ("query_" + "stock_pool") not in function_names, "生产扫描不得再先拿BaoStock全市场股票池逐票慢拉")
    add("source::target_day_only_refresh", "fetch_target_day_only" in function_names and 'start_date=target' in src, "补数据只能补目标交易日这一根")
    add("source::fallback_default_off", 'LINGDONG_ALLOW_BAOSTOCK_FALLBACK", "0"' in src, "BaoStock fallback默认关闭")
    add("source::daily_frequency", 'frequency="d"' in src, "如启用补拉，必须使用BaoStock日K frequency=d")
    add("source::qfq_adjustflag", 'adjustflag=QFQ_ADJUSTFLAG' in src and 'QFQ_ADJUSTFLAG = "2"' in src, "如启用补拉，必须使用前复权日K adjustflag=2")
    add("source::workflow_scan", "run_scan" in function_names and "write_outputs" in function_names, "必须保留扫描与三件套输出链路")
    add("source::activity_evaluator", "evaluate_lingdong" in function_names, "必须存在灵动评价函数")

    add("stock_pool::exclude_sh_000003_index", not common_stock_ok("sh.000003", "000003", "上证B股指数"), "必须剔除 sh.000003 上证B股指数")
    add("stock_pool::exclude_sh_000001_index", not common_stock_ok("sh.000001", "000001", "上证指数"), "必须剔除 sh.000001 上证指数")
    add("stock_pool::allow_sz_000001_stock", common_stock_ok("sz.000001", "000001", "平安银行"), "必须保留 sz.000001 普通股票")
    add("stock_pool::allow_sh_600000_stock", common_stock_ok("sh.600000", "600000", "浦发银行"), "必须保留 sh.600000 普通股票")

    item = StockItem("000001", "sz.000001", "测试股")
    good = evaluate_lingdong(make_good_active_rows(), item)
    add("rule::good_active_hit", good.status == GOOD_ACTIVE, f"状态={good.status}，详情={good.detail}")
    add("rule::good_active_has_attack_memory", good.attack_memory, f"7%阳={good.big_bull7_count_100}，5%阳={good.big_yang5_count_100}")

    low = evaluate_lingdong(make_low_liquidity_rows(), item)
    add("rule::low_liquidity", low.status == LOW_LIQUIDITY, f"状态={low.status}，20日额={low.amount20}")

    dead = evaluate_lingdong(make_dead_rows(), item)
    add("rule::dead_activity", dead.status == DEAD_ACTIVE, f"状态={dead.status}，振幅={dead.range20_pct}，小实体={dead.small_body_ratio_60}")

    bad = evaluate_lingdong(make_bad_active_rows(), item)
    add("rule::bad_active", bad.status == BAD_ACTIVE, f"状态={bad.status}，大阴={bad.big_yin5_count_100}，大阳={bad.big_yang5_count_100}，长阴={bad.volume_long_bear_20}")

    normal = evaluate_lingdong(make_normal_rows(), item)
    add("rule::normal_active", normal.status == NORMAL_ACTIVE, f"状态={normal.status}，详情={normal.detail}")

    unordered = [bad, normal, good, low, dead]
    sorted_status = [x.status for x in sort_hits(unordered)]
    add("output::sort_hits_status_priority", sorted_status[0] == GOOD_ACTIVE and sorted_status[-1] in {DATA_SHORT, LOW_LIQUIDITY}, f"排序={sorted_status}")

    report_text = build_report_text([good]).strip()
    add("output::report_contains_status", GOOD_ACTIVE in report_text and "000001" in report_text, f"报告内容={report_text!r}")

    many_hits = [good] * 200
    limited_text = build_report_text(many_hits, many_hits, None)
    add("output::telegram_safe_length", len(limited_text) <= REPORT_MAX_CHARS + 80, f"报告长度={len(limited_text)}")

    empty_text = build_report_text([]).strip()
    add("output::empty_text", empty_text == "无符合灵动充沛条件股票", f"空文案={empty_text!r}")

    return items


def run_self_check(rounds: int) -> None:
    ensure_report_dir()
    all_rounds: List[Dict[str, Any]] = []

    for round_id in range(1, max(1, rounds) + 1):
        items = self_check_once()
        ok = all(x["ok"] for x in items)
        all_rounds.append({"round": round_id, "ok": ok, "items": items})

        print(f"灵动自检第{round_id}遍：{'通过' if ok else '失败'}", flush=True)
        for item in items:
            print(f"  [{'OK' if item['ok'] else 'FAIL'}] {item['name']}｜{item['detail']}", flush=True)

        if not ok:
            SELF_CHECK_JSON.write_text(json.dumps(all_rounds, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"灵动自检第{round_id}遍失败")

    SELF_CHECK_JSON.write_text(json.dumps(all_rounds, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="灵动日K活性扫描器")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-rounds", type=int, default=3)
    parser.add_argument("--limit", type=int, default=MAX_STOCKS)
    args = parser.parse_args()

    try:
        if args.self_test:
            run_self_check(args.self_test_rounds)
            return 0
        if args.scan:
            return run_scan(args.limit)
        parser.print_help()
        return 0
    except Exception as exc:
        print(f"灵动运行失败: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
