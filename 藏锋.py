# -*- coding: utf-8 -*-
from __future__ import annotations

"""
藏锋.py
爆发前夜压缩指标｜与灵动.py、破界.py、潮汐.py并列的根目录脚本。

定位：
1）只做“压缩蓄势 + 靠近核心触发线 + 真实防守位/RR合格”的海选；
2）不是买点，不追已经启动后的高位票；
3）严格按目标交易日缓存扫描，旧缓存直接跳过；
4）输出 artifacts/zangfeng_report.md、artifacts/zangfeng_candidates.json、artifacts/zangfeng_telegram.txt。
"""

import argparse
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import baostock as bs
except Exception:
    bs = None

BOOT = "ZANGFENG_ROOT_SCRIPT_V3_STRICT_20260626"
VERSION = "藏锋-v3-root-strict"
ROOT = Path(__file__).resolve().parent
ARTIFACT_DIR = ROOT / "artifacts"
MAIN_CACHE_DIR = ROOT / "kline_cache"
CACHE_DIRS = [MAIN_CACHE_DIR, ROOT / "employee5_kline_cache", ROOT / "data" / "kline_cache", ROOT / "cache" / "kline_cache", ROOT.parent / "kline_cache"]
OUTPUT_MD = ARTIFACT_DIR / "zangfeng_report.md"
OUTPUT_JSON = ARTIFACT_DIR / "zangfeng_candidates.json"
OUTPUT_TG = ARTIFACT_DIR / "zangfeng_telegram.txt"
SELF_CHECK_JSON = ARTIFACT_DIR / "zangfeng_self_check.json"

TARGET_KEYS = ["ZANGFENG_TARGET_DATE", "SELECTION_TRADE_DATE", "DATA_GATE_TARGET_DATE", "TARGET_TRADE_DATE", "LAST_TRADE_DAY_OVERRIDE", "REQUIRED_CACHE_DATE"]
TOP_N = int(os.getenv("ZANGFENG_TOP_N", "10"))
MIN_SCORE = float(os.getenv("ZANGFENG_MIN_SCORE", "72"))
MAX_STOCKS = int(os.getenv("MAX_STOCKS", os.getenv("ZANGFENG_MAX_STOCKS", "0")))
MIN_ROWS = int(os.getenv("ZANGFENG_MIN_CACHE_ROWS", "120"))
PROGRESS_EVERY = int(os.getenv("ZANGFENG_PROGRESS_EVERY", "500"))
ALLOW_REFRESH = os.getenv("ZANGFENG_ALLOW_BAOSTOCK_FALLBACK", "1") == "1"
REFRESH_BUDGET_MIN = float(os.getenv("ZANGFENG_REFRESH_BUDGET_MIN", "25"))
QFQ_ADJUSTFLAG = "2"
REPORT_MAX_CHARS = int(os.getenv("ZANGFENG_TELEGRAM_MAX_CHARS", "3500"))

MIN_CLOSE_PRICE = float(os.getenv("ZANGFENG_MIN_CLOSE_PRICE", "2.0"))
MIN_AMOUNT20 = float(os.getenv("ZANGFENG_MIN_AMOUNT20", "30000000"))
MAX_RET20 = float(os.getenv("ZANGFENG_MAX_RET20", "0.20"))
MAX_WIDTH20 = float(os.getenv("ZANGFENG_MAX_WIDTH20", "0.18"))
MAX_WIDTH30 = float(os.getenv("ZANGFENG_MAX_WIDTH30", "0.22"))
MAX_CLOSE_STD10 = float(os.getenv("ZANGFENG_MAX_CLOSE_STD10", "0.055"))
MAX_TRIGGER_ABOVE = float(os.getenv("ZANGFENG_MAX_TRIGGER_ABOVE", "0.050"))
MAX_TRIGGER_BROKEN = float(os.getenv("ZANGFENG_MAX_TRIGGER_BROKEN", "0.012"))
MIN_DEFENSE_DIST = float(os.getenv("ZANGFENG_MIN_DEFENSE_DIST", "0.025"))
MAX_DEFENSE_DIST = float(os.getenv("ZANGFENG_MAX_DEFENSE_DIST", "0.125"))
MIN_RR = float(os.getenv("ZANGFENG_MIN_RR", "1.20"))
MIN_LINE_RESONANCE = int(os.getenv("ZANGFENG_MIN_LINE_RESONANCE", "3"))
MAX_DESTRUCTIVE20 = int(os.getenv("ZANGFENG_MAX_DESTRUCTIVE20", "1"))
HARD_RISK_NAME_KEYWORDS = tuple(x for x in os.getenv("ZANGFENG_HARD_RISK_NAME_KEYWORDS", "ST,*ST,退,退市").split(",") if x)


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def prev_workday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def norm_target_date(x: Any, today: Optional[date] = None) -> str:
    today = today or now_bj().date()
    s = "" if x is None else str(x).strip()
    if not s:
        return ""
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    s = s.replace("/", "-").replace(".", "-").replace("_", "-").strip()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    raw = re.sub(r"\D", "", s)
    if len(raw) >= 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(raw) in (3, 4):
        return f"{today.year:04d}-{int(raw[:-2]):02d}-{int(raw[-2:]):02d}"
    return ""


