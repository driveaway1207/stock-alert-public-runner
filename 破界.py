# -*- coding: utf-8 -*-
from __future__ import annotations

"""破界：年线核心线限定版。
核心线只取年线，日线只判断突破、回踩和当前状态。
"""

import json
import math
import os
import re
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

try:
    import requests
except Exception:
    requests = None


BOOT = "POJIE_FULLMARKET_DIRECT_YEAR_CORE_EASTMONEY_V12_7_4_YEAR_ONLY_LOGIC_RECHECK_20260702"
RUN_MODE = "full_market_default; direct_year_core_kline_source; target_codes_optional; year_core_only; fixed_universe_pagination; steady_http; resume_checkpoint; logic_clean_v9; invalid_indicator_clean; source_clean; logic_recheck; breakout_first; refined_breakout_quality; close_core_candidate; event_dedupe; today_breakout_priority; pullback_segment; chinese_trade_report; failed_retry_pass; completion_guard"
START_TS = time.time()

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "破界报告"
OUTPUT_MD = REPORT_DIR / "核心线突破海选报告.md"
OUTPUT_CSV = REPORT_DIR / "核心线突破海选明细.csv"
OUTPUT_JSON = REPORT_DIR / "核心线突破海选数据.json"
OUTPUT_CORE_MAP = REPORT_DIR / "core_line_map.csv"
OUTPUT_EVENTS = REPORT_DIR / "breakout_events.csv"
SELF_CHECK_JSON = REPORT_DIR / "破界自检.json"

CACHE_DIRS = [
    ROOT / "kline_cache",
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]

TARGET_ENV_KEYS = [
    "POJIE_TARGET_DATE",
    "EMPLOYEE3_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "TARGET_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "LAST_TRADE_DAY",
    "LAST_TRADE_DAY_OVERRIDE",
]

BOT = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
SEND_TELEGRAM = (os.getenv("POJIE_SEND_TELEGRAM") or os.getenv("ENABLE_TELEGRAM") or "0").strip() in {
    "1", "true", "True", "yes", "YES", "发送"
}

TARGET_CODES_RAW = os.getenv("POJIE_TARGET_CODES", "").strip()
TARGET_CODES = [
    x for x in re.split(r"[,，\s]+", TARGET_CODES_RAW)
    if re.fullmatch(r"\d{6}", x)
]
RUN_FULL_MARKET = len(TARGET_CODES) == 0

EASTMONEY_START = os.getenv("POJIE_EASTMONEY_START", "19900101")
ADJUST_FLAG_ENV = os.getenv("POJIE_ADJUST_FLAG", os.getenv("POJIE_FQT", "1")).strip()
ADJUST_FLAG_NAMES = {"1": "前复权", "2": "后复权", "0": "不复权"}

EASTMONEY_CACHE_DIR = ROOT / "kline_cache" / "eastmoney_direct"
UNIVERSE_CACHE = EASTMONEY_CACHE_DIR / "universe_a_share.csv"
EASTMONEY_UNIVERSE_FS = os.getenv(
    "POJIE_EASTMONEY_FS",
    "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80"
)
MAX_STOCKS = int(os.getenv("POJIE_MAX_STOCKS", "0"))
REQUESTED_WORKERS = max(1, int(os.getenv("POJIE_WORKERS", "6")))
STABLE_FULLMARKET_WORKERS = max(1, int(os.getenv("POJIE_STABLE_FULLMARKET_WORKERS", "16")))
MAX_EFFECTIVE_WORKERS = max(1, int(os.getenv("POJIE_MAX_EFFECTIVE_WORKERS", "24")))
if RUN_FULL_MARKET:
    WORKERS = min(MAX_EFFECTIVE_WORKERS, max(REQUESTED_WORKERS, STABLE_FULLMARKET_WORKERS))
else:
    WORKERS = REQUESTED_WORKERS

REQUEST_TIMEOUT = float(os.getenv("POJIE_REQUEST_TIMEOUT", "14"))
REQUEST_RETRIES = max(0, int(os.getenv("POJIE_REQUEST_RETRIES", "1")))
HTTP_POOL_SIZE = max(WORKERS + 8, int(os.getenv("POJIE_HTTP_POOL_SIZE", "48")))
RESUME_ENABLED = os.getenv("POJIE_RESUME_ENABLED", "1").strip() not in {"0", "false", "False", "no", "NO"}
FAST_SKIP_CACHE_REF = os.getenv("POJIE_FAST_SKIP_CACHE_REF", "1").strip() not in {"0", "false", "False", "no", "NO"}
PARTIAL_SAVE_EVERY = max(1, int(os.getenv("POJIE_PARTIAL_SAVE_EVERY", "25")))
EVENT_LOOKBACK_DAYS = int(os.getenv("POJIE_EVENT_LOOKBACK_DAYS", "20"))
TOP_PUSH_LIMIT = int(os.getenv("POJIE_TOP_PUSH_LIMIT", "20"))
MIN_COMPLETION_RATE = float(os.getenv("POJIE_MIN_COMPLETION_RATE", "0.85"))
RETRY_FAILED_PASS = os.getenv("POJIE_RETRY_FAILED_PASS", "1").strip() not in {"0", "false", "False", "no", "NO"}
FAILED_RETRY_WORKERS = max(1, int(os.getenv("POJIE_FAILED_RETRY_WORKERS", "6")))

POINT_TOL = float(os.getenv("POJIE_POINT_TOL", "0.005"))
BODY_EDGE_TOL = float(os.getenv("POJIE_BODY_EDGE_TOL", "0.005"))
SHADOW_REACTION_WEIGHT = float(os.getenv("POJIE_SHADOW_REACTION_WEIGHT", "0.70"))

CORE_SELECTION_MODE = "year_only"

MAX_TODAY_BREAK_DISTANCE_BY_RANK = {
    6: float(os.getenv("POJIE_MAX_DIST_SPLUS", "13.5")),
    5: float(os.getenv("POJIE_MAX_DIST_S", "12.0")),
    4: float(os.getenv("POJIE_MAX_DIST_APLUS", "10.0")),
    3: float(os.getenv("POJIE_MAX_DIST_A", "8.0")),
    2: float(os.getenv("POJIE_MAX_DIST_B", "6.5")),
}
RECENT_BREAK_MIN_CLOSE_ABOVE_LINE = float(os.getenv("POJIE_RECENT_BREAK_MIN_CLOSE_ABOVE_LINE", "0.995"))
RECENT_BREAK_MAX_DISTANCE = float(os.getenv("POJIE_RECENT_BREAK_MAX_DISTANCE", "28.0"))
RECLAIM_CONTEXT_LOOKBACK_DAYS = int(os.getenv("POJIE_RECLAIM_CONTEXT_LOOKBACK_DAYS", "80"))
RECLAIM_CONTEXT_TOUCH_DAYS = int(os.getenv("POJIE_RECLAIM_CONTEXT_TOUCH_DAYS", "8"))
RECLAIM_CONTEXT_MAX_PULLBACK_CLOSE = float(os.getenv("POJIE_RECLAIM_CONTEXT_MAX_PULLBACK_CLOSE", "0.965"))
YEAR_CORE_ALLOW_ONE_CUT_STRONG = os.getenv("POJIE_YEAR_CORE_ALLOW_ONE_CUT_STRONG", "0").strip() in {"1", "true", "True", "yes", "YES"}
MAX_TODAY_LIMIT_BREAK_DISTANCE_CAP = float(os.getenv("POJIE_MAX_TODAY_LIMIT_BREAK_DISTANCE_CAP", "33.5"))

BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "260"))
PULLBACK_LOOKBACK_AFTER_BREAK = int(os.getenv("POJIE_PULLBACK_LOOKBACK_AFTER_BREAK", "80"))
EVENT_DAILY_DAYS = max(420, int(os.getenv("POJIE_EVENT_DAILY_DAYS", str(BREAKOUT_LOOKBACK_DAYS + PULLBACK_LOOKBACK_AFTER_BREAK + 120))))
PROGRESS_DIR = EASTMONEY_CACHE_DIR / "pojie_progress"
PROGRESS_JSONL = None
_THREAD_LOCAL = threading.local()


def log(msg: str) -> None:
    print(f"[破界V12.7.4][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.replace(",", "").replace("%", "").strip()
            if x == "":
                return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def rd(x: Any, n: int = 3) -> float:
    return round(sf(x), n)


def pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def norm_date(x: Any) -> str:
    s = ss(x)
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def code_of(x: Any) -> str:
    m = re.search(r"(\d{6})", ss(x))
    return m.group(1) if m else ""


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def previous_weekday(d: datetime) -> datetime:
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


def resolve_target_raw() -> str:
    for key in TARGET_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    d = now_bj()
    if d.weekday() >= 5:
        d = previous_weekday(d)
    elif d.hour < 15 or (d.hour == 15 and d.minute < 30):
        d = previous_weekday(d)
    return d.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", resolve_target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""
TARGET_TS = pd.Timestamp(TARGET_DASH) if TARGET_DASH else pd.Timestamp.today()
PROGRESS_JSONL = PROGRESS_DIR / f"progress_{TARGET or 'unknown'}_{ADJUST_FLAG_ENV}_{CORE_SELECTION_MODE}_v12_7_4_year_only.jsonl"


def get_http_session() -> Any:
    if requests is None:
        raise RuntimeError("requests 不可用")
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is not None:
        return session
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Connection": "keep-alive",
    })
    try:
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=HTTP_POOL_SIZE, pool_maxsize=HTTP_POOL_SIZE, max_retries=0)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    except Exception:
        pass
    _THREAD_LOCAL.session = session
    return session


