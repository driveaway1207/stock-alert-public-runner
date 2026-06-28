# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py｜破界｜全市场直接年季月K版 V11

核心修正：
1）不再用本地缓存日线聚合年/季/月；
2）不再用 BaoStock 月线聚合年线/季线；
3）直接拉东方财富前复权/后复权/不复权的日线、月线、季线、年线 K；
4）年线/季线/月线全部使用交易端直接多周期 K，不再捏合；
5）核心线逻辑仍然是：实体顶候选 + 离散反应点 + 外部影线有效反应 + K线粘合代表性；
6）默认全市场扫描；设置 POJIE_TARGET_CODES 时进入单票/多票验证模式。

说明：
BaoStock 常用接口稳定支持 d/w/m，不稳定直接支持 q/y。
为了按你说的“年线、季线、月线直接拿回来”，本版改用东方财富 K 线接口直接取：
日线 klt=101，月线 klt=103，季线 klt=104，年线 klt=106。
前复权 fqt=1，后复权 fqt=2，不复权 fqt=0。
"""

import json
import math
import os
import re
import time
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


BOOT = "POJIE_FULLMARKET_DIRECT_YQM_EASTMONEY_V11_20260628"
RUN_MODE = "full_market_default; direct_yqm_kline_source; target_codes_optional"
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
# Eastmoney fqt: 1=前复权, 2=后复权, 0=不复权。
ADJUST_FLAG_NAMES = {"1": "前复权", "2": "后复权", "0": "不复权"}

EASTMONEY_CACHE_DIR = ROOT / "kline_cache" / "eastmoney_direct"
UNIVERSE_CACHE = EASTMONEY_CACHE_DIR / "universe_a_share.csv"
EASTMONEY_UNIVERSE_FS = os.getenv(
    "POJIE_EASTMONEY_FS",
    "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80"
)
MAX_STOCKS = int(os.getenv("POJIE_MAX_STOCKS", "0"))
WORKERS = max(1, int(os.getenv("POJIE_WORKERS", "6")))
EVENT_LOOKBACK_DAYS = int(os.getenv("POJIE_EVENT_LOOKBACK_DAYS", "20"))
TOP_PUSH_LIMIT = int(os.getenv("POJIE_TOP_PUSH_LIMIT", "20"))

POINT_TOL = float(os.getenv("POJIE_POINT_TOL", "0.005"))
BODY_EDGE_TOL = float(os.getenv("POJIE_BODY_EDGE_TOL", "0.005"))
SHADOW_REACTION_WEIGHT = float(os.getenv("POJIE_SHADOW_REACTION_WEIGHT", "0.70"))

PERIOD_SPECS = [
    ("Y", "年线", 4, 3),
    ("Q", "季线", 8, 2),
    ("M", "月线", 10, 1),
]

BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "260"))
PULLBACK_LOOKBACK_AFTER_BREAK = int(os.getenv("POJIE_PULLBACK_LOOKBACK_AFTER_BREAK", "80"))


def log(msg: str) -> None:
    print(f"[破界V11][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


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


def resolve_target_raw() -> str:
    for key in TARGET_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value

    d = now_bj()
    if d.weekday() >= 5 or d.hour < 20 or (d.hour == 20 and d.minute < 30):
        d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", resolve_target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""
TARGET_TS = pd.Timestamp(TARGET_DASH) if TARGET_DASH else pd.Timestamp.today()



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
    # 东方财富 K 线周期：101日，103月，104季，106年。
    return {"D": 101, "M": 103, "Q": 104, "Y": 106}[period]


def eastmoney_cache_path(code: str, period: str, fqt: str) -> Path:
    return EASTMONEY_CACHE_DIR / f"fqt_{fqt}" / period / f"{code}.csv"


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
    if period == "M":
        return latest.to_period("M") >= target.to_period("M")
    if period == "Q":
        return latest.to_period("Q") >= target.to_period("Q")
    if period == "Y":
        return latest.to_period("Y") >= target.to_period("Y")
    return False


def fetch_eastmoney_kline(code: str, period: str, fqt: str, begin: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    if requests is None:
        raise RuntimeError("requests 不可用，无法拉取东方财富K线")

    cache_path = eastmoney_cache_path(code, period, fqt)
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

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    obj = resp.json()
    data = obj.get("data") or {}
    klines = data.get("klines") or []
    rows: List[Dict[str, Any]] = []

    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 11:
            continue
        rows.append({
            "date": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "volume": parts[5],
            "amount": parts[6],
            "amplitude": parts[7],
            "pct_chg": parts[8],
            "change": parts[9],
            "turnover": parts[10],
            "code": code,
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

    # 正常不会走到这里；保留自动匹配能力。
    if reference_close <= 0:
        return "1", {"reason": "default_qfq_no_reference", "reference_close": reference_close}

    begin = (TARGET_TS - pd.Timedelta(days=80)).strftime("%Y%m%d")
    end = TARGET.replace("-", "")

    candidates: List[Dict[str, Any]] = []
    for flag in ["1", "2", "0"]:
        try:
            d = fetch_eastmoney_kline(code, "D", flag, begin, end, use_cache=False)
            latest = sf(d.iloc[-1]["close"]) if not d.empty else 0.0
            candidates.append({
                "flag": flag,
                "name": ADJUST_FLAG_NAMES.get(flag, flag),
                "latest_close": rd(latest),
                "diff": abs(latest - reference_close) if latest > 0 else 1e18,
            })
        except Exception as exc:
            candidates.append({
                "flag": flag,
                "name": ADJUST_FLAG_NAMES.get(flag, flag),
                "latest_close": 0.0,
                "diff": 1e18,
                "error": str(exc)[:120],
            })

    best = min(candidates, key=lambda x: sf(x["diff"], 1e18))
    return ss(best["flag"]), {
        "reason": "match_cache_reference_close",
        "reference_close": rd(reference_close),
        "candidates": candidates,
        "chosen": best,
    }


def prepare_direct_period_bars(raw: pd.DataFrame, period: str) -> pd.DataFrame:
    d = normalize_hist(raw)
    if d.empty:
        return pd.DataFrame()

    dt = pd.to_datetime(d["date"], errors="coerce")
    d = d[dt.notna()].copy()
    if d.empty:
        return pd.DataFrame()

    if period == "Y":
        d["period"] = pd.to_datetime(d["date"]).dt.to_period("Y").astype(str)
        target_period = pd.Timestamp(TARGET_DASH).to_period("Y")
    elif period == "Q":
        d["period"] = pd.to_datetime(d["date"]).dt.to_period("Q").astype(str)
        target_period = pd.Timestamp(TARGET_DASH).to_period("Q")
    else:
        d["period"] = pd.to_datetime(d["date"]).dt.to_period("M").astype(str)
        target_period = pd.Timestamp(TARGET_DASH).to_period("M")

    # 直接多周期K也要排除当前未完成周期。
    d = d[pd.to_datetime(d["date"]).dt.to_period(period) < target_period].copy()
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


def fetch_all_direct_bars(code: str, fqt: str) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    begin = EASTMONEY_START
    end = TARGET.replace("-", "")

    daily = fetch_eastmoney_kline(code, "D", fqt, begin, end)
    yearly = prepare_direct_period_bars(fetch_eastmoney_kline(code, "Y", fqt, begin, end), "Y")
    quarterly = prepare_direct_period_bars(fetch_eastmoney_kline(code, "Q", fqt, begin, end), "Q")
    monthly = prepare_direct_period_bars(fetch_eastmoney_kline(code, "M", fqt, begin, end), "M")

    return daily, {"Y": yearly, "Q": quarterly, "M": monthly}


def fetch_eastmoney_universe() -> pd.DataFrame:
    if requests is None:
        raise RuntimeError("requests 不可用，无法拉取东方财富股票池")

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    all_rows: List[Dict[str, Any]] = []
    page = 1

    while True:
        params = {
            "pn": page,
            "pz": 5000,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": EASTMONEY_UNIVERSE_FS,
            "fields": "f12,f14,f2,f3,f4,f5,f6,f8,f20",
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        obj = resp.json()
        diff = ((obj.get("data") or {}).get("diff") or [])
        if not diff:
            break

        for x in diff:
            code = code_of(x.get("f12"))
            name = ss(x.get("f14"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            if not code.startswith(("0", "3", "6")):
                continue
            if any(bad in name for bad in ["退市", "退"]):
                continue
            all_rows.append({
                "code": code,
                "name": name,
                "last_price": sf(x.get("f2")),
                "pct_chg": sf(x.get("f3")),
                "volume": sf(x.get("f5")),
                "amount": sf(x.get("f6")),
                "turnover": sf(x.get("f8")),
                "mkt_cap": sf(x.get("f20")),
            })

        if len(diff) < 5000:
            break
        page += 1

    df = pd.DataFrame(all_rows).drop_duplicates("code").sort_values("code").reset_index(drop=True)
    if df.empty:
        raise RuntimeError("东方财富股票池为空")

    try:
        UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(UNIVERSE_CACHE, index=False, encoding="utf-8-sig")
    except Exception:
        pass

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

    codes = [code_of(x) for x in universe["code"].tolist() if code_of(x)]
    names = {code_of(r["code"]): ss(r.get("name", "")) for _, r in universe.iterrows()}
    return codes, names


def min_seed_discrete_count(min_effective_unique: int) -> int:
    if min_effective_unique <= 4:
        return 2
    if min_effective_unique <= 8:
        return 3
    return 4


def is_volume_bodytop(row: Any) -> bool:
    return (
        bool(row["is_bull"])
        and sf(row["body_ratio"]) >= 0.35
        and sf(row["close_pos"]) >= 0.55
        and sf(row["vol_ratio_prev"]) >= 1.30
    )


def is_double_bodytop(row: Any) -> bool:
    return (
        bool(row["is_bull"])
        and sf(row["body_ratio"]) >= 0.35
        and sf(row["close_pos"]) >= 0.55
        and 1.80 <= sf(row["vol_ratio_prev"]) <= 2.50
    )


def reaction_point_weight(kind: str, row: Any) -> float:
    if kind == "body_top":
        if is_double_bodytop(row):
            return 1.8
        if is_volume_bodytop(row):
            return 1.6 if sf(row["vol_ratio_prev"]) >= 1.60 else 1.4
        return 1.2
    if kind == "high":
        return 1.0
    if kind == "close":
        return 1.0
    return 1.0


def build_reaction_points(k: pd.DataFrame) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    for idx, row in k.iterrows():
        for kind, price in [
            ("high", sf(row["high"])),
            ("body_top", sf(row["body_top"])),
            ("close", sf(row["close"])),
        ]:
            if price <= 0:
                continue
            points.append({
                "bar_index": int(idx),
                "period": ss(row["period"]),
                "end": ss(row["end"]),
                "kind": kind,
                "price": rd(price, 4),
                "weight": reaction_point_weight(kind, row),
                "is_volume_bodytop": bool(kind == "body_top" and is_volume_bodytop(row)),
                "is_double_bodytop": bool(kind == "body_top" and is_double_bodytop(row)),
                "open": rd(row["open"]),
                "high": rd(row["high"]),
                "low": rd(row["low"]),
                "close": rd(row["close"]),
                "body_top": rd(row["body_top"]),
                "volume": rd(row["volume"]),
                "vol_ratio_prev": rd(row["vol_ratio_prev"]),
            })
    return sorted(points, key=lambda x: sf(x["price"]))


def points_near(points: List[Dict[str, Any]], center: float, tol: float) -> List[Dict[str, Any]]:
    if center <= 0:
        return []
    return [p for p in points if abs(sf(p["price"]) - center) / center <= tol]


def choose_best_point_per_bar(points: List[Dict[str, Any]], prefer_line: Optional[float] = None) -> List[Dict[str, Any]]:
    by_bar: Dict[int, Dict[str, Any]] = {}

    def key(p: Dict[str, Any]) -> Tuple[Any, ...]:
        dist_key = -abs(sf(p["price"]) - sf(prefer_line)) if prefer_line else 0.0
        kind_rank = {"body_top": 3, "high": 2, "close": 1}.get(ss(p["kind"]), 0)
        return (sf(p["weight"]), kind_rank, dist_key)

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
    return {
        "seed": rd(seed_price),
        "center": rd(center),
        "low": rd(min(prices)),
        "high": rd(max(prices)),
        "primary_unique": len(uniq),
        "primary_points": uniq,
        "raw_points": raw,
        "primary_weight": rd(sum(weights)),
        "primary_periods": ",".join(ss(p["period"]) for p in uniq),
        "primary_kinds": ",".join(ss(p["kind"]) for p in uniq),
    }


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
                "bar_index": i,
                "period": ss(r["period"]),
                "end": ss(r["end"]),
                "kind": "shadow",
                "price": rd(line, 4),
                "weight": SHADOW_REACTION_WEIGHT,
                "is_volume_bodytop": False,
                "is_double_bodytop": False,
                "open": rd(r["open"]),
                "high": rd(r["high"]),
                "low": rd(r["low"]),
                "close": rd(r["close"]),
                "body_top": rd(r["body_top"]),
                "volume": rd(r["volume"]),
                "vol_ratio_prev": rd(r["vol_ratio_prev"]),
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

    score = (
        1.25 * len(bodytops)
        + 1.00 * len(highs)
        + 0.85 * len(closes)
        + 0.55 * len(shadows)
        + 0.35 * sum(1 for p in bodytops if bool(p.get("is_volume_bodytop")))
        + 0.45 * sum(1 for p in bodytops if bool(p.get("is_double_bodytop")))
        - 10.0 * compact
        - 6.0 * center_bias
    )

    return {
        "adhesion_score": rd(score, 4),
        "reaction_center": rd(center, 4),
        "compact_error_pct": rd(compact * 100, 4),
        "center_bias_pct": rd(center_bias * 100, 4),
        "latest_reaction_bar": int(latest_bar),
        "bodytop_sticky_count": len(bodytops),
        "high_sticky_count": len(highs),
        "close_sticky_count": len(closes),
        "shadow_sticky_count": len(shadows),
    }


def validate_bodytop_line(
    k: pd.DataFrame,
    points: List[Dict[str, Any]],
    cluster: Dict[str, Any],
    bodytop_point: Dict[str, Any],
    min_effective_unique: int,
) -> Optional[Dict[str, Any]]:
    line = sf(bodytop_point["price"])
    if line <= 0:
        return None

    primary_raw = points_near(points, line, POINT_TOL)
    primary = choose_best_point_per_bar(primary_raw, line)
    primary_bars = {int(p["bar_index"]) for p in primary}

    anchor_bar = int(bodytop_point["bar_index"])
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
    if cut_count != 0:
        return None

    volume_bodytop_count = sum(1 for p in primary if bool(p.get("is_volume_bodytop")))
    double_bodytop_count = sum(1 for p in primary if bool(p.get("is_double_bodytop")))
    if volume_bodytop_count >= 2 and double_bodytop_count >= 1:
        grade = "S-Core"
    elif volume_bodytop_count >= 1:
        grade = "A-Core"
    else:
        return None

    stick = adhesion_metrics(line, samples)

    out = {
        "line": rd(line),
        "core_grade": grade,
        "touch_count": len(sample_bars),
        "effective_unique_count": len(sample_bars),
        "primary_unique_count": len(primary_bars),
        "external_effective_count": len(external_effective),
        "external_primary_count": len(external_primary),
        "external_shadow_count": len(shadows),
        "volume_bodytop_count": int(volume_bodytop_count),
        "double_bodytop_count": int(double_bodytop_count),
        "cut_count": int(cut_count),
        "primary_weight": rd(sum(sf(p["weight"]) for p in primary)),
        "effective_weight": rd(sum(sf(p["weight"]) for p in samples)),
        "cluster_center": rd(cluster["center"]),
        "cluster_low": rd(cluster["low"]),
        "cluster_high": rd(cluster["high"]),
        "cluster_primary_unique": int(cluster["primary_unique"]),
        "cluster_primary_weight": rd(cluster["primary_weight"]),
        "anchor_period": ss(bodytop_point["period"]),
        "anchor_end": ss(bodytop_point["end"]),
        "anchor_bar_index": int(anchor_bar),
        "anchor_kind": ss(bodytop_point["kind"]),
        "anchor_price": rd(bodytop_point["price"]),
        "sample_periods": ",".join(ss(p["period"]) for p in samples),
        "sample_kinds": ",".join(ss(p["kind"]) for p in samples),
        "primary_sample_periods": ",".join(ss(p["period"]) for p in primary),
        "primary_sample_kinds": ",".join(ss(p["kind"]) for p in primary),
        "external_sample_periods": ",".join(ss(p["period"]) for p in external_effective),
        "external_sample_kinds": ",".join(ss(p["kind"]) for p in external_effective),
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
        bodytop_candidates: List[Dict[str, Any]] = []
        for p in points:
            if ss(p.get("kind")) != "body_top":
                continue
            center = sf(cluster["center"])
            if center > 0 and abs(sf(p["price"]) - center) / center <= POINT_TOL:
                bodytop_candidates.append(p)

        valid: List[Dict[str, Any]] = []
        for bp in bodytop_candidates:
            v = validate_bodytop_line(k, points, cluster, bp, min_effective_unique)
            if v:
                valid.append(v)

        if not valid:
            continue

        def rank(x: Dict[str, Any]) -> Tuple[Any, ...]:
            return (
                int(x["external_effective_count"]),
                int(x["effective_unique_count"]),
                sf(x.get("adhesion_score")),
                -sf(x.get("center_bias_pct")),
                -sf(x.get("compact_error_pct")),
                int(x.get("latest_reaction_bar", -1)),
                int(x.get("anchor_bar_index", -1)),
                int(x["external_primary_count"]),
                int(x["external_shadow_count"]),
                int(x["double_bodytop_count"]),
                int(x["volume_bodytop_count"]),
                sf(x["effective_weight"]),
                sf(x["line"]) * 0.000001,
            )

        best = max(valid, key=rank)
        best.update({
            "period_type": period,
            "period_label": label,
            "period_rank": int(period_rank),
            "core_rank": {"S-Core": 3, "A-Core": 2}.get(ss(best["core_grade"]), 0),
            "min_effective_unique": int(min_effective_unique),
            "seed_discrete_need": int(seed_need),
        })
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
        return (
            int(x.get("core_rank", 0)),
            int(x.get("period_rank", 0)),
            int(x.get("external_effective_count", 0)),
            int(x.get("effective_unique_count", 0)),
            sf(x.get("adhesion_score")),
            -sf(x.get("center_bias_pct")),
            -sf(x.get("compact_error_pct")),
            int(x.get("latest_reaction_bar", -1)),
            int(x.get("double_bodytop_count", 0)),
            int(x.get("volume_bodytop_count", 0)),
            sf(x.get("effective_weight")),
            sf(x.get("line")) * 0.000001,
        )

    return sorted([max(g, key=rank) for g in groups], key=lambda z: sf(z["line"]))


def scan_core_lines(period_bars: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for period, label, min_effective_unique, period_rank in PERIOD_SPECS:
        out.extend(scan_period_core_lines(period_bars, period, label, min_effective_unique, period_rank))
    return dedupe_lines(out)


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


def daily_features(row: Any, prev: Any, line: float) -> Dict[str, Any]:
    open_ = sf(row["open"])
    high = sf(row["high"])
    low = sf(row["low"])
    close = sf(row["close"])
    volume = sf(row["volume"])
    prev_close = sf(prev["close"])
    prev_volume = sf(prev["volume"])
    rng = max(high - low, 1e-9)
    body = abs(close - open_)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    return {
        "vol_ratio_prev": volume / prev_volume if prev_volume > 0 else 0.0,
        "gap_break": open_ >= line * 1.003 and prev_close < line,
        "close_pos": (close - low) / rng,
        "body_ratio": body / rng,
        "upper_shadow_ratio": max(0.0, high - body_top) / rng,
        "entity_above_line_ratio": max(0.0, body_top - max(line, body_bottom)) / max(body, 1e-9),
        "positive": close > open_,
    }


def classify_breakout(f: Dict[str, Any]) -> Tuple[str, int]:
    base_ok = (
        bool(f["positive"])
        and sf(f["body_ratio"]) >= 0.30
        and sf(f["close_pos"]) >= 0.65
        and sf(f["upper_shadow_ratio"]) <= 0.35
        and sf(f["entity_above_line_ratio"]) >= 0.35
    )
    if not base_ok:
        return "C突破", 1
    vr = sf(f["vol_ratio_prev"])
    if bool(f["gap_break"]) and 1.80 <= vr <= 2.50:
        return "S突破", 5
    if 1.80 <= vr <= 2.50:
        return "A+突破", 4
    if bool(f["gap_break"]) and vr >= 1.45:
        return "A突破", 3
    if vr >= 1.45:
        return "B突破", 2
    return "C突破", 1


def best_breakout(daily: pd.DataFrame, line: float) -> Dict[str, Any]:
    d = normalize_hist(daily)
    best = {"grade": "无突破", "score_rank": 0}
    if d.empty or line <= 0:
        return best

    start = max(1, len(d) - BREAKOUT_LOOKBACK_DAYS)
    for idx in range(start, len(d)):
        row = d.iloc[idx]
        prev = d.iloc[idx - 1]
        prev_close = sf(prev["close"])
        prev_low = sf(prev["low"])
        close = sf(row["close"])
        high = sf(row["high"])
        if not (prev_close <= line * 1.002 and prev_low < line and close >= line * 1.005 and high >= line * 1.005):
            continue
        f = daily_features(row, prev, line)
        grade, rank = classify_breakout(f)
        dist = pct(close, line)
        note = "突破有效但离线过远，等待回踩" if dist > 10 else ("突破偏高，谨慎追高" if dist > 6 else "突破距离可接受")
        obj = {
            "grade": grade,
            "score_rank": int(rank),
            "date": ss(row["date"]),
            "idx": int(idx),
            "close": rd(close),
            "distance_pct": rd(dist),
            "note": note,
            "vol_ratio_prev": rd(f["vol_ratio_prev"]),
            "gap_break": bool(f["gap_break"]),
        }
        if (int(obj["score_rank"]), -sf(obj["distance_pct"]), int(obj["idx"])) > (
            int(best.get("score_rank", 0)),
            -sf(best.get("distance_pct", 999)),
            int(best.get("idx", 0)),
        ):
            best = obj
    return best


def best_pullback(daily: pd.DataFrame, line: float, breakout: Dict[str, Any]) -> Dict[str, Any]:
    d = normalize_hist(daily)
    best = {"grade": "无回踩", "score_rank": 0}
    if d.empty or line <= 0 or breakout.get("idx") is None:
        return best
    bidx = int(breakout["idx"])
    if bidx < 0 or bidx >= len(d):
        return best
    bvol = sf(d.iloc[bidx]["volume"])
    end = min(len(d), bidx + 1 + PULLBACK_LOOKBACK_AFTER_BREAK)
    for idx in range(bidx + 1, end):
        row = d.iloc[idx]
        prev = d.iloc[idx - 1]
        low = sf(row["low"])
        close = sf(row["close"])
        open_ = sf(row["open"])
        vol = sf(row["volume"])
        prev_close = sf(prev["close"])
        if low > line * 1.03:
            continue
        close_ok = close >= line * 0.995
        defense_ok = close >= line * 0.985
        shrink = bvol > 0 and vol <= bvol * 0.75
        bad = close < open_ and pct(close, prev_close) <= -3 and bvol > 0 and vol >= bvol * 0.80
        if not defense_ok or bad:
            obj = {"grade": "失败", "score_rank": -1, "date": ss(row["date"]), "idx": int(idx), "note": "跌破防守或放量长阴", "low": rd(low), "close": rd(close)}
        elif close_ok and shrink and int(breakout.get("score_rank", 0)) >= 4:
            obj = {"grade": "S回踩", "score_rank": 5, "date": ss(row["date"]), "idx": int(idx), "note": "强突破后的缩量回踩不破", "low": rd(low), "close": rd(close)}
        elif close_ok and shrink and int(breakout.get("score_rank", 0)) >= 2:
            obj = {"grade": "A回踩", "score_rank": 4, "date": ss(row["date"]), "idx": int(idx), "note": "有效突破后的缩量回踩不破", "low": rd(low), "close": rd(close)}
        elif close_ok:
            obj = {"grade": "B观察", "score_rank": 2, "date": ss(row["date"]), "idx": int(idx), "note": "回踩不破但量能或前置突破不足", "low": rd(low), "close": rd(close)}
        else:
            obj = {"grade": "弱回踩", "score_rank": 1, "date": ss(row["date"]), "idx": int(idx), "note": "触线但收盘偏弱", "low": rd(low), "close": rd(close)}
        if int(obj["score_rank"]) > int(best.get("score_rank", 0)):
            best = obj
    return best


def is_recent_index(idx: Any, total_len: int, lookback: int = EVENT_LOOKBACK_DAYS) -> bool:
    try:
        return int(idx) >= max(0, int(total_len) - int(lookback))
    except Exception:
        return False


def event_score(core: Dict[str, Any], breakout: Dict[str, Any], pullback: Dict[str, Any], current_close: float) -> float:
    core_base = 40 if ss(core.get("core_grade")) == "S-Core" else 30
    period_bonus = {"年线": 10, "季线": 6, "月线": 3}.get(ss(core.get("period_label")), 0)
    breakout_bonus = int(breakout.get("score_rank", 0)) * 8
    pullback_bonus = max(0, int(pullback.get("score_rank", 0))) * 6
    distance = abs(pct(current_close, sf(core.get("line"))))
    distance_penalty = 0.0
    if distance > 120:
        distance_penalty = 8
    elif distance > 80:
        distance_penalty = 5
    elif distance > 50:
        distance_penalty = 2
    return round(core_base + period_bonus + breakout_bonus + pullback_bonus + sf(core.get("adhesion_score")) - distance_penalty, 3)


def build_breakout_events(daily: pd.DataFrame, core_lines: List[Dict[str, Any]], current_close: float) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    n = len(daily)

    for core in core_lines:
        line = sf(core.get("line"))
        if line <= 0:
            continue

        br = best_breakout(daily, line)
        pb = best_pullback(daily, line, br)

        recent_break = is_recent_index(br.get("idx"), n)
        recent_pullback = is_recent_index(pb.get("idx"), n)

        if int(br.get("score_rank", 0)) < 2 and int(pb.get("score_rank", 0)) < 4:
            continue
        if not (recent_break or recent_pullback):
            continue

        ev = {
            "core": core,
            "breakout": br,
            "pullback": pb,
            "event_score": event_score(core, br, pb, current_close),
            "recent_breakout": bool(recent_break),
            "recent_pullback": bool(recent_pullback),
            "line": rd(line),
            "core_grade": ss(core.get("core_grade")),
            "period_label": ss(core.get("period_label")),
            "breakout_grade": ss(br.get("grade")),
            "breakout_date": ss(br.get("date")),
            "pullback_grade": ss(pb.get("grade")),
            "pullback_date": ss(pb.get("date")),
        }
        events.append(ev)

    return sorted(events, key=lambda x: sf(x.get("event_score")), reverse=True)



def pack_core(prefix: str, x: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not x:
        return {
            f"{prefix}核心线": None, f"{prefix}等级": "无", f"{prefix}周期": "",
            f"{prefix}有效共振": 0, f"{prefix}离散共振": 0, f"{prefix}外部有效": 0, f"{prefix}外部影线": 0,
            f"{prefix}放量实顶": 0, f"{prefix}倍量实顶": 0, f"{prefix}切实体": 0, f"{prefix}距现价%": None,
            f"{prefix}锚点": "", f"{prefix}主簇": "", f"{prefix}反应中心": None, f"{prefix}粘合分": None,
            f"{prefix}中心偏离%": None, f"{prefix}紧密误差%": None, f"{prefix}样本": "", f"{prefix}类型": "",
        }
    return {
        f"{prefix}核心线": rd(x.get("line")), f"{prefix}等级": ss(x.get("core_grade")), f"{prefix}周期": ss(x.get("period_label")),
        f"{prefix}有效共振": int(x.get("effective_unique_count", 0)), f"{prefix}离散共振": int(x.get("primary_unique_count", 0)),
        f"{prefix}外部有效": int(x.get("external_effective_count", 0)), f"{prefix}外部影线": int(x.get("external_shadow_count", 0)),
        f"{prefix}放量实顶": int(x.get("volume_bodytop_count", 0)), f"{prefix}倍量实顶": int(x.get("double_bodytop_count", 0)),
        f"{prefix}切实体": int(x.get("cut_count", 0)), f"{prefix}距现价%": rd(x.get("distance_pct")),
        f"{prefix}锚点": ss(x.get("anchor_period")), f"{prefix}主簇": f"{rd(x.get('cluster_low'))}-{rd(x.get('cluster_high'))}",
        f"{prefix}反应中心": rd(x.get("reaction_center")), f"{prefix}粘合分": rd(x.get("adhesion_score")),
        f"{prefix}中心偏离%": rd(x.get("center_bias_pct")), f"{prefix}紧密误差%": rd(x.get("compact_error_pct")),
        f"{prefix}样本": ss(x.get("sample_periods")), f"{prefix}类型": ss(x.get("sample_kinds")),
    }


def final_grade(near: Optional[Dict[str, Any]], breakout: Dict[str, Any], pullback: Dict[str, Any]) -> str:
    if not near:
        return "无核心线"
    core = ss(near.get("core_grade"))
    pb = ss(pullback.get("grade"))
    br = ss(breakout.get("grade"))
    if core == "S-Core" and pb == "S回踩":
        return "S"
    if core in {"S-Core", "A-Core"} and pb in {"S回踩", "A回踩"}:
        return "A"
    if br in {"S突破", "A+突破", "A突破"}:
        return "B｜突破有效，等回踩"
    return "C｜核心线成立，等触发"


def analyze_one(code: str, name_hint: str = "") -> Dict[str, Any]:
    ref_close, cache_path = cache_reference_close(code)
    adjust_flag, adjust_info = choose_adjust_flag(code, ref_close)
    daily, period_bars = fetch_all_direct_bars(code, adjust_flag)

    if daily.empty or not period_bars or all(v.empty for v in period_bars.values()):
        return {"股票代码": code, "状态": "东方财富直接K线为空", "adjust_flag": adjust_flag}

    current_close = sf(daily.iloc[-1]["close"])
    name = name_hint or ("长电科技" if code == "600584" else "")

    core_lines = scan_core_lines(period_bars)
    near, far = select_near_far(core_lines, current_close)

    events = build_breakout_events(daily, core_lines, current_close)
    best_event = events[0] if events else None
    event_core = best_event["core"] if best_event else near

    breakout = best_event["breakout"] if best_event else (best_breakout(daily, sf(near["line"])) if near else {"grade": "无突破", "score_rank": 0})
    pullback = best_event["pullback"] if best_event else (best_pullback(daily, sf(near["line"]), breakout) if near else {"grade": "无回踩", "score_rank": 0})

    row: Dict[str, Any] = {
        "股票代码": code,
        "股票中文名称": name,
        "最新日期": ss(daily.iloc[-1]["date"]),
        "当前收盘": rd(current_close),
        "状态": "完成",
        "数据源": "东方财富直接日/月/季/年K",
        "年季来源": "直接取东方财富年线/季线/月线，不从日线或月线捏合",
        "adjust_flag": adjust_flag,
        "复权口径": ADJUST_FLAG_NAMES.get(adjust_flag, adjust_flag),
        "adjust_info": adjust_info,
        "cache_reference_close": rd(ref_close),
        "cache_path": cache_path,
        "正式核心线数量": len(core_lines),
        "突破等级": breakout.get("grade"),
        "突破日期": breakout.get("date", ""),
        "突破说明": breakout.get("note", ""),
        "回踩等级": pullback.get("grade"),
        "回踩日期": pullback.get("date", ""),
        "回踩说明": pullback.get("note", ""),
        "最终等级": final_grade(event_core, breakout, pullback),
        "破界事件数": len(events),
        "最佳事件分": sf(best_event.get("event_score")) if best_event else 0.0,
        "近端详情": near or {},
        "远端详情": far or {},
        "事件核心线详情": event_core or {},
        "全部核心线": core_lines,
        "全部破界事件": events,
        "突破详情": breakout,
        "回踩详情": pullback,
    }
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


def build_report(rows: List[Dict[str, Any]]) -> str:
    completed = [r for r in rows if ss(r.get("状态")) == "完成"]
    event_rows = [r for r in completed if int(r.get("破界事件数", 0)) > 0]
    event_rows = sorted(event_rows, key=lambda r: sf(r.get("最佳事件分")), reverse=True)

    if RUN_FULL_MARKET:
        lines = [
            f"破界全市场海选｜{TARGET_DASH or TARGET}",
            f"BOOT={BOOT}",
            f"RUN_MODE={RUN_MODE}",
            "数据：东方财富直接日/月/季/年K；默认前复权；不再用本地日线或月线捏合年季月。",
            f"扫描：{len(rows)} 只｜完成：{len(completed)} 只｜破界事件：{len(event_rows)} 只｜推送Top{TOP_PUSH_LIMIT}",
            "",
        ]

        for i, r in enumerate(event_rows[:TOP_PUSH_LIMIT], 1):
            lines.extend([
                f"{i}. {r.get('股票代码')} {r.get('股票中文名称')}｜{r.get('最终等级')}｜事件分{r.get('最佳事件分')}｜收盘{r.get('当前收盘')}",
                f"   事件核心线：{r.get('事件核心线')}｜{r.get('事件等级')}｜{r.get('事件周期')}｜有效共振{r.get('事件有效共振')}｜放量实顶{r.get('事件放量实顶')}｜切实体{r.get('事件切实体')}",
                f"   定线：锚点{r.get('事件锚点')}｜主簇{r.get('事件主簇')}｜反应中心{r.get('事件反应中心')}｜粘合分{r.get('事件粘合分')}｜样本{r.get('事件样本')}｜类型{r.get('事件类型')}",
                f"   突破：{r.get('突破等级')}｜{r.get('突破日期')}｜{r.get('突破说明')}；回踩：{r.get('回踩等级')}｜{r.get('回踩日期')}｜{r.get('回踩说明')}",
                "",
            ])

        if not event_rows:
            lines.append("今日未筛出最近窗口内的高质量破界事件。")

        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3850].rstrip() + "\n……\n报告过长，完整明细见 artifact。"
        return text

    lines = [
        f"破界单票/多票验证｜{TARGET_DASH or TARGET}",
        f"BOOT={BOOT}",
        f"RUN_MODE={RUN_MODE}",
        f"目标股票={','.join(TARGET_CODES)}",
        "数据：东方财富直接日/月/季/年K；不再用本地日线或月线捏合年季月。",
        "",
    ]
    for r in rows:
        lines.extend([
            f"{r.get('股票代码')} {r.get('股票中文名称')}｜收盘 {r.get('当前收盘')}｜{r.get('最终等级')}｜事件分{r.get('最佳事件分')}",
            f"数据源：{r.get('数据源')}｜年季来源：{r.get('年季来源')}｜复权：{r.get('复权口径')}({r.get('adjust_flag')})｜缓存参考收盘{r.get('cache_reference_close')}",
            f"事件核心线：{r.get('事件核心线')}｜{r.get('事件等级')}｜{r.get('事件周期')}｜有效共振{r.get('事件有效共振')}｜离散{r.get('事件离散共振')}｜外部有效{r.get('事件外部有效')}｜外部影线{r.get('事件外部影线')}｜放量实顶{r.get('事件放量实顶')}｜倍量实顶{r.get('事件倍量实顶')}｜切实体{r.get('事件切实体')}｜距现价{r.get('事件距现价%')}%",
            f"近端核心线：{r.get('近端核心线')}｜{r.get('近端等级')}｜{r.get('近端周期')}｜有效共振{r.get('近端有效共振')}｜离散{r.get('近端离散共振')}｜外部有效{r.get('近端外部有效')}｜外部影线{r.get('近端外部影线')}｜放量实顶{r.get('近端放量实顶')}｜倍量实顶{r.get('近端倍量实顶')}｜切实体{r.get('近端切实体')}｜距现价{r.get('近端距现价%')}%",
            f"远端核心线：{r.get('远端核心线')}｜{r.get('远端等级')}｜{r.get('远端周期')}｜有效共振{r.get('远端有效共振')}｜离散{r.get('远端离散共振')}｜外部有效{r.get('远端外部有效')}｜外部影线{r.get('远端外部影线')}｜放量实顶{r.get('远端放量实顶')}｜倍量实顶{r.get('远端倍量实顶')}｜切实体{r.get('远端切实体')}｜距现价{r.get('远端距现价%')}%",
            f"事件定线：锚点{r.get('事件锚点')}｜主簇{r.get('事件主簇')}｜反应中心{r.get('事件反应中心')}｜粘合分{r.get('事件粘合分')}｜中心偏离{r.get('事件中心偏离%')}%｜紧密误差{r.get('事件紧密误差%')}%｜样本{r.get('事件样本')}｜类型{r.get('事件类型')}",
            f"突破：{r.get('突破等级')}｜{r.get('突破日期')}｜{r.get('突破说明')}",
            f"回踩：{r.get('回踩等级')}｜{r.get('回踩日期')}｜{r.get('回踩说明')}",
            "",
        ])
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n……\n报告过长，完整明细见 artifact。"
    return text


def flatten_core_line(row: Dict[str, Any], core: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": row.get("股票代码"),
        "name": row.get("股票中文名称"),
        "current_close": row.get("当前收盘"),
        "line": rd(core.get("line")),
        "core_grade": ss(core.get("core_grade")),
        "period_label": ss(core.get("period_label")),
        "effective_unique_count": int(core.get("effective_unique_count", 0)),
        "primary_unique_count": int(core.get("primary_unique_count", 0)),
        "external_effective_count": int(core.get("external_effective_count", 0)),
        "external_shadow_count": int(core.get("external_shadow_count", 0)),
        "volume_bodytop_count": int(core.get("volume_bodytop_count", 0)),
        "double_bodytop_count": int(core.get("double_bodytop_count", 0)),
        "cut_count": int(core.get("cut_count", 0)),
        "anchor_period": ss(core.get("anchor_period")),
        "cluster_low": rd(core.get("cluster_low")),
        "cluster_high": rd(core.get("cluster_high")),
        "reaction_center": rd(core.get("reaction_center")),
        "adhesion_score": rd(core.get("adhesion_score")),
        "center_bias_pct": rd(core.get("center_bias_pct")),
        "compact_error_pct": rd(core.get("compact_error_pct")),
        "sample_periods": ss(core.get("sample_periods")),
        "sample_kinds": ss(core.get("sample_kinds")),
    }


def flatten_event(row: Dict[str, Any], ev: Dict[str, Any]) -> Dict[str, Any]:
    core = ev.get("core") or {}
    br = ev.get("breakout") or {}
    pb = ev.get("pullback") or {}
    out = flatten_core_line(row, core)
    out.update({
        "event_score": rd(ev.get("event_score")),
        "recent_breakout": bool(ev.get("recent_breakout")),
        "recent_pullback": bool(ev.get("recent_pullback")),
        "breakout_grade": ss(br.get("grade")),
        "breakout_date": ss(br.get("date")),
        "breakout_close": rd(br.get("close")),
        "breakout_distance_pct": rd(br.get("distance_pct")),
        "breakout_note": ss(br.get("note")),
        "pullback_grade": ss(pb.get("grade")),
        "pullback_date": ss(pb.get("date")),
        "pullback_note": ss(pb.get("note")),
    })
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
    pd.DataFrame(event_rows).sort_values("event_score", ascending=False).to_csv(OUTPUT_EVENTS, index=False, encoding="utf-8-sig")

    payload = {
        "boot": BOOT,
        "run_mode": RUN_MODE,
        "full_market": RUN_FULL_MARKET,
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "target_codes": TARGET_CODES,
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "source": "eastmoney_direct_kline",
            "klt": {"D": 101, "M": 103, "Q": 104, "Y": 106},
            "adjust_flag_env": ADJUST_FLAG_ENV,
            "point_tol": POINT_TOL,
            "body_edge_tol": BODY_EDGE_TOL,
            "shadow_reaction_weight": SHADOW_REACTION_WEIGHT,
            "event_lookback_days": EVENT_LOOKBACK_DAYS,
            "workers": WORKERS,
            "max_stocks": MAX_STOCKS,
        },
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    SELF_CHECK_JSON.write_text(json.dumps({
        "status": "PASS",
        "boot": BOOT,
        "run_mode": RUN_MODE,
        "full_market": RUN_FULL_MARKET,
        "target_codes": TARGET_CODES,
        "outputs": {
            "report": str(OUTPUT_MD),
            "detail": str(OUTPUT_CSV),
            "core_line_map": str(OUTPUT_CORE_MAP),
            "breakout_events": str(OUTPUT_EVENTS),
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    if not SEND_TELEGRAM or not BOT or not CHAT or requests is None:
        log(f"Telegram跳过 enable={SEND_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}")
        print(text, flush=True)
        return

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        log(f"Telegram status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:160]}")
    except Exception as exc:
        log(f"Telegram发送失败：{exc}")


def safe_analyze(code: str, name_hint: str) -> Dict[str, Any]:
    try:
        return analyze_one(code, name_hint)
    except Exception as exc:
        return {
            "股票代码": code,
            "股票中文名称": name_hint,
            "当前收盘": None,
            "状态": f"失败：{exc}",
            "最终等级": "失败",
            "破界事件数": 0,
            "最佳事件分": 0.0,
        }


def main() -> None:
    print(BOOT, flush=True)
    print(f"RUN_MODE={RUN_MODE}", flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print(f"run_full_market={RUN_FULL_MARKET}", flush=True)
    print("target_codes=" + (",".join(TARGET_CODES) if TARGET_CODES else "FULL_MARKET"), flush=True)
    print("data_source=eastmoney_direct_yqm_fullmarket", flush=True)
    print(f"workers={WORKERS} max_stocks={MAX_STOCKS} event_lookback={EVENT_LOOKBACK_DAYS}", flush=True)

    codes, name_map = resolve_run_universe()
    log(f"准备扫描：{len(codes)} 只")

    rows: List[Dict[str, Any]] = []

    if RUN_FULL_MARKET and WORKERS > 1:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(safe_analyze, code, name_map.get(code, "")): code for code in codes}
            done = 0
            for fut in as_completed(futs):
                row = fut.result()
                rows.append(row)
                done += 1
                if done % 50 == 0 or done == len(codes):
                    event_count = sum(1 for r in rows if int(r.get("破界事件数", 0)) > 0)
                    log(f"进度 {done}/{len(codes)}｜事件 {event_count}")
    else:
        for idx, code in enumerate(codes, 1):
            row = safe_analyze(code, name_map.get(code, ""))
            rows.append(row)
            log(f"完成 {idx}/{len(codes)} {code}｜事件={row.get('破界事件数')}｜近端={row.get('近端核心线')}｜等级={row.get('最终等级')}")

    report_text = build_report(rows)
    write_outputs(rows, report_text)
    send_telegram(report_text)
    log(f"done report={OUTPUT_MD} detail={OUTPUT_CSV} core_map={OUTPUT_CORE_MAP} events={OUTPUT_EVENTS}")


if __name__ == "__main__":
    main()