def target_raw() -> str:
    for key in TARGET_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return prev_workday(now_bj().date()).isoformat()


TARGET_DASH = norm_target_date(target_raw())


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        v = float(str(x).replace("%", "").replace(",", ""))
        return v if math.isfinite(v) else default
    except Exception:
        return default


def rd(x: Any, n: int = 3) -> float:
    v = sf(x)
    return 0.0 if math.isnan(v) or math.isinf(v) else round(v, n)


def div(a: Any, b: Any, default: float = 0.0) -> float:
    a, b = sf(a, float("nan")), sf(b, float("nan"))
    if not math.isfinite(a) or not math.isfinite(b) or abs(b) < 1e-12:
        return default
    v = a / b
    return v if math.isfinite(v) else default


def clamp(x: Any, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, sf(x, lo)))


def low_score(v: Any, good: float, bad: float, pts: float) -> float:
    v = sf(v, float("nan"))
    if not math.isfinite(v):
        return 0.0
    if v <= good:
        return pts
    if v >= bad:
        return 0.0
    return pts * (bad - v) / (bad - good)


def high_score(v: Any, good: float, bad: float, pts: float) -> float:
    v = sf(v, float("nan"))
    if not math.isfinite(v):
        return 0.0
    if v >= good:
        return pts
    if v <= bad:
        return 0.0
    return pts * (v - bad) / (good - bad)


def band_score(v: Any, low: float, high: float, pts: float, soft: float) -> float:
    v = sf(v, float("nan"))
    if not math.isfinite(v):
        return 0.0
    if low <= v <= high:
        return pts
    miss = low - v if v < low else v - high
    return max(0.0, pts * (1.0 - miss / max(soft, 1e-12)))