def http_get_json(url: str, params: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    timeout = REQUEST_TIMEOUT if timeout is None else timeout
    for attempt in range(REQUEST_RETRIES + 1):
        try:
            resp = get_http_session().get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < REQUEST_RETRIES:
                time.sleep(min(1.5 + attempt, 4.0))
    raise RuntimeError(f"HTTP请求失败：{last_exc}")


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    col_map = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "股票代码": "code", "证券代码": "code", "symbol": "code", "code": "code",
        "名称": "name", "股票名称": "name", "股票简称": "name", "证券简称": "name", "name": "name",
        "开盘": "open", "开盘价": "open", "open": "open",
        "最高": "high", "最高价": "high", "high": "high",
        "最低": "low", "最低价": "low", "low": "low",
        "收盘": "close", "收盘价": "close", "close": "close",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "涨幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg",
    }
    d = df.rename(columns={c: col_map.get(str(c), col_map.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns:
            d[col] = d[col].map(sf)
    for col, default in [("volume", 0.0), ("amount", 0.0), ("code", ""), ("name", "")]:
        if col not in d.columns:
            d[col] = default
    d["date"] = d["date"].map(norm_date)
    d["code"] = d["code"].map(code_of)
    d["name"] = d["name"].map(ss)
    d = d[
        (d["date"] != "")
        & (d["open"] > 0)
        & (d["high"] > 0)
        & (d["low"] > 0)
        & (d["close"] > 0)
    ].sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if TARGET_DASH:
        d = d[d["date"] <= TARGET_DASH].reset_index(drop=True)
    if d.empty:
        return d
    if "pct_chg" not in d.columns or float(d["pct_chg"].abs().sum()) == 0:
        prev = d["close"].shift(1)
        d["pct_chg"] = (d["close"] / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
    return d


def find_cache_file(code: str) -> Optional[Path]:
    hits: List[Path] = []
    for root in CACHE_DIRS:
        if not root.exists():
            continue
        hits.extend([p for p in root.glob(f"*{code}*") if p.suffix.lower() in {".csv", ".json"}])
    if not hits:
        return None
    return sorted(hits, key=lambda p: (len(str(p)), str(p)))[0]


def read_cache(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            return normalize_hist(pd.read_csv(path))
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            rows = obj.get("rows") or obj.get("data") or obj.get("klines") or obj.get("items") or []
        else:
            rows = obj
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def cache_reference_close(code: str) -> Tuple[float, str]:
    if RUN_FULL_MARKET and FAST_SKIP_CACHE_REF and ADJUST_FLAG_ENV in {"0", "1", "2"}:
        return 0.0, "skipped_full_market_fixed_adjust"
    p = find_cache_file(code)
    if not p:
        return 0.0, ""
    d = read_cache(p)
    if d.empty:
        return 0.0, str(p)
    return sf(d.iloc[-1]["close"]), str(p)


def eastmoney_secid(code: str) -> str:
    c = code_of(code)
    if c.startswith(("6", "9")):
        return f"1.{c}"
    if c.startswith(("0", "3")):
        return f"0.{c}"
    if c.startswith(("8", "4")):
        return f"0.{c}"
    return c


def eastmoney_klt(period: str) -> int:
    return {"D": 101, "Y": 106}[period]


def eastmoney_cache_path(code: str, period: str, fqt: str, begin: str = "") -> Path:
    suffix = "recent" if period == "D" and begin and begin > EASTMONEY_START else "full"
    return EASTMONEY_CACHE_DIR / f"fqt_{fqt}" / period / suffix / f"{code}.csv"


def cache_is_fresh(df: pd.DataFrame, period: str) -> bool:
    d = normalize_hist(df)
    if d.empty or not TARGET_DASH:
        return False
    latest = pd.to_datetime(d["date"], errors="coerce").max()
    if pd.isna(latest):
        return False
    target = pd.Timestamp(TARGET_DASH)
    if period == "D":
        return latest.date() >= target.date()
    if period == "Y":
        complete_year_needed = (target - pd.DateOffset(years=1)).to_period("Y")
        return latest.to_period("Y") >= complete_year_needed
    return False


def fetch_eastmoney_kline(code: str, period: str, fqt: str, begin: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    if requests is None:
        raise RuntimeError("requests 不可用，无法拉取东方财富K线")
    cache_path = eastmoney_cache_path(code, period, fqt, begin)
    if use_cache and cache_path.exists():
        try:
            cached = normalize_hist(pd.read_csv(cache_path))
            if cache_is_fresh(cached, period):
                return cached
        except Exception:
            pass
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": eastmoney_klt(period),
        "fqt": fqt,
        "beg": begin,
        "end": end,
        "lmt": "1000000",
    }
    obj = http_get_json(url, params=params)
    data = obj.get("data") or {}
    klines = data.get("klines") or []
    rows: List[Dict[str, Any]] = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 11:
            continue
        rows.append({
            "date": parts[0], "open": parts[1], "close": parts[2], "high": parts[3], "low": parts[4],
            "volume": parts[5], "amount": parts[6], "amplitude": parts[7], "pct_chg": parts[8],
            "change": parts[9], "turnover": parts[10], "code": code,
        })
    d = normalize_hist(pd.DataFrame(rows))
    if d.empty:
        raise RuntimeError(f"东方财富K线为空 code={code} period={period} fqt={fqt}")
    if use_cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            d.to_csv(cache_path, index=False, encoding="utf-8-sig")
        except Exception:
            pass
    return d


def choose_adjust_flag(code: str, reference_close: float) -> Tuple[str, Dict[str, Any]]:
    if ADJUST_FLAG_ENV in {"0", "1", "2"}:
        return ADJUST_FLAG_ENV, {"reason": "env_or_default", "reference_close": rd(reference_close)}
    if reference_close <= 0:
        return "1", {"reason": "default_qfq_no_reference", "reference_close": reference_close}
    begin = (TARGET_TS - pd.Timedelta(days=80)).strftime("%Y%m%d")
    end = TARGET.replace("-", "")
    candidates: List[Dict[str, Any]] = []
    for flag in ["1", "2", "0"]:
        try:
            d = fetch_eastmoney_kline(code, "D", flag, begin, end, use_cache=False)
            latest = sf(d.iloc[-1]["close"]) if not d.empty else 0.0
            candidates.append({"flag": flag, "name": ADJUST_FLAG_NAMES.get(flag, flag), "latest_close": rd(latest), "diff": abs(latest - reference_close) if latest > 0 else 1e18})
        except Exception as exc:
            candidates.append({"flag": flag, "name": ADJUST_FLAG_NAMES.get(flag, flag), "latest_close": 0.0, "diff": 1e18, "error": str(exc)[:120]})
    best = min(candidates, key=lambda x: sf(x["diff"], 1e18))
    return ss(best["flag"]), {"reason": "match_cache_reference_close", "reference_close": rd(reference_close), "candidates": candidates, "chosen": best}


def prepare_direct_period_bars(raw: pd.DataFrame, period: str) -> pd.DataFrame:
    if period != "Y":
        raise ValueError("破界年线限定版只允许用年线生成核心线")
    d = normalize_hist(raw)
    if d.empty:
        return pd.DataFrame()
    dt = pd.to_datetime(d["date"], errors="coerce")
    d = d[dt.notna()].copy()
    if d.empty:
        return pd.DataFrame()
    d["period"] = pd.to_datetime(d["date"]).dt.to_period("Y").astype(str)
    target_period = pd.Timestamp(TARGET_DASH).to_period("Y")
    d = d[pd.to_datetime(d["date"]).dt.to_period("Y") < target_period].copy()
    if d.empty:
        return pd.DataFrame()
    d = d.sort_values("date").reset_index(drop=True)
    d["start"] = d["date"]
    d["end"] = d["date"]
    d["body_top"] = d[["open", "close"]].max(axis=1)
    d["body_bottom"] = d[["open", "close"]].min(axis=1)
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["body_ratio"] = ((d["close"] - d["open"]).abs() / rng).fillna(0.0)
    d["close_pos"] = ((d["close"] - d["low"]) / rng).fillna(0.0)
    d["upper_shadow_ratio"] = ((d["high"] - d["body_top"]) / rng).fillna(0.0)
    d["is_bull"] = d["close"] > d["open"]
    d["vol_ratio_prev"] = d["volume"] / d["volume"].shift(1).replace(0, np.nan)
    d["vol_ratio_prev"] = d["vol_ratio_prev"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return d.reset_index(drop=True)


def fetch_direct_period_bars(code: str, fqt: str, period: str) -> pd.DataFrame:
    raw = fetch_eastmoney_kline(code, period, fqt, EASTMONEY_START, TARGET.replace("-", ""))
    return prepare_direct_period_bars(raw, period)


def fetch_event_daily(code: str, fqt: str) -> pd.DataFrame:
    begin = (TARGET_TS - pd.Timedelta(days=int(EVENT_DAILY_DAYS * 1.7))).strftime("%Y%m%d")
    return fetch_eastmoney_kline(code, "D", fqt, begin, TARGET.replace("-", ""))


def fetch_eastmoney_universe() -> pd.DataFrame:
    if requests is None:
        raise RuntimeError("requests 不可用，无法拉取东方财富股票池")
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    all_rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    page = 1
    page_size = 100
    total = 0
    while True:
        params = {
            "pn": page, "pz": page_size, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
            "fs": EASTMONEY_UNIVERSE_FS,
            "fields": "f12,f14,f2,f3,f4,f5,f6,f8,f20",
        }
        obj = http_get_json(url, params=params, timeout=max(REQUEST_TIMEOUT, 20))
        data = obj.get("data") or {}
        diff = data.get("diff") or []
        total = int(sf(data.get("total"), total))
        if not diff:
            break
        new_count = 0
        for x in diff:
            code = code_of(x.get("f12"))
            name = ss(x.get("f14"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            if not code.startswith(("0", "3", "6")):
                continue
            if any(bad in name for bad in ["退市", "退"]):
                continue
            if code in seen:
                continue
            seen.add(code)
            new_count += 1
            all_rows.append({
                "code": code, "name": name, "last_price": sf(x.get("f2")), "pct_chg": sf(x.get("f3")),
                "volume": sf(x.get("f5")), "amount": sf(x.get("f6")), "turnover": sf(x.get("f8")), "mkt_cap": sf(x.get("f20")),
            })
        if total > 0 and page * page_size >= total:
            break
        if len(diff) < page_size and total <= 0:
            break
        if new_count == 0 and page > 5:
            break
        page += 1
        if page > 300:
            break
    df = pd.DataFrame(all_rows).drop_duplicates("code").sort_values("code").reset_index(drop=True)
    if df.empty:
        raise RuntimeError("东方财富股票池为空")
    try:
        UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(UNIVERSE_CACHE, index=False, encoding="utf-8-sig")
    except Exception:
        pass
    log(f"股票池拉取完成：{len(df)}只｜page_size={page_size}｜pages={page}｜total={total}")
    return df


def load_stock_universe() -> pd.DataFrame:
    try:
        return fetch_eastmoney_universe()
    except Exception as exc:
        log(f"股票池在线拉取失败，尝试缓存：{exc}")
        if UNIVERSE_CACHE.exists():
            try:
                df = pd.read_csv(UNIVERSE_CACHE)
                if not df.empty and "code" in df.columns:
                    df["code"] = df["code"].map(lambda x: str(x).zfill(6)[-6:])
                    return df.drop_duplicates("code").sort_values("code").reset_index(drop=True)
            except Exception:
                pass
        raise


def resolve_run_universe() -> Tuple[List[str], Dict[str, str]]:
    if TARGET_CODES:
        return TARGET_CODES, {c: ("长电科技" if c == "600584" else "") for c in TARGET_CODES}
    universe = load_stock_universe()
    if MAX_STOCKS > 0:
        universe = universe.head(MAX_STOCKS).copy()
        log(f"扫描上限启用：POJIE_MAX_STOCKS={MAX_STOCKS}｜实际扫描{len(universe)}只")
    else:
        log(f"扫描上限关闭：POJIE_MAX_STOCKS=0｜实际扫描股票池{len(universe)}只")
    codes = [code_of(x) for x in universe["code"].tolist() if code_of(x)]
    names = {code_of(r["code"]): ss(r.get("name", "")) for _, r in universe.iterrows()}
    return codes, names


def min_seed_discrete_count(min_effective_unique: int) -> int:
    if min_effective_unique <= 4:
        return 2
    if min_effective_unique <= 8:
        return 3
    return 4


def reaction_volume_weight(row: Any) -> float:
    vr = sf(row.get("vol_ratio_prev", 0.0)) if hasattr(row, "get") else sf(row["vol_ratio_prev"])
    if vr >= 2.50:
        return 2.0
    if vr >= 1.80:
        return 1.8
    if vr >= 1.60:
        return 1.6
    if vr >= 1.30:
        return 1.35
    return 1.0

def build_reaction_points(k: pd.DataFrame) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    for idx, row in k.iterrows():
        weight = reaction_volume_weight(row)
        for kind, price in [("high", sf(row["high"])), ("body_top", sf(row["body_top"])), ("close", sf(row["close"]))]:
            if price <= 0:
                continue
            points.append({
                "bar_index": int(idx), "period": ss(row["period"]), "end": ss(row["end"]), "kind": kind,
                "price": rd(price, 4), "weight": weight,
                "is_volume_reaction": bool(weight >= 1.30),
                "is_double_reaction": bool(weight >= 1.80),
                "open": rd(row["open"]), "high": rd(row["high"]), "low": rd(row["low"]), "close": rd(row["close"]),
                "body_top": rd(row["body_top"]), "volume": rd(row["volume"]), "vol_ratio_prev": rd(row["vol_ratio_prev"]),
            })
    return sorted(points, key=lambda x: sf(x["price"]))


def points_near(points: List[Dict[str, Any]], center: float, tol: float) -> List[Dict[str, Any]]:
    if center <= 0:
        return []
    return [p for p in points if abs(sf(p["price"]) - center) / center <= tol]


def choose_best_point_per_bar(points: List[Dict[str, Any]], prefer_line: Optional[float] = None) -> List[Dict[str, Any]]:
    by_bar: Dict[int, Dict[str, Any]] = {}
    def key(p: Dict[str, Any]) -> Tuple[Any, ...]:
        dist_key = -abs(sf(p["price"]) - sf(prefer_line)) / max(sf(prefer_line), 1e-9) if prefer_line else 0.0
        return (sf(p["weight"]), dist_key)
    for p in points:
        b = int(p["bar_index"])
        if b not in by_bar or key(p) > key(by_bar[b]):
            by_bar[b] = p
    return sorted(by_bar.values(), key=lambda x: int(x["bar_index"]))


def cluster_from_seed(points: List[Dict[str, Any]], seed_price: float, min_seed_unique: int) -> Optional[Dict[str, Any]]:
    raw = points_near(points, seed_price, POINT_TOL)
    uniq = choose_best_point_per_bar(raw, seed_price)
    if len(uniq) < min_seed_unique:
        return None
    prices = [sf(p["price"]) for p in uniq]
    weights = [max(sf(p["weight"]), 0.1) for p in uniq]
    center = float(np.average(prices, weights=weights))
    return {"seed": rd(seed_price), "center": rd(center), "low": rd(min(prices)), "high": rd(max(prices)), "primary_unique": len(uniq), "primary_points": uniq, "raw_points": raw, "primary_weight": rd(sum(weights)), "primary_periods": ",".join(ss(p["period"]) for p in uniq), "primary_kinds": ",".join(ss(p["kind"]) for p in uniq)}


def dedupe_clusters(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[List[Dict[str, Any]]] = []
    for c in sorted(clusters, key=lambda z: sf(z["center"])):
        placed = False
        for g in groups:
            if abs(sf(c["center"]) - sf(g[0]["center"])) / max(sf(g[0]["center"]), 1e-9) <= POINT_TOL:
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])
    def rank(c: Dict[str, Any]) -> Tuple[Any, ...]:
        return (int(c["primary_unique"]), sf(c["primary_weight"]), sf(c["center"]))
    return [max(g, key=rank) for g in groups]


def count_cut_entities(k: pd.DataFrame, line: float) -> int:
    cuts = 0
    for _, r in k.iterrows():
        body_top = sf(r["body_top"])
        body_bottom = sf(r["body_bottom"])
        inside = body_bottom < line < body_top
        near_top = abs(body_top - line) / max(line, 1e-9) <= BODY_EDGE_TOL
        if inside and not near_top:
            cuts += 1
    return cuts


def shadow_reactions(k: pd.DataFrame, line: float, used_bars: set[int], anchor_bar: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, r in k.iterrows():
        i = int(idx)
        if i == int(anchor_bar) or i in used_bars:
            continue
        body_top = sf(r["body_top"])
        high = sf(r["high"])
        if body_top < line < high:
            out.append({
                "bar_index": i, "period": ss(r["period"]), "end": ss(r["end"]), "kind": "shadow", "price": rd(line, 4),
                "weight": SHADOW_REACTION_WEIGHT, "is_volume_reaction": False, "is_double_reaction": False,
                "open": rd(r["open"]), "high": rd(r["high"]), "low": rd(r["low"]), "close": rd(r["close"]),
                "body_top": rd(r["body_top"]), "volume": rd(r["volume"]), "vol_ratio_prev": rd(r["vol_ratio_prev"]),
            })
    return out


def weighted_center(points: List[Dict[str, Any]]) -> float:
    if not points:
        return 0.0
    prices = [sf(p["price"]) for p in points]
    weights = [max(sf(p.get("weight")), 0.1) for p in points]
    return float(np.average(prices, weights=weights))


def adhesion_metrics(line: float, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    primary = [p for p in samples if ss(p.get("kind")) in {"high", "body_top", "close"}]
    shadows = [p for p in samples if ss(p.get("kind")) == "shadow"]
    bodytops = [p for p in primary if ss(p.get("kind")) == "body_top"]
    highs = [p for p in primary if ss(p.get("kind")) == "high"]
    closes = [p for p in primary if ss(p.get("kind")) == "close"]
    center = weighted_center(primary) or line
    if primary:
        total_w = sum(max(sf(p["weight"]), 0.1) for p in primary)
        compact = sum(abs(sf(p["price"]) - line) / max(line, 1e-9) * max(sf(p["weight"]), 0.1) for p in primary) / total_w
    else:
        compact = 0.0
    center_bias = abs(line - center) / max(center, 1e-9)
    latest_bar = max([int(p.get("bar_index", -1)) for p in samples] or [-1])
    primary_score = sum(max(sf(p.get("weight")), 1.0) for p in primary)
    shadow_score = sum(max(sf(p.get("weight")), 0.1) for p in shadows)
    score = primary_score + shadow_score - 10.0 * compact - 6.0 * center_bias
    return {"adhesion_score": rd(score, 4), "reaction_center": rd(center, 4), "compact_error_pct": rd(compact * 100, 4), "center_bias_pct": rd(center_bias * 100, 4), "latest_reaction_bar": int(latest_bar), "bodytop_sticky_count": len(bodytops), "high_sticky_count": len(highs), "close_sticky_count": len(closes), "shadow_sticky_count": len(shadows)}

def validate_candidate_line(k: pd.DataFrame, points: List[Dict[str, Any]], cluster: Dict[str, Any], anchor_point: Dict[str, Any], min_effective_unique: int) -> Optional[Dict[str, Any]]:
    line = sf(anchor_point["price"])
    if line <= 0:
        return None
    primary_raw = points_near(points, line, POINT_TOL)
    primary = choose_best_point_per_bar(primary_raw, line)
    primary_bars = {int(p["bar_index"]) for p in primary}
    anchor_bar = int(anchor_point["bar_index"])
    external_primary = [p for p in primary if int(p["bar_index"]) != anchor_bar]
    shadows = shadow_reactions(k, line, primary_bars, anchor_bar)
    samples = sorted(primary + shadows, key=lambda x: int(x["bar_index"]))
    sample_bars = {int(p["bar_index"]) for p in samples}
    external_effective = [p for p in samples if int(p["bar_index"]) != anchor_bar]
    if len(sample_bars) < min_effective_unique:
        return None
    if len(external_effective) < max(2, min_effective_unique - 1):
        return None
    if len(primary_bars) < 2:
        return None
    center = sf(cluster["center"])
    if center > 0 and abs(line - center) / center > POINT_TOL:
        return None
    cut_count = count_cut_entities(k, line)
    volume_reaction_count = sum(1 for p in primary if sf(p.get("weight")) >= 1.30)
    double_reaction_count = sum(1 for p in primary if sf(p.get("weight")) >= 1.80)
    strong_one_cut_ok = (
        YEAR_CORE_ALLOW_ONE_CUT_STRONG
        and cut_count == 1
        and len(sample_bars) >= 6
        and volume_reaction_count >= 2
        and double_reaction_count >= 1
    )
    if cut_count > 0 and not strong_one_cut_ok:
        return None
    if cut_count > 1:
        return None
    if volume_reaction_count >= 2 and double_reaction_count >= 1 and cut_count == 0:
        grade = "S-Core"
    elif cut_count == 0 and volume_reaction_count >= 1:
        grade = "A-Core"
    elif strong_one_cut_ok:
        grade = "A-Core"
    else:
        return None
    stick = adhesion_metrics(line, samples)
    cut_penalty = round((float(cut_count) ** 1.55) * 1.8, 4) if cut_count > 0 else 0.0
    stick["adhesion_score"] = rd(sf(stick.get("adhesion_score")) - cut_penalty, 4)
    stick["cut_penalty"] = rd(cut_penalty, 4)
    out = {
        "line": rd(line), "core_grade": grade, "touch_count": len(sample_bars), "effective_unique_count": len(sample_bars),
        "primary_unique_count": len(primary_bars), "external_effective_count": len(external_effective), "external_primary_count": len(external_primary),
        "external_shadow_count": len(shadows),
        "volume_reaction_count": int(volume_reaction_count), "double_reaction_count": int(double_reaction_count),
        "cut_count": int(cut_count), "primary_weight": rd(sum(sf(p["weight"]) for p in primary)), "effective_weight": rd(sum(sf(p["weight"]) for p in samples)),
        "cluster_center": rd(cluster["center"]), "cluster_low": rd(cluster["low"]), "cluster_high": rd(cluster["high"]),
        "cluster_primary_unique": int(cluster["primary_unique"]), "cluster_primary_weight": rd(cluster["primary_weight"]),
        "anchor_period": ss(anchor_point["period"]), "anchor_end": ss(anchor_point["end"]), "anchor_bar_index": int(anchor_bar),
        "anchor_kind": ss(anchor_point["kind"]), "anchor_price": rd(anchor_point["price"]),
        "sample_periods": ",".join(ss(p["period"]) for p in samples), "sample_kinds": ",".join(ss(p["kind"]) for p in samples),
        "primary_sample_periods": ",".join(ss(p["period"]) for p in primary), "primary_sample_kinds": ",".join(ss(p["kind"]) for p in primary),
        "external_sample_periods": ",".join(ss(p["period"]) for p in external_effective), "external_sample_kinds": ",".join(ss(p["kind"]) for p in external_effective),
        "samples": samples,
    }
    out.update(stick)
    return out


def scan_period_core_lines(period_bars: Dict[str, pd.DataFrame], period: str, label: str, min_effective_unique: int, period_rank: int) -> List[Dict[str, Any]]:
    k = period_bars.get(period, pd.DataFrame())
    if k is None or k.empty:
        return []
    points = build_reaction_points(k)
    if not points:
        return []
    seed_need = min_seed_discrete_count(min_effective_unique)
    clusters = []
    for p in points:
        c = cluster_from_seed(points, sf(p["price"]), seed_need)
        if c:
            clusters.append(c)
    clusters = dedupe_clusters(clusters)
    lines: List[Dict[str, Any]] = []
    for cluster in clusters:
        line_candidates: List[Dict[str, Any]] = []
        for p in points:
            if ss(p.get("kind")) not in {"body_top", "high", "close"}:
                continue
            center = sf(cluster["center"])
            if center > 0 and abs(sf(p["price"]) - center) / center <= POINT_TOL:
                line_candidates.append(p)
        valid: List[Dict[str, Any]] = []
        for bp in line_candidates:
            v = validate_candidate_line(k, points, cluster, bp, min_effective_unique)
            if v:
                valid.append(v)
        if not valid:
            continue
        def rank(x: Dict[str, Any]) -> Tuple[Any, ...]:
            return (int(x["external_effective_count"]), int(x["effective_unique_count"]), sf(x.get("adhesion_score")), -int(x.get("cut_count", 0)), -sf(x.get("center_bias_pct")), -sf(x.get("compact_error_pct")), int(x.get("latest_reaction_bar", -1)), int(x.get("anchor_bar_index", -1)), int(x["external_primary_count"]), int(x["external_shadow_count"]), int(x.get("double_reaction_count", 0)), int(x.get("volume_reaction_count", 0)), sf(x["effective_weight"]), sf(x["line"]) * 0.000001)
        best = max(valid, key=rank)
        best.update({"period_type": period, "period_label": label, "period_rank": int(period_rank), "core_rank": {"S-Core": 3, "A-Core": 2}.get(ss(best["core_grade"]), 0), "min_effective_unique": int(min_effective_unique), "seed_discrete_need": int(seed_need)})
        lines.append(best)
    return dedupe_lines(lines)


def dedupe_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[List[Dict[str, Any]]] = []
    for x in sorted(lines, key=lambda z: sf(z["line"])):
        placed = False
        for g in groups:
            if abs(sf(x["line"]) - sf(g[0]["line"])) / max(sf(g[0]["line"]), 1e-9) <= POINT_TOL:
                g.append(x)
                placed = True
                break
        if not placed:
            groups.append([x])
    def rank(x: Dict[str, Any]) -> Tuple[Any, ...]:
        return (int(x.get("core_rank", 0)), int(x.get("period_rank", 0)), int(x.get("external_effective_count", 0)), int(x.get("effective_unique_count", 0)), sf(x.get("adhesion_score")), -int(x.get("cut_count", 0)), -sf(x.get("center_bias_pct")), -sf(x.get("compact_error_pct")), int(x.get("latest_reaction_bar", -1)), int(x.get("double_reaction_count", 0)), int(x.get("volume_reaction_count", 0)), sf(x.get("effective_weight")), sf(x.get("line")) * 0.000001)
    return sorted([max(g, key=rank) for g in groups], key=lambda z: sf(z["line"]))


def scan_core_lines(period_bars: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    yearly = scan_period_core_lines(period_bars, "Y", "年线", 4, 3)
    for x in yearly:
        x["selection_path"] = "year_core_only"
    return dedupe_lines(yearly)


def scan_core_lines_progressive(code: str, fqt: str) -> Tuple[List[Dict[str, Any]], Dict[str, pd.DataFrame]]:
    bars: Dict[str, pd.DataFrame] = {}
    y = fetch_direct_period_bars(code, fqt, "Y")
    bars["Y"] = y
    return scan_core_lines(bars), bars

def select_near_far(lines: List[Dict[str, Any]], current_close: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    valid = []
    for x in lines:
        y = dict(x)
        y["distance_pct"] = abs(pct(current_close, sf(y["line"])))
        valid.append(y)
    if not valid:
        return None, None
    near = min(valid, key=lambda x: sf(x["distance_pct"]))
    rest = [x for x in valid if abs(sf(x["line"]) - sf(near["line"])) / max(sf(near["line"]), 1e-9) > POINT_TOL]
    if not rest:
        return near, None
    far = min(rest, key=lambda x: sf(x["distance_pct"]))
    return near, far


def infer_limit_up_threshold(code: Any, name: str = "") -> float:
    c = code_of(code)
    nm = ss(name)
    if "ST" in nm.upper() or "*ST" in nm.upper():
        return 4.75
    if c.startswith(("300", "301", "688")):
        return 19.30
    if c.startswith(("8", "4")):
        return 29.00
    return 9.70


def daily_features(row: Any, prev: Any, line: float) -> Dict[str, Any]:
    open_ = sf(row["open"])
    high = sf(row["high"])
    low = sf(row["low"])
    close = sf(row["close"])
    volume = sf(row["volume"])
    prev_close = sf(prev["close"])
    prev_volume = sf(prev["volume"])
    pct_chg = sf(row.get("pct_chg"), pct(close, prev_close)) if hasattr(row, "get") else pct(close, prev_close)
    code = ss(row.get("code", "")) if hasattr(row, "get") else ""
    name = ss(row.get("name", "")) if hasattr(row, "get") else ""
    rng = max(high - low, 1e-9)
    body = abs(close - open_)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    body_tiny = body <= max(close, 1e-9) * 0.001
    full_body_above_raw = body_bottom >= line * 0.999 and close >= line * 1.003
    full_k_above_raw = low >= line * 0.998 and close >= line * 1.003
    if body_tiny:
        entity_above = 1.0 if full_body_above_raw or full_k_above_raw else 0.0
    else:
        entity_above = max(0.0, body_top - max(line, body_bottom)) / max(body, 1e-9)
    lower_body_below = body_bottom < line
    gap_open_above = open_ >= line * 1.001 and prev_close < line * 1.002
    body_accept_above = full_body_above_raw and prev_close < line * 1.002
    full_body_above = full_body_above_raw
    full_k_above = full_k_above_raw
    from_below_entity = lower_body_below and body_top >= line * 1.005
    from_below_trade = low < line * 0.998 and close >= line * 1.005
    limit_threshold = infer_limit_up_threshold(code, name)
    near_limit_up = pct_chg >= limit_threshold
    close_near_high = (close >= high * 0.992) if high > 0 else False
    limit_lock_accept = near_limit_up and close_near_high and close >= line * 1.005 and (full_body_above or full_k_above or from_below_trade)
    return {
        "vol_ratio_prev": volume / prev_volume if prev_volume > 0 else 0.0,
        "gap_open_above_line": bool(gap_open_above),
        "body_accept_above_line": bool(body_accept_above),
        "full_body_above_line": bool(full_body_above),
        "full_k_above_line": bool(full_k_above),
        "from_below_entity_break": bool(from_below_entity),
        "from_below_trade_break": bool(from_below_trade),
        "limit_lock_accept": bool(limit_lock_accept),
        "close_pos": (close - low) / rng,
        "body_ratio": body / rng,
        "body_tiny": bool(body_tiny),
        "upper_shadow_ratio": max(0.0, high - body_top) / rng,
        "entity_above_line_ratio": entity_above,
        "positive": close > open_,
        "non_negative": close >= open_ * 0.999,
        "pct_chg": pct_chg,
        "near_limit_up": bool(near_limit_up),
        "close_near_high": bool(close_near_high),
    }

def classify_breakout(f: Dict[str, Any]) -> Tuple[str, int]:
    positive = bool(f.get("positive"))
    non_negative = bool(f.get("non_negative"))
    close_pos = sf(f.get("close_pos"))
    body_ratio = sf(f.get("body_ratio"))
    upper_shadow = sf(f.get("upper_shadow_ratio"))
    above = sf(f.get("entity_above_line_ratio"))
    vr = sf(f.get("vol_ratio_prev"))
    gap = bool(f.get("gap_open_above_line"))
    open_accept = bool(f.get("body_accept_above_line")) or gap
    full_body = bool(f.get("full_body_above_line")) or above >= 0.98
    full_k = bool(f.get("full_k_above_line"))
    from_below = bool(f.get("from_below_entity_break")) or bool(f.get("from_below_trade_break"))
    limit_up = bool(f.get("near_limit_up"))
    limit_lock = bool(f.get("limit_lock_accept"))
    close_near_high = bool(f.get("close_near_high"))
    standard_double = 1.80 <= vr <= 2.50
    healthy_volume = 1.45 <= vr <= 3.20
    mild_volume = vr >= 1.15
    strong_close = close_pos >= 0.72 and upper_shadow <= 0.28
    ok_close = close_pos >= 0.62 and upper_shadow <= 0.38
    acceptable_close = close_pos >= 0.45 and upper_shadow <= 0.45
    strong_body = body_ratio >= 0.45
    ok_body = body_ratio >= 0.30
    body_tiny = bool(f.get("body_tiny"))
    accepted_above = (full_body or full_k) and above >= 0.90

    if open_accept and limit_lock and accepted_above:
        return "S+突破｜跳空打板破界", 6

    if not (positive or (limit_up and non_negative) or accepted_above):
        return "弱突破/试探", 0
    if above < 0.25 or close_pos < 0.35 or upper_shadow > 0.60:
        if accepted_above and open_accept:
            return "B突破｜跳空守线承接", 2
        return "弱突破/试探", 0

    if open_accept and limit_up and above >= 0.90 and close_near_high and upper_shadow <= 0.20 and (ok_body or body_tiny or full_k):
        return "S+突破｜跳空打板破界", 6

    if from_below and limit_up and above >= 0.60 and close_near_high and upper_shadow <= 0.22 and (ok_body or full_k):
        return "S突破｜实体打板破界", 5

    if open_accept and accepted_above and strong_close and (strong_body or body_tiny or full_k) and mild_volume:
        return "S突破｜跳空整实体在线上", 5

    if open_accept and accepted_above:
        if not mild_volume:
            return "C突破｜缩量跳空守线观察", 1
        if acceptable_close and (ok_body or body_tiny or full_k):
            return "A突破｜跳空整实体承接", 3
        return "B突破｜跳空守线承接", 2

    if from_below and above >= 0.70 and (standard_double or limit_up) and strong_close and strong_body:
        return "A+突破｜倍量强实体上穿", 4

    if ((open_accept and above >= 0.70 and mild_volume) or (from_below and above >= 0.50 and (healthy_volume or limit_up))) and ok_close and ok_body:
        return "A突破｜高质量实体破界", 3

    if above >= 0.35 and mild_volume and ok_close and ok_body:
        return "B突破｜普通实体破界", 2

    if above >= 0.25 and close_pos >= 0.55:
        return "C突破｜弱站上", 1
    return "弱突破/试探", 0

def breakout_rank_from_grade(grade: str) -> int:
    if ss(grade).startswith("S+突破"):
        return 6
    if ss(grade).startswith("S突破"):
        return 5
    if ss(grade).startswith("A+突破"):
        return 4
    if ss(grade).startswith("A突破"):
        return 3
    if ss(grade).startswith("B突破"):
        return 2
    if ss(grade).startswith("C突破"):
        return 1
    return 0


def max_today_distance_for_note(rank: int, f: Dict[str, Any]) -> float:
    max_dist = MAX_TODAY_BREAK_DISTANCE_BY_RANK.get(int(rank), 0.0)
    if bool(f.get("near_limit_up")) and int(rank) >= 5:
        max_dist = max(max_dist, min(MAX_TODAY_LIMIT_BREAK_DISTANCE_CAP, sf(f.get("pct_chg"), 0.0) + 3.0))
    return max_dist


def breakout_note(grade: str, f: Dict[str, Any], close: float, line: float) -> str:
    dist = pct(close, line)
    path = "跳空" if bool(f.get("gap_open_above_line")) else ("上穿" if bool(f.get("from_below_entity_break")) else "站上")
    pos = f"实体在线上{rd(sf(f.get('entity_above_line_ratio')) * 100, 1)}%"
    vol = f"昨比量{rd(f.get('vol_ratio_prev'), 2)}"
    extra = "涨停/近似涨停" if bool(f.get("near_limit_up")) else ""
    rank = breakout_rank_from_grade(grade)
    max_dist = max_today_distance_for_note(rank, f)
    if max_dist > 0 and dist > max_dist:
        far = "离核心线过远，降为观察/等回踩"
    elif dist > max(6.0, max_dist * 0.72 if max_dist else 6.0):
        far = "突破偏高，谨慎追高"
    else:
        far = "距离可接受"
    return "｜".join(x for x in [grade, path, pos, vol, extra, far] if x)


def is_reclaim_context(daily: pd.DataFrame, idx: int, line: float) -> bool:
    try:
        i = int(idx)
    except Exception:
        return False
    d = normalize_hist(daily)
    if d.empty or i <= 1 or line <= 0:
        return False
    lookback_start = max(0, i - max(5, RECLAIM_CONTEXT_LOOKBACK_DAYS))
    hist = d.iloc[lookback_start:i].copy()
    if hist.empty:
        return False
    accepted_mask = hist["close"].map(sf) >= line * 1.005
    if not bool(accepted_mask.any()):
        return False
    last_accept_label = accepted_mask[accepted_mask].index[-1]
    try:
        last_accept_pos = int(d.index.get_loc(last_accept_label))
    except Exception:
        last_accept_pos = int(last_accept_label) if str(last_accept_label).isdigit() else i - 1
    after_accept = d.iloc[last_accept_pos + 1:i]
    if not after_accept.empty:
        if bool((after_accept["close"].map(sf) < line * RECLAIM_CONTEXT_MAX_PULLBACK_CLOSE).any()):
            return False
    recent = d.iloc[max(0, i - max(2, RECLAIM_CONTEXT_TOUCH_DAYS)):i]
    if recent.empty:
        return False
    recent_touch = bool((recent["low"].map(sf) <= line * 1.018).any())
    recent_defended = bool((recent["close"].map(sf) >= line * 0.985).any())
    return recent_touch and recent_defended


def recent_break_distance_ok(current_close: float, line: float) -> bool:
    if line <= 0 or current_close <= 0:
        return False
    return abs(pct(current_close, line)) <= RECENT_BREAK_MAX_DISTANCE


def collect_breakouts(daily: pd.DataFrame, line: float) -> List[Dict[str, Any]]:
    d = normalize_hist(daily)
    out: List[Dict[str, Any]] = []
    if d.empty or line <= 0:
        return out
    start = max(1, len(d) - BREAKOUT_LOOKBACK_DAYS)
    for idx in range(start, len(d)):
        row = d.iloc[idx]
        prev = d.iloc[idx - 1]
        prev_close = sf(prev["close"])
        prev_low = sf(prev["low"])
        open_ = sf(row["open"])
        close = sf(row["close"])
        high = sf(row["high"])
        low = sf(row["low"])
        body_bottom = min(open_, close)
        crossed_from_below = prev_close <= line * 1.002 and prev_low < line and high >= line * 1.005 and close >= line * 1.003
        gap_accept = prev_close < line * 1.002 and open_ >= line * 1.001 and close >= line * 1.003
        body_accept = prev_close < line * 1.002 and body_bottom >= line * 0.999 and close >= line * 1.003
        reclaim_accept = prev_close <= line * 1.002 and prev_low < line * 1.002 and low <= line * 1.018 and close >= line * 1.005
        reclaim_context = is_reclaim_context(d, idx, line)
        reclaim_only = bool(reclaim_context and (reclaim_accept or crossed_from_below or gap_accept or body_accept))
        if not (crossed_from_below or gap_accept or body_accept or reclaim_accept):
            continue
        f = daily_features(row, prev, line)
        f["reclaim_only"] = reclaim_only
        f["reclaim_context"] = reclaim_context
        grade, rank = classify_breakout(f)
        if rank <= 0:
            continue
        dist = pct(close, line)
        obj = {
            "grade": grade,
            "score_rank": int(rank),
            "date": ss(row["date"]),
            "idx": int(idx),
            "close": rd(close),
            "distance_pct": rd(dist),
            "note": breakout_note(grade, f, close, line),
            "vol_ratio_prev": rd(f["vol_ratio_prev"]),
            "gap_break": bool(f["gap_open_above_line"]),
            "full_body_above_line": bool(f["full_body_above_line"]),
            "full_k_above_line": bool(f["full_k_above_line"]),
            "entity_above_line_ratio": rd(f["entity_above_line_ratio"]),
            "body_ratio": rd(f["body_ratio"]),
            "close_pos": rd(f["close_pos"]),
            "upper_shadow_ratio": rd(f["upper_shadow_ratio"]),
            "near_limit_up": bool(f["near_limit_up"]),
            "pct_chg": rd(f.get("pct_chg")),
            "breakout_path": "回收站上" if reclaim_only else ("跳空接受" if bool(f["gap_open_above_line"]) else ("整实体接受" if bool(f["body_accept_above_line"]) else ("实体上穿" if bool(f["from_below_entity_break"]) else "站上"))),
            "reclaim_only": bool(reclaim_only),
            "reclaim_context": bool(reclaim_context),
        }
        out.append(obj)
    return out


def best_breakout(daily: pd.DataFrame, line: float) -> Dict[str, Any]:
    breakouts = collect_breakouts(daily, line)
    if not breakouts:
        return {"grade": "无突破", "score_rank": 0}
    return max(breakouts, key=lambda obj: (int(obj.get("score_rank", 0)), -sf(obj.get("distance_pct", 999)), int(obj.get("idx", 0))))


def best_pullback(daily: pd.DataFrame, line: float, breakout: Dict[str, Any]) -> Dict[str, Any]:
    d = normalize_hist(daily)
    best = {"grade": "无回踩", "score_rank": 0}
    if d.empty or line <= 0 or breakout.get("idx") is None:
        return best
    bidx = int(breakout["idx"])
    if bidx < 0 or bidx >= len(d):
        return best
    bvol = sf(d.iloc[bidx]["volume"])
    pre_vol_ref = 0.0
    try:
        pre_slice = d.iloc[max(0, bidx - 5):bidx]["volume"].map(sf)
        pre_vol_ref = float(pre_slice.median()) if len(pre_slice) else 0.0
    except Exception:
        pre_vol_ref = 0.0
    pullback_vol_ref = max(bvol, pre_vol_ref * 0.80)
    end = min(len(d), bidx + 1 + PULLBACK_LOOKBACK_AFTER_BREAK)
    segment_started = False
    segment_start_idx: Optional[int] = None
    last_ok_idx: Optional[int] = None
    hold_days = 0
    shrink_days = 0
    best_base_rank = 0
    fail_seen = False
    fail_obj: Optional[Dict[str, Any]] = None
    breakaway_confirm = False
    for idx in range(bidx + 1, end):
        row = d.iloc[idx]
        prev = d.iloc[idx - 1]
        low = sf(row["low"])
        close = sf(row["close"])
        open_ = sf(row["open"])
        vol = sf(row["volume"])
        prev_close = sf(prev["close"])
        if low > line * 1.03 and not segment_started:
            continue
        close_ok = close >= line * 0.995
        defense_ok = close >= line * 0.985
        shrink = pullback_vol_ref > 0 and vol <= pullback_vol_ref * 0.75
        calm_volume = pullback_vol_ref > 0 and vol <= pullback_vol_ref * 1.05
        bad = close < open_ and pct(close, prev_close) <= -3 and pullback_vol_ref > 0 and vol >= pullback_vol_ref * 0.95
        if not segment_started:
            segment_started = True
            segment_start_idx = idx
        if not defense_ok or bad:
            fail_seen = True
            fail_obj = {"grade": "失败", "score_rank": -1, "date": ss(row["date"]), "idx": int(idx), "note": "跌破防守或放量长阴", "low": rd(low), "close": rd(close)}
            break
        if low <= line * 1.03:
            if close_ok:
                hold_days += 1
                last_ok_idx = idx
                if shrink:
                    shrink_days += 1
                br_rank0 = int(breakout.get("score_rank", 0))
                if close_ok and shrink and br_rank0 >= 5:
                    base_rank = 5
                elif close_ok and (shrink or (calm_volume and br_rank0 >= 5)) and br_rank0 >= 3:
                    base_rank = 4
                elif close_ok and br_rank0 >= 2:
                    base_rank = 2
                else:
                    base_rank = 1
            else:
                base_rank = 1
            best_base_rank = max(best_base_rank, base_rank)
        elif segment_started:
            if last_ok_idx is not None:
                rng2 = max(sf(row["high"]) - sf(row["low"]), 1e-9)
                close_pos2 = (close - sf(row["low"])) / rng2
                upward_confirm = close >= line * 1.015 and close >= open_ and close_pos2 >= 0.55 and pct(close, prev_close) >= 0
                if upward_confirm:
                    last_ok_idx = idx
                    breakaway_confirm = True
                break
    if fail_seen and fail_obj:
        return fail_obj
    if last_ok_idx is None or segment_start_idx is None or best_base_rank <= 0:
        return best
    latest = d.iloc[last_ok_idx]
    first = d.iloc[segment_start_idx]
    segment_bonus = min(0.6, max(0, hold_days - 1) * 0.15 + shrink_days * 0.05)
    final_rank = best_base_rank
    if best_base_rank >= 4 and hold_days >= 3 and shrink_days >= 2:
        final_rank = min(5, best_base_rank + 1 if best_base_rank == 4 else best_base_rank)
    grade_map = {5: "S回踩", 4: "A回踩", 2: "B观察", 1: "弱回踩"}
    grade = grade_map.get(final_rank, grade_map.get(best_base_rank, "B观察"))
    extra_note = "，回踩后向上脱离确认" if breakaway_confirm else ""
    note = f"{ss(first['date'])}首次回踩，{ss(latest['date'])}最新确认，连续{hold_days}个交易日守线，缩量{shrink_days}天{extra_note}"
    return {"grade": grade, "score_rank": int(final_rank), "date": ss(latest["date"]), "idx": int(last_ok_idx), "first_date": ss(first["date"]), "latest_confirm_date": ss(latest["date"]), "hold_days": int(hold_days), "shrink_days": int(shrink_days), "breakaway_confirm": bool(breakaway_confirm), "segment_bonus": rd(segment_bonus), "note": note, "low": rd(latest["low"]), "close": rd(latest["close"])}


def is_recent_index(idx: Any, total_len: int, lookback: int = EVENT_LOOKBACK_DAYS) -> bool:
    try:
        return int(idx) >= max(0, int(total_len) - int(lookback))
    except Exception:
        return False


def is_today_index(idx: Any, daily: pd.DataFrame) -> bool:
    try:
        i = int(idx)
        if daily is None or daily.empty or i != len(daily) - 1:
            return False
        if not TARGET_DASH:
            return True
        return ss(daily.iloc[i].get("date", "")) == TARGET_DASH
    except Exception:
        return False

def event_score(core: Dict[str, Any], breakout: Dict[str, Any], pullback: Dict[str, Any], current_close: float, event_type: str = "") -> float:
    core_base = 40 if ss(core.get("core_grade")) == "S-Core" else 30
    period_bonus = 10 if ss(core.get("period_label")) == "年线" else 0
    br_rank = int(breakout.get("score_rank", 0))
    pb_rank = max(0, int(pullback.get("score_rank", 0)))
    breakout_bonus = br_rank * 12
    pullback_bonus = pb_rank * 5 if ss(event_type) == "回踩确认" else 0
    today_bonus = 38 if ss(event_type) == "今日突破" and br_rank >= 4 else (25 if ss(event_type) == "今日突破" and br_rank >= 2 else 0)
    recent_break_bonus = 8 if bool(breakout.get("recent_breakout")) and ss(event_type) != "今日突破" else 0
    segment_bonus = sf(pullback.get("segment_bonus"), 0.0)
    distance = abs(pct(current_close, sf(core.get("line"))))
    distance_penalty = 0.0
    if ss(event_type) == "今日突破":
        if distance > 10:
            distance_penalty = 3.0 + (distance - 10) * 0.55
        elif distance > 6:
            distance_penalty = (distance - 6) * 0.35
    else:
        if distance > 120:
            distance_penalty = 8
        elif distance > 80:
            distance_penalty = 5
        elif distance > 50:
            distance_penalty = 2
    return round(core_base + period_bonus + breakout_bonus + pullback_bonus + today_bonus + recent_break_bonus + segment_bonus + sf(core.get("adhesion_score")) - distance_penalty, 3)


def latest_daily_is_target(daily: pd.DataFrame) -> bool:
    if daily is None or daily.empty:
        return False
    if not TARGET_DASH:
        return True
    try:
        return ss(daily.iloc[-1].get("date", "")) == TARGET_DASH
    except Exception:
        return False


def breakout_distance_ok_for_today(br: Dict[str, Any]) -> bool:
    rank = int(br.get("score_rank", 0))
    dist = sf(br.get("distance_pct"), 999.0)
    max_dist = MAX_TODAY_BREAK_DISTANCE_BY_RANK.get(rank, 0.0)
    if bool(br.get("near_limit_up")) and rank >= 5:
        max_dist = max(max_dist, min(MAX_TODAY_LIMIT_BREAK_DISTANCE_CAP, sf(br.get("pct_chg"), 0.0) + 3.0))
    return rank >= 3 and dist <= max_dist


def latest_line_not_damaged(daily: pd.DataFrame, line: float) -> bool:
    d = normalize_hist(daily)
    if d.empty or line <= 0:
        return False
    row = d.iloc[-1]
    prev = d.iloc[-2] if len(d) >= 2 else row
    open_ = sf(row["open"])
    high = sf(row["high"])
    low = sf(row["low"])
    close = sf(row["close"])
    volume = sf(row.get("volume", 0.0)) if hasattr(row, "get") else sf(row["volume"])
    prev_close = sf(prev["close"])
    prev_volume = sf(prev.get("volume", 0.0)) if hasattr(prev, "get") else sf(prev["volume"])
    vol_ratio = volume / prev_volume if prev_volume > 0 else 0.0
    rng = max(high - low, 1e-9)
    close_pos = (close - low) / rng
    body_down = close < open_
    close_accepted = close >= line * RECENT_BREAK_MIN_CLOSE_ABOVE_LINE
    if not close_accepted:
        return False
    heavy_bear = body_down and pct(close, prev_close) <= -3.0 and vol_ratio >= 1.30
    failed_retest = high >= line * 1.025 and close < line * 1.005 and close_pos <= 0.42 and vol_ratio >= 1.25
    return not (heavy_bear or failed_retest)


def build_breakout_events(daily: pd.DataFrame, core_lines: List[Dict[str, Any]], current_close: float) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    d = normalize_hist(daily)
    n = len(d)
    if n == 0:
        return events
    data_fresh = latest_daily_is_target(d)
    for core in core_lines:
        line = sf(core.get("line"))
        if line <= 0:
            continue
        breakouts = collect_breakouts(d, line)
        if not breakouts:
            continue
        for br in breakouts:
            br = dict(br)
            recent_break = is_recent_index(br.get("idx"), n)
            today_break = data_fresh and is_today_index(br.get("idx"), d)
            pb = best_pullback(d, line, br)
            recent_pullback = is_recent_index(pb.get("idx"), n)
            today_pullback = data_fresh and is_today_index(pb.get("idx"), d)

            line_not_damaged_now = latest_line_not_damaged(d, line)
            today_tradeable_break = today_break and (not bool(br.get("reclaim_only"))) and breakout_distance_ok_for_today(br)
            if today_tradeable_break:
                event_type = "今日突破"
            elif today_pullback and int(pb.get("score_rank", 0)) >= 4 and line_not_damaged_now:
                event_type = "回踩确认"
            elif data_fresh and recent_break and int(br.get("score_rank", 0)) >= 2 and line_not_damaged_now and recent_break_distance_ok(current_close, line):
                event_type = "近期突破"
                if today_break and not breakout_distance_ok_for_today(br):
                    if int(br.get("score_rank", 0)) < 3:
                        br["note"] = ss(br.get("note")) + "｜今日普通破界，等级不足，降为观察"
                    else:
                        br["note"] = ss(br.get("note")) + "｜离核心线过远，降为观察"
            else:
                continue
            br["recent_breakout"] = bool(recent_break)
            br["today_breakout"] = bool(today_break)
            pb["today_pullback"] = bool(today_pullback)
            ev_score = event_score(core, br, pb, current_close, event_type)
            ev = {
                "core": core,
                "breakout": br,
                "pullback": pb,
                "event_type": event_type,
                "event_score": ev_score,
                "recent_breakout": bool(recent_break),
                "today_breakout": bool(today_break),
                "recent_pullback": bool(recent_pullback),
                "today_pullback": bool(today_pullback),
                "line_not_damaged_now": bool(line_not_damaged_now),
                "line": rd(line),
                "core_grade": ss(core.get("core_grade")),
                "period_label": ss(core.get("period_label")),
                "breakout_grade": ss(br.get("grade")),
                "breakout_date": ss(br.get("date")),
                "pullback_grade": ss(pb.get("grade")),
                "pullback_date": ss(pb.get("date")),
            }
            events.append(ev)
    deduped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for ev in events:
        key_date = ss(ev.get("pullback_date")) if ss(ev.get("event_type")) == "回踩确认" else ss(ev.get("breakout_date"))
        key = (round(sf(ev.get("line")), 3), ss(ev.get("event_type")), key_date)
        if key not in deduped or sf(ev.get("event_score")) > sf(deduped[key].get("event_score")):
            deduped[key] = ev
    events = list(deduped.values())

    def rank_event(x: Dict[str, Any]) -> Tuple[Any, ...]:
        type_rank = {"今日突破": 3, "回踩确认": 2, "近期突破": 1}.get(ss(x.get("event_type")), 0)
        br_rank = int((x.get("breakout") or {}).get("score_rank", 0))
        pb_rank = int((x.get("pullback") or {}).get("score_rank", 0))
        return (type_rank, sf(x.get("event_score")), br_rank, pb_rank)
    return sorted(events, key=rank_event, reverse=True)

def pack_core(prefix: str, x: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not x:
        return {f"{prefix}核心线": None, f"{prefix}等级": "无", f"{prefix}周期": "", f"{prefix}有效共振": 0, f"{prefix}离散共振": 0, f"{prefix}外部有效": 0, f"{prefix}外部影线": 0, f"{prefix}带量共振": 0, f"{prefix}倍量共振": 0, f"{prefix}切实体": 0, f"{prefix}距现价%": None, f"{prefix}锚点": "", f"{prefix}主簇": "", f"{prefix}反应中心": None, f"{prefix}粘合分": None, f"{prefix}中心偏离%": None, f"{prefix}紧密误差%": None, f"{prefix}样本": "", f"{prefix}类型": "", f"{prefix}路径": ""}
    volume_reaction = int(x.get("volume_reaction_count", 0))
    double_reaction = int(x.get("double_reaction_count", 0))
    return {f"{prefix}核心线": rd(x.get("line")), f"{prefix}等级": ss(x.get("core_grade")), f"{prefix}周期": ss(x.get("period_label")), f"{prefix}有效共振": int(x.get("effective_unique_count", 0)), f"{prefix}离散共振": int(x.get("primary_unique_count", 0)), f"{prefix}外部有效": int(x.get("external_effective_count", 0)), f"{prefix}外部影线": int(x.get("external_shadow_count", 0)), f"{prefix}带量共振": volume_reaction, f"{prefix}倍量共振": double_reaction, f"{prefix}切实体": int(x.get("cut_count", 0)), f"{prefix}距现价%": rd(x.get("distance_pct")), f"{prefix}锚点": ss(x.get("anchor_period")), f"{prefix}主簇": f"{rd(x.get('cluster_low'))}-{rd(x.get('cluster_high'))}", f"{prefix}反应中心": rd(x.get("reaction_center")), f"{prefix}粘合分": rd(x.get("adhesion_score")), f"{prefix}中心偏离%": rd(x.get("center_bias_pct")), f"{prefix}紧密误差%": rd(x.get("compact_error_pct")), f"{prefix}样本": ss(x.get("sample_periods")), f"{prefix}类型": ss(x.get("sample_kinds")), f"{prefix}路径": ss(x.get("selection_path"))}

def final_grade(near: Optional[Dict[str, Any]], breakout: Dict[str, Any], pullback: Dict[str, Any], event_type: str = "") -> str:
    if not near:
        return "无核心线"
    core = ss(near.get("core_grade"))
    pb = ss(pullback.get("grade"))
    br_rank = int(breakout.get("score_rank", 0))
    today = ss(event_type) == "今日突破"
    if today and br_rank >= 6:
        return "S+｜今日跳空打板破界"
    if today and br_rank >= 5:
        return "S｜今日高质量跳空破界"
    if today and br_rank >= 4:
        return "A+｜今日倍量强实体破界"
    if today and br_rank >= 3:
        return "A｜今日有效实体破界"
    if today and br_rank >= 2:
        return "B｜今日普通破界，等确认"
    if core == "S-Core" and pb == "S回踩":
        return "A｜强突破后黄金回踩"
    if core in {"S-Core", "A-Core"} and pb in {"S回踩", "A回踩"}:
        return "A-｜回踩确认"
    if br_rank >= 5:
        return "B+｜近期强突破，等回踩"
    if br_rank >= 3:
        return "B｜近期突破有效，等回踩"
    return "C｜核心线成立，等触发"


def classify_failure_reason(status: str) -> str:
    s = ss(status)
    if s == "完成":
        return "完成"
    if "timed out" in s or "Read timed" in s or "timeout" in s or "超时" in s:
        return "网络超时"
    if "Connection" in s or "连接" in s or "HTTP请求失败" in s:
        return "连接失败/接口波动"
    if "K线为空" in s or "为空" in s:
        return "K线为空"
    if "json" in s.lower() or "解析" in s:
        return "返回解析失败"
    if "失败" in s:
        return "其他失败"
    return s or "未知"


def analyze_one(code: str, name_hint: str = "") -> Dict[str, Any]:
    ref_close, cache_path = cache_reference_close(code)
    adjust_flag, adjust_info = choose_adjust_flag(code, ref_close)
    core_lines, _ = scan_core_lines_progressive(code, adjust_flag)
    if not core_lines:
        current_close = 0.0
        name = name_hint or ("长电科技" if code == "600584" else "")
        return {"股票代码": code, "股票中文名称": name, "当前收盘": None, "状态": "完成", "数据源": "东方财富直接年线K", "正式核心线数量": 0, "最终等级": "无核心线", "破界事件数": 0, "最佳事件分": 0.0, "全部核心线": [], "全部破界事件": [], "failure_reason": "完成"}
    daily = fetch_event_daily(code, adjust_flag)
    if daily.empty:
        return {"股票代码": code, "状态": "东方财富日线为空", "adjust_flag": adjust_flag, "failure_reason": "K线为空"}
    name = name_hint or ("长电科技" if code == "600584" else "")
    daily = daily.copy()
    daily["code"] = code
    daily["name"] = name
    current_close = sf(daily.iloc[-1]["close"])
    latest_date = ss(daily.iloc[-1]["date"])
    data_fresh = (not TARGET_DASH) or latest_date == TARGET_DASH
    near, far = select_near_far(core_lines, current_close)
    events = build_breakout_events(daily, core_lines, current_close) if data_fresh else []
    best_event = events[0] if events else None
    event_core = best_event["core"] if best_event else near
    if best_event:
        breakout = best_event["breakout"]
        pullback = best_event["pullback"]
        event_type = ss(best_event.get("event_type"))
        final_label = final_grade(event_core, breakout, pullback, event_type)
    else:
        breakout = {"grade": "无当期事件", "score_rank": 0, "date": "", "note": "未形成目标日破界/回踩事件"}
        pullback = {"grade": "无当期回踩", "score_rank": 0, "date": "", "note": "未形成目标日回踩确认"}
        event_type = ""
        final_label = "C｜年线核心线成立，等触发" if data_fresh else f"数据未更新到目标日{TARGET_DASH}"
    row: Dict[str, Any] = {"股票代码": code, "股票中文名称": name, "最新日期": latest_date, "当前收盘": rd(current_close), "状态": "完成", "数据新鲜": bool(data_fresh), "数据新鲜说明": ("目标日数据" if data_fresh else f"日线未更新到目标日{TARGET_DASH}"), "数据源": "东方财富直接日线/年线K", "年线来源": "核心线仅取东方财富直接年线K；日线只做突破/回踩事件", "adjust_flag": adjust_flag, "复权口径": ADJUST_FLAG_NAMES.get(adjust_flag, adjust_flag), "adjust_info": adjust_info, "cache_reference_close": rd(ref_close), "cache_path": cache_path, "正式核心线数量": len(core_lines), "突破等级": breakout.get("grade"), "突破日期": breakout.get("date", ""), "突破说明": breakout.get("note", ""), "回踩等级": pullback.get("grade"), "回踩日期": pullback.get("date", ""), "回踩首次日期": pullback.get("first_date", ""), "回踩最新确认日期": pullback.get("latest_confirm_date", ""), "回踩连续守线天数": pullback.get("hold_days", 0), "回踩缩量天数": pullback.get("shrink_days", 0), "回踩说明": pullback.get("note", ""), "事件类型": event_type, "最终等级": final_label, "破界事件数": len(events), "最佳事件分": sf(best_event.get("event_score")) if best_event else 0.0, "近端详情": near or {}, "远端详情": far or {}, "事件核心线详情": event_core or {}, "全部核心线": core_lines, "全部破界事件": events, "突破详情": breakout, "回踩详情": pullback, "failure_reason": "完成"}
    row.update(pack_core("近端", near))
    row.update(pack_core("远端", far))
    row.update(pack_core("事件", event_core))
    return row


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    try:
        if pd.isna(obj) and not isinstance(obj, (str, dict, list, tuple)):
            return None
    except Exception:
        pass
    return obj


def cn_core_grade(x: Any) -> str:
    s = ss(x)
    if s == "S-Core":
        return "S级核心线"
    if s == "A-Core":
        return "A级核心线"
    return s or "核心线"


def core_trade_sentence(prefix: str, r: Dict[str, Any]) -> str:
    return f"核心线：{r.get(prefix+'核心线')}｜{r.get(prefix+'周期')}{cn_core_grade(r.get(prefix+'等级'))}｜{r.get(prefix+'主簇')}附近反复反应{r.get(prefix+'有效共振')}次；锚点{r.get(prefix+'锚点')}；带量共振{r.get(prefix+'带量共振')}次；倍量共振{r.get(prefix+'倍量共振')}次；切实体{r.get(prefix+'切实体')}"


def failure_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        reason = ss(r.get("failure_reason")) or classify_failure_reason(ss(r.get("状态")))
        if reason == "完成":
            continue
        out[reason] = out.get(reason, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


def event_type_of_row(r: Dict[str, Any]) -> str:
    return ss(r.get("事件类型")) or ss(((r.get("全部破界事件") or [{}])[0]).get("event_type"))


def build_report(rows: List[Dict[str, Any]]) -> str:
    completed = [r for r in rows if ss(r.get("状态")) == "完成"]
    event_rows = [r for r in completed if int(r.get("破界事件数", 0)) > 0]
    today_rows = [r for r in event_rows if event_type_of_row(r) == "今日突破"]
    pullback_rows = [r for r in event_rows if event_type_of_row(r) == "回踩确认"]
    recent_rows = [r for r in event_rows if event_type_of_row(r) == "近期突破"]
    today_rows = sorted(today_rows, key=lambda r: sf(r.get("最佳事件分")), reverse=True)
    pullback_rows = sorted(pullback_rows, key=lambda r: sf(r.get("最佳事件分")), reverse=True)
    recent_rows = sorted(recent_rows, key=lambda r: sf(r.get("最佳事件分")), reverse=True)
    event_rows = today_rows + pullback_rows + recent_rows
    total = len(rows)
    completion_rate = len(completed) / total if total else 0.0
    fail_sum = failure_summary(rows)

    def append_stock(lines: List[str], i: int, r: Dict[str, Any]) -> None:
        lines.extend([
            f"{i}. {r.get('股票代码')} {r.get('股票中文名称')}｜{r.get('最终等级')}｜事件分{r.get('最佳事件分')}｜收盘{r.get('当前收盘')}",
            f"   {core_trade_sentence('事件', r)}",
            f"   突破：{r.get('突破等级')}｜{r.get('突破日期')}｜{r.get('突破说明')}",
            f"   回踩：{r.get('回踩等级')}｜{r.get('回踩说明')}",
            "",
        ])

    if RUN_FULL_MARKET:
        status_line = "全市场完成" if completion_rate >= MIN_COMPLETION_RATE else "完成率不足，本次不作为正式全市场Top"
        lines = [
            f"破界全市场海选｜{TARGET_DASH or TARGET}",
            "数据：东方财富直接日线/年线K，默认前复权；核心线仅限年线。",
            "排序：今日高质量突破优先，其次才是黄金回踩；回踩不再压过当天S/A级破界。",
            "突破分层：S+跳空打板；S实体打板/跳空整实体在线上；A+倍量强实体上穿；A高质量实体破界；B普通实体破界。",
            f"扫描：{total}只｜完成：{len(completed)}只｜完成率：{completion_rate:.1%}｜今日高质量突破：{len(today_rows)}只｜回踩确认：{len(pullback_rows)}只｜近期突破：{len(recent_rows)}只｜{status_line}",
            f"运行参数：并发{WORKERS}｜超时{REQUEST_TIMEOUT}s｜重试{REQUEST_RETRIES}次｜失败补扫={'开' if RETRY_FAILED_PASS else '关'}",
            "",
        ]
        if fail_sum:
            top_fail = "；".join(f"{k}{v}只" for k, v in list(fail_sum.items())[:5])
            lines.append(f"失败原因：{top_fail}")
            lines.append("")
        if completion_rate < MIN_COMPLETION_RATE:
            lines.append("完成率低于有效线，不输出正式全市场Top；以下仅作阶段结果。")
            lines.append("")
        lines.append("【第一池｜今日高质量破界】")
        if today_rows:
            for i, r in enumerate(today_rows[:TOP_PUSH_LIMIT], 1):
                append_stock(lines, i, r)
        else:
            lines.append("今日暂无符合条件的高质量破界。")
            lines.append("")
        remain = max(0, TOP_PUSH_LIMIT - len(today_rows[:TOP_PUSH_LIMIT]))
        lines.append("【第二池｜黄金回踩确认】")
        if pullback_rows and remain > 0:
            for i, r in enumerate(pullback_rows[:remain], 1):
                append_stock(lines, i, r)
        elif pullback_rows:
            lines.append("今日突破池已占满推送名额，回踩池见完整CSV/JSON。")
            lines.append("")
        else:
            lines.append("暂无符合条件的回踩确认。")
            lines.append("")
        if not today_rows and not pullback_rows and recent_rows:
            lines.append("【补充｜近期突破，等待回踩】")
            for i, r in enumerate(recent_rows[:min(TOP_PUSH_LIMIT, 5)], 1):
                append_stock(lines, i, r)
        if not event_rows:
            lines.append("今日未筛出最近窗口内的高质量破界事件。")
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3850].rstrip() + "\n……\n报告过长，完整明细见 artifact。"
        return text

    lines = [
        f"破界单票/多票验证｜{TARGET_DASH or TARGET}",
        "数据：东方财富直接日线/年线K；核心线仅限年线。",
        "排序：今日突破优先于回踩。",
        "",
    ]
    for r in event_rows or rows:
        lines.extend([
            f"{r.get('股票代码')} {r.get('股票中文名称')}｜{event_type_of_row(r)}｜收盘 {r.get('当前收盘')}｜{r.get('最终等级')}｜事件分{r.get('最佳事件分')}",
            core_trade_sentence('事件', r),
            f"突破：{r.get('突破等级')}｜{r.get('突破日期')}｜{r.get('突破说明')}",
            f"回踩：{r.get('回踩等级')}｜{r.get('回踩说明')}",
            "",
        ])
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n……\n报告过长，完整明细见 artifact。"
    return text

def flatten_core_line(row: Dict[str, Any], core: Dict[str, Any]) -> Dict[str, Any]:
    volume_reaction = int(core.get("volume_reaction_count", 0) or 0)
    double_reaction = int(core.get("double_reaction_count", 0) or 0)
    return {"code": row.get("股票代码"), "name": row.get("股票中文名称"), "current_close": row.get("当前收盘"), "line": rd(core.get("line")), "core_grade": ss(core.get("core_grade")), "period_label": ss(core.get("period_label")), "effective_unique_count": int(core.get("effective_unique_count", 0)), "primary_unique_count": int(core.get("primary_unique_count", 0)), "external_effective_count": int(core.get("external_effective_count", 0)), "external_shadow_count": int(core.get("external_shadow_count", 0)), "volume_reaction_count": volume_reaction, "double_reaction_count": double_reaction, "cut_count": int(core.get("cut_count", 0)), "anchor_period": ss(core.get("anchor_period")), "anchor_kind": ss(core.get("anchor_kind")), "cluster_low": rd(core.get("cluster_low")), "cluster_high": rd(core.get("cluster_high")), "reaction_center": rd(core.get("reaction_center")), "adhesion_score": rd(core.get("adhesion_score")), "cut_penalty": rd(core.get("cut_penalty")), "center_bias_pct": rd(core.get("center_bias_pct")), "compact_error_pct": rd(core.get("compact_error_pct")), "sample_periods": ss(core.get("sample_periods")), "sample_kinds": ss(core.get("sample_kinds")), "selection_path": ss(core.get("selection_path"))}

def flatten_event(row: Dict[str, Any], ev: Dict[str, Any]) -> Dict[str, Any]:
    core = ev.get("core") or {}
    br = ev.get("breakout") or {}
    pb = ev.get("pullback") or {}
    out = flatten_core_line(row, core)
    out.update({"event_type": ss(ev.get("event_type")), "event_score": rd(ev.get("event_score")), "today_breakout": bool(ev.get("today_breakout")), "recent_breakout": bool(ev.get("recent_breakout")), "recent_pullback": bool(ev.get("recent_pullback")), "today_pullback": bool(ev.get("today_pullback")), "line_not_damaged_now": bool(ev.get("line_not_damaged_now")), "breakout_grade": ss(br.get("grade")), "breakout_date": ss(br.get("date")), "breakout_close": rd(br.get("close")), "breakout_distance_pct": rd(br.get("distance_pct")), "breakout_path": ss(br.get("breakout_path")), "breakout_entity_above_line_ratio": rd(br.get("entity_above_line_ratio")), "breakout_vol_ratio_prev": rd(br.get("vol_ratio_prev")), "breakout_gap_break": bool(br.get("gap_break")), "breakout_full_body_above_line": bool(br.get("full_body_above_line")), "breakout_near_limit_up": bool(br.get("near_limit_up")), "breakout_reclaim_only": bool(br.get("reclaim_only")), "breakout_reclaim_context": bool(br.get("reclaim_context")), "breakout_pct_chg": rd(br.get("pct_chg")), "breakout_note": ss(br.get("note")), "pullback_grade": ss(pb.get("grade")), "pullback_date": ss(pb.get("date")), "pullback_first_date": ss(pb.get("first_date")), "pullback_latest_confirm_date": ss(pb.get("latest_confirm_date")), "pullback_hold_days": int(pb.get("hold_days", 0) or 0), "pullback_shrink_days": int(pb.get("shrink_days", 0) or 0), "pullback_note": ss(pb.get("note"))})
    return out


def write_outputs(rows: List[Dict[str, Any]], report_text: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report_text, encoding="utf-8")
    flat_rows = [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in rows]
    pd.DataFrame(flat_rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    core_rows: List[Dict[str, Any]] = []
    event_rows: List[Dict[str, Any]] = []
    for r in rows:
        for core in r.get("全部核心线", []) or []:
            core_rows.append(flatten_core_line(r, core))
        for ev in r.get("全部破界事件", []) or []:
            event_rows.append(flatten_event(r, ev))
    pd.DataFrame(core_rows).to_csv(OUTPUT_CORE_MAP, index=False, encoding="utf-8-sig")
    event_columns = ["code", "name", "current_close", "line", "core_grade", "period_label", "effective_unique_count", "primary_unique_count", "external_effective_count", "external_shadow_count", "volume_reaction_count", "double_reaction_count", "cut_count", "anchor_period", "anchor_kind", "cluster_low", "cluster_high", "reaction_center", "adhesion_score", "cut_penalty", "center_bias_pct", "compact_error_pct", "sample_periods", "sample_kinds", "selection_path", "event_type", "event_score", "today_breakout", "recent_breakout", "recent_pullback", "today_pullback", "line_not_damaged_now", "breakout_grade", "breakout_date", "breakout_close", "breakout_distance_pct", "breakout_path", "breakout_entity_above_line_ratio", "breakout_vol_ratio_prev", "breakout_gap_break", "breakout_full_body_above_line", "breakout_near_limit_up", "breakout_reclaim_only", "breakout_reclaim_context", "breakout_pct_chg", "breakout_note", "pullback_grade", "pullback_date", "pullback_first_date", "pullback_latest_confirm_date", "pullback_hold_days", "pullback_shrink_days", "pullback_note"]
    event_df = pd.DataFrame(event_rows, columns=event_columns)
    if not event_df.empty:
        type_priority = {"今日突破": 3, "回踩确认": 2, "近期突破": 1}
        event_df["_type_priority"] = event_df["event_type"].map(type_priority).fillna(0).astype(int)
        event_df = event_df.sort_values(["_type_priority", "event_score"], ascending=[False, False]).drop(columns=["_type_priority"])
    event_df.to_csv(OUTPUT_EVENTS, index=False, encoding="utf-8-sig")
    payload = {"boot": BOOT, "run_mode": RUN_MODE, "full_market": RUN_FULL_MARKET, "target": TARGET, "target_dash": TARGET_DASH, "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"), "completion": {"total": len(rows), "completed": sum(1 for r in rows if ss(r.get("状态")) == "完成"), "completion_rate": sum(1 for r in rows if ss(r.get("状态")) == "完成") / len(rows) if rows else 0.0, "failure_summary": failure_summary(rows)}, "config": {"source": "eastmoney_direct_kline", "klt": {"D": 101, "Y": 106}, "adjust_flag_env": ADJUST_FLAG_ENV, "point_tol": POINT_TOL, "body_edge_tol": BODY_EDGE_TOL, "shadow_reaction_weight": SHADOW_REACTION_WEIGHT, "event_lookback_days": EVENT_LOOKBACK_DAYS, "workers": WORKERS, "request_timeout": REQUEST_TIMEOUT, "request_retries": REQUEST_RETRIES, "max_stocks": MAX_STOCKS, "core_selection_mode": CORE_SELECTION_MODE, "retry_failed_pass": RETRY_FAILED_PASS, "failed_retry_workers": FAILED_RETRY_WORKERS, "min_completion_rate": MIN_COMPLETION_RATE, "core_scope": "year_only", "year_core_allow_one_cut_strong": YEAR_CORE_ALLOW_ONE_CUT_STRONG, "recent_break_min_close_above_line": RECENT_BREAK_MIN_CLOSE_ABOVE_LINE, "recent_break_max_distance": RECENT_BREAK_MAX_DISTANCE, "reclaim_context_lookback_days": RECLAIM_CONTEXT_LOOKBACK_DAYS, "reclaim_context_touch_days": RECLAIM_CONTEXT_TOUCH_DAYS}, "rows": rows}
    OUTPUT_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    SELF_CHECK_JSON.write_text(json.dumps({"status": "PASS", "boot": BOOT, "run_mode": RUN_MODE, "full_market": RUN_FULL_MARKET, "outputs": {"report": str(OUTPUT_MD), "detail": str(OUTPUT_CSV), "core_line_map": str(OUTPUT_CORE_MAP), "breakout_events": str(OUTPUT_EVENTS)}}, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    if not SEND_TELEGRAM or not BOT or not CHAT or requests is None:
        log(f"Telegram跳过 enable={SEND_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}")
        print(text, flush=True)
        return
    try:
        resp = get_http_session().post(f"https://api.telegram.org/bot{BOT}/sendMessage", json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True}, timeout=30)
        log(f"Telegram status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:160]}")
    except Exception as exc:
        log(f"Telegram发送失败：{exc}")


def safe_analyze(code: str, name_hint: str) -> Dict[str, Any]:
    try:
        row = analyze_one(code, name_hint)
        row["failure_reason"] = classify_failure_reason(ss(row.get("状态"))) if ss(row.get("状态")) != "完成" else "完成"
        return row
    except Exception as exc:
        status = f"失败：{exc}"
        return {"股票代码": code, "股票中文名称": name_hint, "当前收盘": None, "状态": status, "最终等级": "失败", "破界事件数": 0, "最佳事件分": 0.0, "failure_reason": classify_failure_reason(status)}


def append_progress(row: Dict[str, Any]) -> None:
    if not RESUME_ENABLED or PROGRESS_JSONL is None:
        return
    try:
        PROGRESS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with PROGRESS_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(json_safe(row), ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_progress() -> Dict[str, Dict[str, Any]]:
    if not RESUME_ENABLED or PROGRESS_JSONL is None or not PROGRESS_JSONL.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for line in PROGRESS_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            code = code_of(obj.get("股票代码"))
            if code:
                out[code] = obj
    except Exception:
        return {}
    return out


def run_batch(codes: List[str], name_map: Dict[str, str], workers: int, existing_rows: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    existing_rows = existing_rows or {}
    rows: List[Dict[str, Any]] = []
    pending = [c for c in codes if c not in existing_rows]
    rows.extend(existing_rows[c] for c in codes if c in existing_rows)
    if not pending:
        return rows
    if RUN_FULL_MARKET and workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(safe_analyze, code, name_map.get(code, "")): code for code in pending}
            done = 0
            for fut in as_completed(futs):
                row = fut.result()
                rows.append(row)
                append_progress(row)
                done += 1
                if done % PARTIAL_SAVE_EVERY == 0 or done == len(pending):
                    completed_count = sum(1 for r in rows if ss(r.get("状态")) == "完成")
                    event_count = sum(1 for r in rows if int(r.get("破界事件数", 0)) > 0)
                    log(f"进度 {done}/{len(pending)}｜总完成 {completed_count}/{len(rows)}｜事件 {event_count}")
    else:
        for idx, code in enumerate(pending, 1):
            row = safe_analyze(code, name_map.get(code, ""))
            rows.append(row)
            append_progress(row)
            log(f"完成 {idx}/{len(pending)} {code}｜状态={row.get('状态')}｜事件={row.get('破界事件数')}｜等级={row.get('最终等级')}")
    by_code = {code_of(r.get("股票代码")): r for r in rows if code_of(r.get("股票代码"))}
    return [by_code[c] for c in codes if c in by_code]


def main() -> None:
    print(BOOT, flush=True)
    print(f"RUN_MODE={RUN_MODE}", flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print(f"run_full_market={RUN_FULL_MARKET}", flush=True)
    print("target_codes=" + (",".join(TARGET_CODES) if TARGET_CODES else "FULL_MARKET"), flush=True)
    print("data_source=eastmoney_direct_year_core_fullmarket", flush=True)
    print(f"workers={WORKERS} requested_workers={REQUESTED_WORKERS} stable_workers={STABLE_FULLMARKET_WORKERS} max_stocks={MAX_STOCKS} event_lookback={EVENT_LOOKBACK_DAYS}", flush=True)
    print(f"core_selection_mode={CORE_SELECTION_MODE} timeout={REQUEST_TIMEOUT}s retries={REQUEST_RETRIES} resume={RESUME_ENABLED} retry_failed_pass={RETRY_FAILED_PASS}", flush=True)
    codes, name_map = resolve_run_universe()
    log(f"准备扫描：{len(codes)} 只")
    progress = load_progress()
    if progress:
        log(f"载入断点：{len(progress)} 只")
    rows = run_batch(codes, name_map, WORKERS, progress)
    if RETRY_FAILED_PASS and RUN_FULL_MARKET:
        failed_codes = [code_of(r.get("股票代码")) for r in rows if ss(r.get("状态")) != "完成" and code_of(r.get("股票代码"))]
        if failed_codes:
            log(f"首轮失败 {len(failed_codes)} 只，启动低并发补扫 workers={FAILED_RETRY_WORKERS}")
            current_by_code = {code_of(r.get("股票代码")): r for r in rows if code_of(r.get("股票代码"))}
            for c in failed_codes:
                current_by_code.pop(c, None)
            retry_name_map = {c: name_map.get(c, "") for c in failed_codes}
            retry_rows = run_batch(failed_codes, retry_name_map, FAILED_RETRY_WORKERS, {})
            for r in retry_rows:
                current_by_code[code_of(r.get("股票代码"))] = r
            rows = [current_by_code[c] for c in codes if c in current_by_code]
    report_text = build_report(rows)
    write_outputs(rows, report_text)
    send_telegram(report_text)
    completed = sum(1 for r in rows if ss(r.get("状态")) == "完成")
    log(f"done completed={completed}/{len(rows)} report={OUTPUT_MD} detail={OUTPUT_CSV} core_map={OUTPUT_CORE_MAP} events={OUTPUT_EVENTS}")


if __name__ == "__main__":
    main()