def norm_date(x: Any) -> str:
    s = re.sub(r"\D", "", ss(x)[:10])
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def code_of(x: Any) -> str:
    s = x.stem if isinstance(x, Path) else ss(x)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def valid_code(code: str) -> bool:
    c = code_of(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def bs_code(code: str) -> str:
    c = code_of(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("920", "8", "4")):
        return "bj." + c
    return c


def fmt_seconds(seconds: float) -> str:
    if seconds <= 0 or math.isnan(seconds) or math.isinf(seconds):
        return "0秒"
    if seconds < 60:
        return f"{seconds:.1f}秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f}分钟"
    return f"{seconds / 3600:.1f}小时"


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {"日期": "date", "交易日期": "date", "date": "date", "time": "date", "trade_date": "date", "代码": "code", "code": "code", "证券代码": "code", "名称": "name", "股票名称": "name", "name": "name", "开盘": "open", "open": "open", "开盘价": "open", "最高": "high", "high": "high", "最高价": "high", "最低": "low", "low": "low", "最低价": "low", "收盘": "close", "close": "close", "收盘价": "close", "成交量": "volume", "volume": "volume", "vol": "volume", "成交额": "amount", "amount": "amount", "涨跌幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg", "pctchg": "pct_chg", "涨幅": "pct_chg"}
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns:
            d[col] = d[col].map(sf)
    if "volume" not in d.columns:
        d["volume"] = 0.0
    if "amount" not in d.columns:
        d["amount"] = d["close"] * d["volume"]
    else:
        miss_amount = d["amount"].fillna(0) <= 0
        d.loc[miss_amount, "amount"] = d.loc[miss_amount, "close"] * d.loc[miss_amount, "volume"]
    if "name" not in d.columns:
        d["name"] = ""
    if "code" not in d.columns:
        d["code"] = ""
    d["date"] = d["date"].map(norm_date)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0) & (d.high >= d.low)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if TARGET_DASH:
        d = d[d.date <= TARGET_DASH].reset_index(drop=True)
    if d.empty:
        return d
    if "pct_chg" not in d.columns or d["pct_chg"].abs().sum() == 0:
        prev = d.close.shift(1)
        d["pct_chg"] = (d.close / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
        d["pct_chg"] = d["pct_chg"].fillna(0.0)
    return d


def read_cache_file(path: Path) -> pd.DataFrame:
    try:
        return normalize_hist(pd.read_csv(path))
    except Exception:
        pass
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = obj.get("rows") or obj.get("data") or obj.get("klines") or []
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    seen: Dict[str, Path] = {}
    for d in CACHE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            code = code_of(p)
            if valid_code(code) and code not in seen:
                seen[code] = p
    files = list(seen.values())
    if MAX_STOCKS > 0:
        files = files[:MAX_STOCKS]
    return files


def fetch_baostock_kline(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    if bs is None:
        return pd.DataFrame()
    fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
    try:
        rs = bs.query_history_k_data_plus(bs_code(code), fields, start_date=start_date, end_date=end_date, frequency="d", adjustflag=QFQ_ADJUSTFLAG)
        if getattr(rs, "error_code", "1") != "0":
            return pd.DataFrame()
        rows: List[List[str]] = []
        while rs.next():
            rows.append(rs.get_row_data())
        return normalize_hist(pd.DataFrame(rows, columns=fields.split(","))) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def save_cache(code: str, df: pd.DataFrame) -> None:
    MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    keep = [c for c in ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg"] if c in df.columns]
    df[keep].to_csv(MAIN_CACHE_DIR / f"{code}.csv", index=False, encoding="utf-8")


def refresh_stale_cache(files: List[Path]) -> Dict[str, Any]:
    stat = {"enabled": ALLOW_REFRESH, "attempt": 0, "success": 0, "failed": 0, "skipped": 0}
    if not ALLOW_REFRESH or not TARGET_DASH or bs is None:
        return stat
    try:
        lg = bs.login()
        if getattr(lg, "error_code", "1") != "0":
            stat["enabled"] = False
            return stat
    except Exception:
        stat["enabled"] = False
        return stat
    start_ts = time.time()
    budget = max(1.0, REFRESH_BUDGET_MIN * 60.0)
    try:
        for i, p in enumerate(files, 1):
            if time.time() - start_ts > budget:
                print("藏锋补拉达到预算，停止补拉", flush=True)
                break
            code = code_of(p)
            old = read_cache_file(p)
            latest = ss(old.date.iloc[-1]) if not old.empty and "date" in old.columns else ""
            if latest == TARGET_DASH:
                stat["skipped"] += 1
                continue
            stat["attempt"] += 1
            new = fetch_baostock_kline(code, TARGET_DASH, TARGET_DASH)
            if new.empty or ss(new.date.iloc[-1]) != TARGET_DASH:
                stat["failed"] += 1
                continue
            merged = normalize_hist(pd.concat([old, new], ignore_index=True)) if not old.empty else new
            if not merged.empty and ss(merged.date.iloc[-1]) == TARGET_DASH:
                save_cache(code, merged)
                stat["success"] += 1
            else:
                stat["failed"] += 1
            if PROGRESS_EVERY > 0 and i % PROGRESS_EVERY == 0:
                print(f"藏锋补拉 {i}/{len(files)} attempt={stat['attempt']} success={stat['success']} failed={stat['failed']}", flush=True)
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    return stat


def volume_sample_weight(vol_ratio: float) -> float:
    if vol_ratio >= 2.50:
        return 2.00
    if vol_ratio >= 1.80:
        return 1.80
    if vol_ratio >= 1.60:
        return 1.60
    if vol_ratio >= 1.35:
        return 1.45
    if vol_ratio >= 1.15:
        return 1.30
    return 1.00


def body_cut_count(df: pd.DataFrame, line: float) -> int:
    o = df.open.astype(float)
    c = df.close.astype(float)
    top = np.maximum(o, c)
    bot = np.minimum(o, c)
    edge_ok = np.abs(top - line) / line <= 0.005
    return int(((line > bot) & (line < top) & (~edge_ok)).sum())


def score_line(df: pd.DataFrame, line: float, mode: str) -> Dict[str, Any]:
    if line <= 0 or df.empty:
        return {"score": -999.0, "resonance": 0, "cut": 999, "line": line}
    d = df.tail(260).copy()
    vol_ratio = d.volume / d.volume.shift(1).replace(0, np.nan)
    resonance = 0.0
    points = 0
    volume_hits = 0
    tol_hi = 0.015 if mode == "pressure" else 0.012
    tol_body = 0.010
    for idx, row in d.iterrows():
        h, l = sf(row.high), sf(row.low)
        o, c = sf(row.open), sf(row.close)
        top, bot = max(o, c), min(o, c)
        vr = sf(vol_ratio.loc[idx], 1.0) if idx in vol_ratio.index else 1.0
        if mode == "pressure":
            hit = abs(h - line) / line <= tol_hi or abs(top - line) / line <= tol_body or abs(c - line) / line <= tol_body
        else:
            hit = abs(l - line) / line <= tol_hi or abs(bot - line) / line <= tol_body or abs(c - line) / line <= tol_body
        if hit:
            w = volume_sample_weight(vr)
            resonance += w
            points += 1
            if w >= 1.45:
                volume_hits += 1
    cut = body_cut_count(d, line)
    cut_penalty = cut * 0.85 + max(0, cut - 2) ** 2 * 0.35
    score = resonance - cut_penalty
    return {"score": round(score, 4), "resonance": int(points), "weighted_resonance": round(resonance, 3), "cut": int(cut), "volume_hits": int(volume_hits), "line": round(line, 4)}


def candidate_prices(df: pd.DataFrame) -> List[float]:
    d = df.tail(260).copy()
    o, c = d.open.astype(float), d.close.astype(float)
    vals: List[float] = []
    for col in [d.high, d.low, d.close, pd.Series(np.maximum(o, c)), pd.Series(np.minimum(o, c))]:
        vals.extend([sf(x) for x in col.tail(180).tolist() if sf(x) > 0])
    out: List[float] = []
    seen = set()
    for v in vals:
        key = round(v, 2 if v >= 10 else 3)
        if key not in seen:
            out.append(v)
            seen.add(key)
    return out


def estimate_lines(df: pd.DataFrame) -> Dict[str, Any]:
    close, high, low = df.close.astype(float), df.high.astype(float), df.low.astype(float)
    last = float(close.iloc[-1])
    cands = candidate_prices(df)
    pressure_candidates: List[Dict[str, Any]] = []
    for line in cands:
        if last * (1 - MAX_TRIGGER_BROKEN) <= line <= last * (1 + 0.12):
            q = score_line(df, line, "pressure")
            dist = div(line - last, last, 9.99)
            q["distance"] = round(dist, 4)
            q["score"] = round(q["score"] + high_score(-abs(dist), -0.015, -0.080, 1.5), 4)
            if q["resonance"] >= 2:
                pressure_candidates.append(q)
    pressure_candidates.sort(key=lambda x: (x["score"], x["weighted_resonance"], -abs(x["distance"])), reverse=True)
    if pressure_candidates:
        pressure_info = pressure_candidates[0]
        pressure = sf(pressure_info["line"])
    else:
        pressure = float(high.tail(20).max())
        pressure_info = {"line": round(pressure, 4), "score": 0.0, "resonance": 1, "weighted_resonance": 1.0, "cut": 0, "volume_hits": 0, "distance": round(div(pressure - last, last, 0.0), 4), "fallback": True}
    support_candidates: List[Dict[str, Any]] = []
    for line in cands:
        dist = div(last - line, last, 9.99)
        if 0.018 <= dist <= 0.16:
            q = score_line(df, line, "support")
            q["distance"] = round(dist, 4)
            q["score"] = round(q["score"] + band_score(dist, MIN_DEFENSE_DIST, MAX_DEFENSE_DIST, 1.2, 0.05), 4)
            if q["resonance"] >= 2:
                support_candidates.append(q)
    support_candidates.sort(key=lambda x: (x["score"], -x["distance"]), reverse=True)
    if support_candidates:
        support_info = support_candidates[0]
        support = sf(support_info["line"])
    else:
        support = float(low.tail(20).min())
        if not (0 < support < last):
            support = last * 0.94
        support_info = {"line": round(support, 4), "score": 0.0, "resonance": 1, "weighted_resonance": 1.0, "cut": 0, "volume_hits": 0, "distance": round(div(last - support, last, 0.0), 4), "fallback": True}
    above = [sf(x) for x in high.tail(260).tolist() if sf(x) > max(last, pressure) * 1.025]
    next_pressure = min(above) if above else max(float(high.tail(min(len(high), 260)).max()), last * 1.15)
    if next_pressure <= last * 1.03:
        next_pressure = last * 1.15
    return {"pressure": round(pressure, 4), "support": round(support, 4), "trigger": round(pressure, 4), "next_pressure": round(next_pressure, 4), "pressure_info": pressure_info, "support_info": support_info}


def mean_tail(s: pd.Series, n: int) -> float:
    return sf(pd.to_numeric(s.tail(n), errors="coerce").mean(), 0.0)


def cv_tail(s: pd.Series, n: int) -> float:
    v = pd.to_numeric(s.tail(n), errors="coerce")
    v = v[v > 0].dropna()
    if len(v) < max(3, n // 3) or v.mean() <= 0:
        return float("nan")
    return div(v.std(ddof=0), v.mean(), float("nan"))


def ret(close: pd.Series, n: int) -> float:
    if len(close) <= n:
        return 0.0
    return div(close.iloc[-1], close.iloc[-n - 1], 1.0) - 1.0


def slope_pct(s: pd.Series) -> float:
    vals = pd.to_numeric(s, errors="coerce").dropna().to_numpy(float)
    if len(vals) < 3 or np.nanmean(vals) <= 0:
        return 0.0
    return div(np.polyfit(np.arange(len(vals), dtype=float), vals, 1)[0], np.nanmean(vals), 0.0)


def tr_pct(d: pd.DataFrame) -> pd.Series:
    pc = d.close.shift(1)
    tr = pd.concat([(d.high - d.low), (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr / d.close.replace(0, np.nan)


def is_hard_risk_name(name: str) -> bool:
    n = ss(name).upper()
    return any(k.upper() in n for k in HARD_RISK_NAME_KEYWORDS)


def calculate_zangfeng(df: pd.DataFrame, lines: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d = normalize_hist(df)
    if len(d) < max(60, MIN_ROWS // 2):
        return {"ok": False, "score": 0.0, "grade": "数据不足", "action_bias": "不参与：有效K线太少。", "dimensions": {}, "flags": ["数据不足"], "metrics": {"rows": int(len(d))}}
    if lines is None:
        lines = estimate_lines(d)
    flags: List[str] = []
    reject: List[str] = []
    close, high, low, open_, volume, amount = d.close.astype(float), d.high.astype(float), d.low.astype(float), d.open.astype(float), d.volume.astype(float), d.amount.astype(float)
    last = float(close.iloc[-1])
    name = ss(d.name.iloc[-1]) if "name" in d.columns and not d.empty else ""
    rng = (high - low) / close.replace(0, np.nan)
    body = (close - open_).abs() / close.replace(0, np.nan)
    tr = tr_pct(d)
    daily_ret = close.pct_change().fillna(0.0)
    range_ratio = div(mean_tail(rng, 10), sf(rng.iloc[-30:-10].mean(), mean_tail(rng, 10)), 1.0)
    atr_ratio = div(mean_tail(tr, 10), mean_tail(tr, 30), 1.0)
    body_ratio = div(mean_tail(body, 10), sf(body.iloc[-30:-10].mean(), mean_tail(body, 10)), 1.0)
    close_std10 = div(close.tail(10).std(ddof=0), close.tail(10).mean(), 0.0)
    low_slope20 = slope_pct(low.tail(20))
    center_slope20 = slope_pct(close.tail(20))
    width20 = div(float(high.tail(20).max() - low.tail(20).min()), last, 0.0)
    width30 = div(float(high.tail(30).max() - low.tail(30).min()), last, 0.0)
    price_score = low_score(range_ratio, 0.70, 1.08, 5.0) + low_score(atr_ratio, 0.72, 1.10, 5.0) + low_score(body_ratio, 0.72, 1.12, 4.0) + low_score(close_std10, 0.022, MAX_CLOSE_STD10, 5.0) + low_score(width20, 0.090, MAX_WIDTH20, 4.0) + high_score(low_slope20, 0.0015, -0.0015, 2.0)
    price_score = clamp(price_score, 0.0, 25.0)
    vol_cv10 = cv_tail(volume, 10)
    vol_cv30 = cv_tail(volume, 30)
    cv_ratio = div(vol_cv10, vol_cv30, 1.0)
    recent_vol = mean_tail(volume, 10)
    prior_vol = sf(volume.iloc[-30:-10].mean(), recent_vol)
    vol_level = div(recent_vol, prior_vol, 1.0)
    rv = volume.tail(10)
    rv = rv[rv > 0].dropna()
    flat_ratio = float(((rv / rv.median()).between(0.85, 1.15)).mean()) if len(rv) >= 5 and rv.median() > 0 else 0.0
    tail_vol = volume.tail(20)
    up_vol = sf(tail_vol[daily_ret.tail(20) > 0].mean(), float("nan"))
    down_vol = sf(tail_vol[daily_ret.tail(20) < 0].mean(), float("nan"))
    down_up_ratio = div(down_vol, up_vol, 1.0)
    pb_days = daily_ret.tail(10) < 0
    pb_vol = sf(volume.tail(10)[pb_days].mean(), recent_vol)
    pb_shrink = div(pb_vol, recent_vol, 1.0)
    amount20 = mean_tail(amount, 20)
    volume_score = low_score(cv_ratio, 0.72, 1.18, 5.0) + high_score(flat_ratio, 0.55, 0.20, 4.0) + band_score(vol_level, 0.58, 1.42, 4.0, 0.45) + low_score(down_up_ratio, 0.85, 1.35, 4.0) + low_score(pb_shrink, 0.80, 1.16, 3.0)
    volume_score = clamp(volume_score, 0.0, 20.0)
    vol_ratio_prev = volume / volume.shift(1).replace(0, np.nan)
    bear_body = (close - open_) / open_.replace(0, np.nan)
    destructive = int(((bear_body <= -0.045) & (vol_ratio_prev >= 1.55) & (((high - low) / close.replace(0, np.nan)) >= 0.055)).tail(20).sum())
    long_upper = int((((high - np.maximum(open_, close)) / (high - low).replace(0, np.nan) >= 0.55) & (vol_ratio_prev >= 1.35)).tail(20).sum())
    pressure = sf(lines.get("pressure"), float("nan"))
    support = sf(lines.get("support"), float("nan"))
    trigger = sf(lines.get("trigger"), pressure)
    next_pressure = sf(lines.get("next_pressure"), float("nan"))
    pinfo = lines.get("pressure_info", {}) or {}
    sinfo = lines.get("support_info", {}) or {}
    trigger_dist = div(trigger - last, last, 9.99)
    defense_dist = div(last - support, last, 9.99) if support > 0 else 9.99
    rr = div(next_pressure - last, last - support, 0.0) if next_pressure > last and 0 < support < last else 0.0
    ret20 = ret(close, 20)
    structure_score = high_score(pinfo.get("resonance", 0), 5, 2, 6.0) + high_score(pinfo.get("weighted_resonance", 0), 7.5, 2.0, 5.0) + low_score(pinfo.get("cut", 99), 0, 5, 4.0) + band_score(trigger_dist, -MAX_TRIGGER_BROKEN, MAX_TRIGGER_ABOVE, 4.0, 0.06) + band_score(defense_dist, MIN_DEFENSE_DIST, MAX_DEFENSE_DIST, 3.5, 0.06) + high_score(sinfo.get("resonance", 0), 4, 1, 2.5)
    structure_score = clamp(structure_score, 0.0, 25.0)
    trade_score = high_score(rr, 2.2, MIN_RR, 6.0) + low_score(ret20, 0.10, MAX_RET20, 4.0) + low_score(destructive, 0, MAX_DESTRUCTIVE20 + 2, 4.0) + low_score(long_upper, 0, 4, 2.0) + high_score(amount20, max(MIN_AMOUNT20 * 2.0, 1.0), MIN_AMOUNT20, 4.0)
    trade_score = clamp(trade_score, 0.0, 20.0)
    trigger_score = band_score(trigger_dist, 0.000, 0.035, 4.0, 0.035) + high_score(div(last, float(high.tail(20).max()), 0.0), 0.965, 0.90, 3.0) + high_score(center_slope20, 0.0012, -0.0025, 3.0)
    trigger_score = clamp(trigger_score, 0.0, 10.0)
    score = clamp(price_score + volume_score + structure_score + trade_score + trigger_score, 0.0, 100.0)
    if is_hard_risk_name(name):
        reject.append("名称命中ST/退市风险")
    if last < MIN_CLOSE_PRICE:
        reject.append("股价过低")
    if amount20 > 0 and amount20 < MIN_AMOUNT20:
        reject.append("20日成交额不足")
    if len(d) < MIN_ROWS:
        reject.append("样本不足")
    if pinfo.get("resonance", 0) < MIN_LINE_RESONANCE:
        reject.append("核心触发线共振不足")
    if trigger_dist > MAX_TRIGGER_ABOVE:
        reject.append("离触发线太远")
    if trigger_dist < -MAX_TRIGGER_BROKEN:
        reject.append("已明显突破触发线，不再按藏锋前夜推送")
    if not (MIN_DEFENSE_DIST <= defense_dist <= MAX_DEFENSE_DIST):
        reject.append("真实防守距离不舒服")
    if rr < MIN_RR:
        reject.append("上方空间/RR不足")
    if width20 > MAX_WIDTH20 and width30 > MAX_WIDTH30:
        reject.append("平台过宽，不是压缩前夜")
    if close_std10 > MAX_CLOSE_STD10:
        reject.append("近10日波动未压缩")
    if ret20 > MAX_RET20:
        reject.append("近20日涨幅过热")
    if destructive > MAX_DESTRUCTIVE20:
        reject.append("近20日破坏性放量阴线偏多")
    if volume.tail(20).le(0).sum() > 0:
        reject.append("近20日存在零成交/停牌数据")
    if close_std10 <= 0.025 and flat_ratio >= 0.45:
        flags.append("价量同步压缩")
    if 0.0 <= trigger_dist <= 0.035:
        flags.append("贴近触发线")
    if MIN_DEFENSE_DIST <= defense_dist <= MAX_DEFENSE_DIST:
        flags.append("防守位距离合格")
    if rr >= 1.8:
        flags.append("RR尚可")
    if pinfo.get("volume_hits", 0) >= 1:
        flags.append("带量共振线")
    if ret20 > 0.12:
        flags.append("短线偏热")
    eligible = not reject and score >= MIN_SCORE
    if eligible and score >= 85:
        grade = "S级藏锋"
        bias = "临界观察：已进入爆发前夜，只等放量突破、站稳和RR复核。"
    elif eligible:
        grade = "A级藏锋"
        bias = "重点观察：压缩、触发线和防守位基本合格，等待确认，不提前追。"
    else:
        grade = "淘汰/观察"
        bias = "不推送：" + "；".join(reject[:5]) if reject else "观察：分数未达阈值。"
    dimensions = {"锋势": round(price_score, 2), "锋气": round(volume_score, 2), "锋骨": round(structure_score, 2), "交易约束": round(trade_score, 2), "出鞘准备": round(trigger_score, 2)}
    metrics = {"rows": int(len(d)), "last_close": round(last, 4), "amount20": round(amount20, 2), "range_contract_ratio": round(sf(range_ratio), 4), "atr_contract_ratio": round(sf(atr_ratio), 4), "body_contract_ratio": round(sf(body_ratio), 4), "close_std_pct_10d": round(sf(close_std10), 4), "low_slope_20d": round(sf(low_slope20), 6), "center_slope_20d": round(sf(center_slope20), 6), "volume_cv_contract_ratio": round(sf(cv_ratio), 4), "flat_volume_ratio_10d": round(sf(flat_ratio), 4), "vol_level_ratio": round(sf(vol_level), 4), "down_up_vol_ratio": round(sf(down_up_ratio), 4), "pullback_shrink_ratio": round(sf(pb_shrink), 4), "platform_width_20d": round(sf(width20), 4), "platform_width_30d": round(sf(width30), 4), "destructive_count_20d": destructive, "long_upper_count_20d": long_upper, "trigger_distance": round(sf(trigger_dist), 4), "defense_distance": round(sf(defense_dist), 4), "rr_ratio": round(sf(rr), 4), "return_20d": round(sf(ret20), 4), "pressure_line_quality": pinfo, "support_line_quality": sinfo}
    return {"indicator": "藏锋", "version": VERSION, "ok": True, "eligible": eligible, "score": round(score, 2), "grade": grade, "action_bias": bias, "dimensions": dimensions, "flags": list(dict.fromkeys(flags)), "reject_reasons": list(dict.fromkeys(reject)), "metrics": metrics}


def scan() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    files = iter_cache_files()
    refresh = refresh_stale_cache(files)
    files = iter_cache_files()
    rows: List[Dict[str, Any]] = []
    stat = {"boot": BOOT, "version": VERSION, "target_date": TARGET_DASH, "cache_files": len(files), "target_hit": 0, "stale_skip": 0, "scanned": 0, "bad": 0, "short": 0, "passed": 0, "rejected": 0, "refresh": refresh}
    start = time.time()
    print(f"藏锋启动 target={TARGET_DASH} cache_files={len(files)} min_score={MIN_SCORE}", flush=True)
    for i, path in enumerate(files, 1):
        code = code_of(path)
        df = read_cache_file(path)
        if df.empty:
            stat["bad"] += 1
            continue
        latest = ss(df.date.iloc[-1]) if "date" in df.columns and not df.empty else ""
        if TARGET_DASH and latest != TARGET_DASH:
            stat["stale_skip"] += 1
            continue
        stat["target_hit"] += 1
        if len(df) < 60:
            stat["short"] += 1
            continue
        stat["scanned"] += 1
        lines = estimate_lines(df)
        result = calculate_zangfeng(df, lines)
        if result.get("eligible"):
            stat["passed"] += 1
            last_row = df.iloc[-1]
            rows.append({"code": code, "name": ss(last_row.get("name", "")), "date": latest, "close": rd(last_row.get("close")), "score": result["score"], "grade": result["grade"], "action_bias": result["action_bias"], "dimensions": result["dimensions"], "flags": result["flags"], "reject_reasons": result["reject_reasons"], "metrics": result["metrics"], "pressure_price": rd(lines.get("pressure")), "support_price": rd(lines.get("support")), "trigger_price": rd(lines.get("trigger")), "next_pressure_price": rd(lines.get("next_pressure"))})
        else:
            stat["rejected"] += 1
        if PROGRESS_EVERY > 0 and i % PROGRESS_EVERY == 0:
            print(f"藏锋进度 {i}/{len(files)} target_hit={stat['target_hit']} scanned={stat['scanned']} passed={stat['passed']} stale={stat['stale_skip']} elapsed={fmt_seconds(time.time() - start)}", flush=True)
    rows.sort(key=lambda x: (x["score"], x["metrics"].get("rr_ratio", 0), x["metrics"].get("flat_volume_ratio_10d", 0), -x["metrics"].get("return_20d", 0)), reverse=True)
    return rows[:TOP_N], stat


def render_report(candidates: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    r = stat.get("refresh", {}) or {}
    lines = ["# 藏锋｜爆发前夜压缩指标", "", f"- 版本：{stat.get('version', VERSION)}", f"- 运行时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')} 北京时间", f"- 目标交易日：{stat.get('target_date') or '-'}", f"- 缓存文件：{stat.get('cache_files', 0)}", f"- 目标日命中：{stat.get('target_hit', 0)}", f"- 旧缓存跳过：{stat.get('stale_skip', 0)}", f"- 有效扫描：{stat.get('scanned', 0)}", f"- 硬过滤淘汰：{stat.get('rejected', 0)}", f"- 入选数量：{len(candidates)}", f"- 最低分：{MIN_SCORE}", f"- 数据补拉：attempt={r.get('attempt', 0)} success={r.get('success', 0)} failed={r.get('failed', 0)}", "", "口径：藏锋只推爆发前夜候选；已明显突破、远离防守位、成交额不足、平台过宽、近期过热或RR不足的票直接剔除。", ""]
    if not candidates:
        lines += ["## 今日无藏锋候选", "", "这比乱推错误股票更好。可能原因：目标日缓存覆盖不足，或全市场没有同时满足压缩、触发线、防守位、RR和流动性的标的。"]
        return "\n".join(lines) + "\n"
    lines += ["## Top 候选", "", "| 排名 | 代码 | 名称 | 日期 | 收盘 | 分数 | 等级 | 触发线 | 防守 | RR | 标签 |", "|---:|---|---|---|---:|---:|---|---:|---:|---:|---|"]
    for idx, item in enumerate(candidates, 1):
        flags = "、".join(item.get("flags", [])[:4])
        m = item.get("metrics", {})
        lines.append(f"| {idx} | {item['code']} | {item.get('name','')} | {item.get('date','')} | {item.get('close',0)} | {item['score']} | {item['grade']} | {item.get('trigger_price',0)} | {item.get('support_price',0)} | {m.get('rr_ratio',0)} | {flags} |")
    lines += ["", "## 明细"]
    for idx, item in enumerate(candidates, 1):
        d, m = item["dimensions"], item["metrics"]
        pinfo = m.get("pressure_line_quality", {}) or {}
        lines += ["", f"### {idx}. {item['code']} {item.get('name','')}", f"- 结论：{item['action_bias']}", f"- 分项：锋势{d['锋势']} / 锋气{d['锋气']} / 锋骨{d['锋骨']} / 交易约束{d['交易约束']} / 出鞘准备{d['出鞘准备']}", f"- 结构线：触发 {item.get('trigger_price')}，防守 {item.get('support_price')}，下一压力 {item.get('next_pressure_price')}，RR {m.get('rr_ratio')}", f"- 触发线质量：共振{pinfo.get('resonance')}，带量触碰{pinfo.get('volume_hits')}，切实体{pinfo.get('cut')}，加权共振{pinfo.get('weighted_resonance')}", f"- 压缩：ATR收缩 {m.get('atr_contract_ratio')}，振幅收缩 {m.get('range_contract_ratio')}，20日平台宽度 {m.get('platform_width_20d')}，10日波动 {m.get('close_std_pct_10d')}", f"- 量能：20日成交额 {round(m.get('amount20', 0) / 100000000, 2)}亿，平量比例 {m.get('flat_volume_ratio_10d')}，量能CV收缩 {m.get('volume_cv_contract_ratio')}，阴/阳量比 {m.get('down_up_vol_ratio')}", f"- 风险：20日涨幅 {round(m.get('return_20d', 0) * 100, 2)}%，破坏性放量阴线 {m.get('destructive_count_20d')}，长上影风险 {m.get('long_upper_count_20d')}", f"- 标签：{'、'.join(item.get('flags', []))}"]
    return "\n".join(lines) + "\n"


def render_telegram(report: str) -> str:
    text = report.strip()
    if len(text) <= REPORT_MAX_CHARS:
        return text + "\n"
    keep = max(500, REPORT_MAX_CHARS - 120)
    return text[:keep].rstrip() + "\n\n……\n报告已截断，完整内容看 artifacts/zangfeng_report.md\n"


def build_builtin_sample(n: int = 160, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = 10 + np.cumsum(rng.normal(0.010, 0.055, n))
    noise = np.concatenate([rng.normal(0, 0.18, max(0, n - 45)), rng.normal(0, 0.030, min(45, n))])[:n]
    close = np.maximum(base + noise, 1.0)
    close[-20:] = np.linspace(close[-21] * 0.985, close[-21] * 1.018, 20) + rng.normal(0, 0.012, 20)
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.006, 0.0025, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.006, 0.0025, n)))
    volume = np.maximum(np.concatenate([rng.normal(1500000, 360000, max(0, n - 45)), rng.normal(1320000, 80000, min(45, n))])[:n], 10000)
    amount = volume * close * 100
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": volume, "amount": amount, "name": "自检样本"})


def self_check() -> Dict[str, Any]:
    good_df = build_builtin_sample()
    lines = estimate_lines(good_df)
    last = float(good_df.close.iloc[-1])
    lines["pressure"] = round(last * 1.025, 4)
    lines["trigger"] = round(last * 1.025, 4)
    lines["support"] = round(last * 0.94, 4)
    lines["next_pressure"] = round(last * 1.20, 4)
    lines["pressure_info"] = {"line": lines["pressure"], "score": 8.0, "resonance": 5, "weighted_resonance": 7.0, "cut": 0, "volume_hits": 1, "distance": 0.025}
    lines["support_info"] = {"line": lines["support"], "score": 5.0, "resonance": 4, "weighted_resonance": 5.0, "cut": 0, "volume_hits": 0, "distance": 0.06}
    good = calculate_zangfeng(good_df, lines)
    rng = np.random.default_rng(17)
    n = 160
    close = np.maximum(5 + np.cumsum(rng.normal(0.0, 0.45, n)), 1.0)
    open_ = close * (1 + rng.normal(0, 0.035, n))
    bad = calculate_zangfeng(pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="B"), "open": open_, "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0.05, 0.025, n))), "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0.05, 0.025, n))), "close": close, "volume": rng.lognormal(mean=14.0, sigma=0.75, size=n), "amount": rng.lognormal(mean=18.0, sigma=0.75, size=n)}))
    short = calculate_zangfeng(good_df.tail(12))
    checks = {"good_score_positive": good["score"] >= 45, "bad_score_lower_than_good": bad["score"] <= good["score"], "short_sample_rejected": short["ok"] is False and short["grade"] == "数据不足", "score_range_good": 0 <= good["score"] <= 100, "score_range_bad": 0 <= bad["score"] <= 100, "required_keys_present": all(k in good for k in ["score", "grade", "dimensions", "metrics", "flags", "action_bias", "reject_reasons"]), "root_script_version": VERSION.startswith("藏锋-v3")}
    return {"ok": all(checks.values()), "checks": checks, "good_score": good["score"], "good_grade": good["grade"], "bad_score": bad["score"], "bad_grade": bad["grade"], "short_grade": short["grade"]}


def run_self_check(rounds: int) -> Dict[str, Any]:
    results = []
    for i in range(1, max(1, rounds) + 1):
        item = self_check()
        item["round"] = i
        results.append(item)
        print(f"藏锋自检 {i}/{rounds}: ok={item.get('ok')} good={item.get('good_score')} bad={item.get('bad_score')}", flush=True)
    return {"ok": all(bool(x.get("ok")) for x in results), "rounds": max(1, rounds), "results": results}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="藏锋爆发前夜压缩指标")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-rounds", type=int, default=7)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    global MAX_STOCKS
    if args.limit is not None and args.limit >= 0:
        MAX_STOCKS = args.limit
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    check = run_self_check(args.self_test_rounds)
    SELF_CHECK_JSON.write_text(json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8")
    if not check.get("ok"):
        raise SystemExit("藏锋指标自检失败")
    if args.self_test and not args.scan:
        print(json.dumps(check, ensure_ascii=False, indent=2), flush=True)
        return 0
    candidates, stat = scan()
    report = render_report(candidates, stat)
    OUTPUT_MD.write_text(report, encoding="utf-8")
    OUTPUT_TG.write_text(render_telegram(report), encoding="utf-8")
    OUTPUT_JSON.write_text(json.dumps({"stat": stat, "candidates": candidates}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report, flush=True)
    print(f"藏锋报告已生成：{OUTPUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
